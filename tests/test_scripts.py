import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import estimate_cost  # noqa: E402


@pytest.mark.parametrize("name", ["preflight.sh", "deploy.sh", "destroy.sh", "smoke.sh", "_common.sh"])
def test_shell_syntax(name):
    subprocess.run(["bash", "-n", str(SCRIPTS / name)], check=True)


def test_preflight_never_deploys():
    text = (SCRIPTS / "preflight.sh").read_text()
    for forbidden in ("cdk deploy", "run_cdk", "put-secret-value", "create-stack", "delete-stack"):
        assert forbidden not in text


def test_deploy_requires_typed_stack_name():
    text = (SCRIPTS / "deploy.sh").read_text()
    assert '[ "$typed" = "$stack" ]' in text
    assert text.index("preflight.sh") < text.index("run_cdk deploy")


def test_default_estimate_exceeds_public_ip_estimate():
    context = estimate_cost.load_context([])
    nat_total = sum(a for _, a in estimate_cost.estimate(context))
    context["egress_mode"] = "public_ip"
    public_ip_total = sum(a for _, a in estimate_cost.estimate(context))
    assert 70 < nat_total < 80
    assert public_ip_total < nat_total - 30


def test_default_budget_exceeds_default_estimate():
    context = estimate_cost.load_context([])
    assert float(context["monthly_budget_usd"]) > sum(a for _, a in estimate_cost.estimate(context))


def test_destroy_finds_retained_resources_by_name_after_stack_deletion():
    """A second run with --purge must find what a first run without it left behind, when the
    stacks no longer exist."""
    text = (SCRIPTS / "destroy.sh").read_text()
    body = text[text.index('delete_stack "$SERVICE_STACK"') :]
    assert "--log-group-name-prefix" in text and "describe-stack-resources" not in text
    assert "retained_log_groups" in body and "secret_exists" in body
    assert "get-template" not in text
