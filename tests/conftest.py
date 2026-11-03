import json
import sys
from pathlib import Path

import pytest
from aws_cdk.assertions import Template

INFRA_DIR = Path(__file__).resolve().parents[1] / "infra"
sys.path.insert(0, str(INFRA_DIR))

from app import build_app  # noqa: E402


def cdk_json_context() -> dict:
    return json.loads((INFRA_DIR / "cdk.json").read_text())["context"]


@pytest.fixture
def synth():
    """Return a function that synthesizes both stacks with the cdk.json defaults plus overrides."""

    def _synth(**overrides):
        app = build_app({**cdk_json_context(), **overrides})
        assembly = app.synth()
        return {
            stack.stack_name: Template.from_json(stack.template) for stack in assembly.stacks
        }

    return _synth


@pytest.fixture
def service(synth):
    return synth()["CodeInterpreterAgentService"]


@pytest.fixture
def secrets(synth):
    return synth()["CodeInterpreterAgentSecrets"]
