#!/usr/bin/env python3
"""CDK entry point. Context defaults live in cdk.json; override with -c key=value."""
import os

import aws_cdk as cdk

from stacks.secrets_stack import SECRETS_STACK_NAME, SecretsStack
from stacks.service_stack import SERVICE_STACK_NAME, ServiceStack


def build_app(context: dict | None = None) -> cdk.App:
    app = cdk.App(context=context)
    env = cdk.Environment(
        account=os.environ.get("CDK_DEFAULT_ACCOUNT"),
        region=os.environ.get("CDK_DEFAULT_REGION"),
    )
    secrets = SecretsStack(app, SECRETS_STACK_NAME, env=env)
    ServiceStack(app, SERVICE_STACK_NAME, secret=secrets.secret, env=env)
    return app


if __name__ == "__main__":
    build_app().synth()
