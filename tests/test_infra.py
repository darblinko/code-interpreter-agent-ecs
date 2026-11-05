import json

import pytest
from aws_cdk.assertions import Match
from conftest import cdk_json_context

PUBLIC = {
    "public_https": True,
    "certificate_arn": "arn:aws:acm:us-east-1:111111111111:certificate/00000000-0000-0000-0000-000000000000",
    "public_ingress_cidr": "203.0.113.7/32",
}
# Default Linux capabilities of a Fargate container, from the ECS KernelCapabilities API reference.
FARGATE_DEFAULT_CAPABILITIES = {
    "AUDIT_WRITE", "CHOWN", "DAC_OVERRIDE", "FOWNER", "FSETID", "KILL", "MKNOD",
    "NET_BIND_SERVICE", "NET_RAW", "SETFCAP", "SETGID", "SETPCAP", "SETUID", "SYS_CHROOT",
}  # fmt: skip
# ecr:GetAuthorizationToken does not support resource-level permissions.
WILDCARD_ALLOWED_ACTIONS = {"ecr:GetAuthorizationToken"}


def as_list(value):
    return value if isinstance(value, list) else [value]


def policy_statements(template):
    statements = []
    for resource in template.find_resources("AWS::IAM::Policy").values():
        statements += as_list(resource["Properties"]["PolicyDocument"]["Statement"])
    for resource in template.find_resources("AWS::IAM::ManagedPolicy").values():
        statements += as_list(resource["Properties"]["PolicyDocument"]["Statement"])
    for resource in template.find_resources("AWS::IAM::Role").values():
        for inline in resource["Properties"].get("Policies", []):
            statements += as_list(inline["PolicyDocument"]["Statement"])
    return statements


def ingress_cidrs(template):
    cidrs = []
    groups = template.find_resources("AWS::EC2::SecurityGroup").values()
    rules = [r for g in groups for r in g["Properties"].get("SecurityGroupIngress", [])]
    rules += [r["Properties"] for r in template.find_resources("AWS::EC2::SecurityGroupIngress").values()]
    for rule in rules:
        cidrs.append(rule.get("CidrIp"))
    return [c for c in cidrs if c]


def container(template):
    task = next(iter(template.find_resources("AWS::ECS::TaskDefinition").values()))
    return task["Properties"]["ContainerDefinitions"][0]


def test_no_wildcard_iam_resources(service, secrets):
    for template in (service, secrets):
        for statement in policy_statements(template):
            if statement.get("Effect") != "Allow":
                continue
            actions = set(as_list(statement["Action"]))
            assert "*" not in actions and not any(a.endswith(":*") for a in actions), statement
            if "*" in as_list(statement["Resource"]):
                assert actions <= WILDCARD_ALLOWED_ACTIONS, statement


def test_task_role_has_no_permissions(service):
    roles = service.find_resources(
        "AWS::IAM::Role",
        {"Properties": {"Description": "Intentionally has no policies"}},
    )
    assert len(roles) == 1
    task_role_id = next(iter(roles))
    for policy in service.find_resources("AWS::IAM::Policy").values():
        refs = json.dumps(policy["Properties"]["Roles"])
        assert task_role_id not in refs
    assert "Policies" not in next(iter(roles.values()))["Properties"]


def test_no_public_ingress_by_default(service):
    assert "0.0.0.0/0" not in ingress_cidrs(service)
    service.has_resource_properties(
        "AWS::ElasticLoadBalancingV2::LoadBalancer", {"Scheme": "internal"}
    )


def test_service_ingress_only_from_alb(service):
    groups = service.find_resources("AWS::EC2::SecurityGroup")
    service_sg = next(g for name, g in groups.items() if name.startswith("ServiceSg"))
    assert not service_sg["Properties"].get("SecurityGroupIngress")
    ingress = [
        r["Properties"]
        for r in service.find_resources("AWS::EC2::SecurityGroupIngress").values()
        if "ServiceSg" in json.dumps(r["Properties"]["GroupId"])
    ]
    assert len(ingress) == 1
    assert "AlbSg" in json.dumps(ingress[0]["SourceSecurityGroupId"])
    assert ingress[0]["FromPort"] == 8080


