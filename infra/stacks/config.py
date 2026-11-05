"""Typed view of the CDK context. Values from `-c key=value` arrive as strings."""
import ipaddress
from dataclasses import dataclass

from constructs import Construct


def _as_bool(value) -> bool:
    return str(value).strip().lower() in ("true", "1", "yes")


@dataclass(frozen=True)
class Config:
    egress_mode: str
    cpu_arch: str
    cpu: int
    memory_mib: int
    desired_count: int
    autoscaling: bool
    max_count: int
    openai_enabled: bool
    api_token_enabled: bool
    public_https: bool
    certificate_arn: str
    public_ingress_cidr: str
    destroy_on_delete: bool
    monthly_budget_usd: float
    notification_email: str

    @classmethod
    def from_scope(cls, scope: Construct) -> "Config":
        def get(key, default=None):
            value = scope.node.try_get_context(key)
            return default if value is None else value

        config = cls(
            egress_mode=str(get("egress_mode", "nat")),
            cpu_arch=str(get("cpu_arch", "arm64")),
            cpu=int(get("cpu", 512)),
            memory_mib=int(get("memory_mib", 1024)),
            desired_count=int(get("desired_count", 1)),
            autoscaling=_as_bool(get("autoscaling", False)),
            max_count=int(get("max_count", 2)),
            openai_enabled=_as_bool(get("openai_enabled", False)),
            api_token_enabled=_as_bool(get("api_token_enabled", False)),
            public_https=_as_bool(get("public_https", False)),
            certificate_arn=str(get("certificate_arn", "")),
            public_ingress_cidr=str(get("public_ingress_cidr", "")),
            destroy_on_delete=_as_bool(get("destroy_on_delete", False)),
            monthly_budget_usd=float(get("monthly_budget_usd", 50)),
            notification_email=str(get("notification_email", "")),
        )
        config.validate()
        return config

    def validate(self) -> None:
        if self.egress_mode not in ("nat", "public_ip"):
            raise ValueError("egress_mode must be 'nat' or 'public_ip'")
        if self.cpu_arch not in ("arm64", "x86_64"):
            raise ValueError("cpu_arch must be 'arm64' or 'x86_64'")
        if self.public_https:
            self._validate_public_https()

    def _validate_public_https(self) -> None:
        if not self.certificate_arn.startswith("arn:") or ":acm:" not in self.certificate_arn:
            raise ValueError("public_https=true requires certificate_arn (an ACM certificate ARN)")
        if not _is_restricted_ipv4_network(self.public_ingress_cidr):
            raise ValueError(
                "public_https=true requires public_ingress_cidr set to a restricted IPv4 network "
                "in canonical form (for example your office /32). Any /0 is refused."
            )


def _is_restricted_ipv4_network(cidr: str) -> bool:
    """True for a canonical IPv4 network with a prefix of at least /1. AWS canonicalizes
    1.2.3.4/0 to 0.0.0.0/0, so host bits are rejected rather than masked."""
    try:
        network = ipaddress.IPv4Network(cidr, strict=True)
    except ValueError:
        return False
    return "/" in cidr and network.prefixlen > 0