def test_public_mode_requires_certificate_and_restricted_cidr(synth):
    with pytest.raises(Exception, match="certificate_arn"):
        synth(public_https=True)
    for cidr in ("0.0.0.0/0", "1.2.3.4/0", "203.0.113.7/24", "203.0.113.7", "::/0", "", "x/32"):
        with pytest.raises(Exception, match="public_ingress_cidr"):
            synth(**{**PUBLIC, "public_ingress_cidr": cidr})


def test_public_mode_with_certificate(synth):
    template = synth(**PUBLIC)["CodeInterpreterAgentService"]
    template.has_resource_properties(
        "AWS::ElasticLoadBalancingV2::LoadBalancer", {"Scheme": "internet-facing"}
    )
    template.has_resource_properties("AWS::ElasticLoadBalancingV2::Listener", {"Port": 443})
    assert set(ingress_cidrs(template)) == {"203.0.113.7/32"}


def test_secret_is_empty_and_not_literal(secrets, service):
    resource = next(iter(secrets.find_resources("AWS::SecretsManager::Secret").values()))
    assert "SecretString" not in resource["Properties"]
    assert "GenerateSecretString" not in resource["Properties"]
    env = {e["Name"] for e in container(service).get("Environment", [])}
    assert not {"ANTHROPIC_API_KEY", "OPENAI_API_KEY"} & env
    secret_names = {s["Name"]: s for s in container(service)["Secrets"]}
    assert set(secret_names) == {"ANTHROPIC_API_KEY"}
    assert "ValueFrom" in secret_names["ANTHROPIC_API_KEY"]
    assert "sk-" not in json.dumps(service.to_json()) + json.dumps(secrets.to_json())


def test_openai_key_injected_only_when_enabled(synth):
    template = synth(openai_enabled=True)["CodeInterpreterAgentService"]
    names = {s["Name"] for s in container(template)["Secrets"]}
    assert names == {"ANTHROPIC_API_KEY", "OPENAI_API_KEY"}


def test_secret_access_is_scoped_to_one_arn(service):
    reads = [
        s
        for s in policy_statements(service)
        if "secretsmanager:GetSecretValue" in as_list(s["Action"])
    ]
    assert len(reads) == 1
    assert reads[0]["Resource"] != "*"


def test_log_retention_14_days(service):
    service.has_resource_properties("AWS::Logs::LogGroup", {"RetentionInDays": 14})


def test_readonly_root_filesystem_and_writable_volumes(service):
    definition = container(service)
    assert definition["ReadonlyRootFilesystem"] is True
    mounts = {m["ContainerPath"]: m for m in definition["MountPoints"]}
    assert set(mounts) == {"/workspace", "/tmp"}
    assert not any(m.get("ReadOnly") for m in mounts.values())
    capabilities = definition["LinuxParameters"]["Capabilities"]
    # Fargate accepts only SYS_PTRACE in Add, so nothing is added. Dropping the rest of the
    # Fargate default set leaves exactly SETUID, SETGID and KILL.
    assert "Add" not in capabilities
    assert FARGATE_DEFAULT_CAPABILITIES - set(capabilities["Drop"]) == {"KILL", "SETGID", "SETUID"}
    assert set(capabilities["Drop"]) <= FARGATE_DEFAULT_CAPABILITIES
    assert definition["PortMappings"][0]["ContainerPort"] == 8080


def test_default_has_nat_and_arm64(service):
    service.resource_count_is("AWS::EC2::NatGateway", 1)
    service.has_resource_properties(
        "AWS::ECS::TaskDefinition", {"RuntimePlatform": {"CpuArchitecture": "ARM64"}}
    )
    service.has_resource_properties(
        "AWS::ECS::Service",
        {"NetworkConfiguration": {"AwsvpcConfiguration": {"AssignPublicIp": "DISABLED"}}},
    )


def test_public_ip_egress_mode_has_no_nat(synth):
    template = synth(egress_mode="public_ip")["CodeInterpreterAgentService"]
    template.resource_count_is("AWS::EC2::NatGateway", 0)
    template.has_resource_properties(
        "AWS::ECS::Service",
        {"NetworkConfiguration": {"AwsvpcConfiguration": {"AssignPublicIp": "ENABLED"}}},
    )
    assert "0.0.0.0/0" not in ingress_cidrs(template)


def test_autoscaling_disabled_by_default(service, synth):
    service.resource_count_is("AWS::ApplicationAutoScaling::ScalableTarget", 0)
    enabled = synth(autoscaling=True)["CodeInterpreterAgentService"]
    enabled.resource_count_is("AWS::ApplicationAutoScaling::ScalableTarget", 1)


def test_alarms_and_budget(service, synth):
    service.resource_count_is("AWS::CloudWatch::Alarm", 2)
    service.resource_count_is("AWS::Budgets::Budget", 0)
    template = synth(notification_email="ops@example.com", monthly_budget_usd=25)[
        "CodeInterpreterAgentService"
    ]
    template.has_resource_properties(
        "AWS::Budgets::Budget",
        {"Budget": Match.object_like({"TimeUnit": "MONTHLY", "BudgetLimit": {"Amount": 25, "Unit": "USD"}})},
    )


def test_removal_policy_follows_destroy_flag(synth):
    kept = synth()
    destroyed = synth(destroy_on_delete="true")
    for name, resource_type in (
        ("CodeInterpreterAgentSecrets", "AWS::SecretsManager::Secret"),
        ("CodeInterpreterAgentService", "AWS::Logs::LogGroup"),
    ):
        kept_policy = next(iter(kept[name].find_resources(resource_type).values()))
        gone_policy = next(iter(destroyed[name].find_resources(resource_type).values()))
        assert kept_policy["DeletionPolicy"] == "Retain"
        assert gone_policy["DeletionPolicy"] == "Delete"


def test_cdk_json_defaults_are_cheapest_safe():
    context = cdk_json_context()
    assert context["public_https"] is False
    assert context["autoscaling"] is False
    assert context["destroy_on_delete"] is False
    assert context["desired_count"] == 1
    assert (context["cpu"], context["memory_mib"]) == (512, 1024)
    assert context["cpu_arch"] == "arm64"


def test_api_token_injected_only_when_enabled(synth):
    template = synth(api_token_enabled=True)["CodeInterpreterAgentService"]
    assert "SERVICE_API_TOKEN" in {s["Name"] for s in container(template)["Secrets"]}
    environment = {e["Name"]: e["Value"] for e in container(template)["Environment"]}
    assert environment["AUTH_REQUIRED"] == "true"
    default = synth()["CodeInterpreterAgentService"]
    assert "SERVICE_API_TOKEN" not in {s["Name"] for s in container(default)["Secrets"]}
    assert "AUTH_REQUIRED" not in {e["Name"] for e in container(default)["Environment"]}


def test_forwarded_for_trusted_only_from_the_vpc(service):
    environment = {e["Name"]: e["Value"] for e in container(service)["Environment"]}
    value = environment["TRUSTED_PROXY_CIDRS"]["Fn::GetAtt"]
    assert value[0].startswith("Vpc") and value[1] == "CidrBlock"


def test_task_does_not_force_a_user_or_extra_privileges(service):
    definition = container(service)
    assert definition.get("Privileged") in (None, False)
    assert "User" not in definition
    assert "SYS_ADMIN" not in str(definition["LinuxParameters"])


@pytest.mark.parametrize(("memory_mib", "sessions"), [(1024, "3"), (2048, "7")])
def test_session_count_fits_the_task_memory(synth, memory_mib, sessions):
    template = synth(memory_mib=memory_mib, cpu=1024)["CodeInterpreterAgentService"]
    environment = {e["Name"]: e["Value"] for e in container(template)["Environment"]}
    assert environment["TASK_MEMORY_MIB"] == str(memory_mib)
    assert environment["EXEC_MEMORY_MB"] == "256"
    assert environment["MAX_SESSIONS"] == sessions
