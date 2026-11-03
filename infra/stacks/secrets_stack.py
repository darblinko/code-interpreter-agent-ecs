"""Stack 1: an empty Secrets Manager secret. The user fills it before stack 2 is deployed.

A separate stack lets the service stack be deployed only after the keys exist, so the
first deployment does not loop on tasks that cannot start.
"""
import aws_cdk as cdk
from aws_cdk import aws_secretsmanager as secretsmanager
from constructs import Construct

from .config import Config

SECRETS_STACK_NAME = "CodeInterpreterAgentSecrets"
SECRET_NAME = "code-interpreter-agent/api-keys"


class SecretsStack(cdk.Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)
        config = Config.from_scope(self)

        # CfnSecret with no SecretString creates an empty secret. The higher level
        # Secret construct would generate a random value, which is not wanted here.
        cfn_secret = secretsmanager.CfnSecret(
            self,
            "ApiKeys",
            name=SECRET_NAME,
            description=(
                "JSON object with keys ANTHROPIC_API_KEY and optionally OPENAI_API_KEY. "
                "Created empty; fill it with put-secret-value."
            ),
        )
        cfn_secret.apply_removal_policy(
            cdk.RemovalPolicy.DESTROY if config.destroy_on_delete else cdk.RemovalPolicy.RETAIN
        )
        self.secret = secretsmanager.Secret.from_secret_complete_arn(
            self, "ApiKeysRef", cfn_secret.ref
        )
        cdk.CfnOutput(self, "SecretName", value=SECRET_NAME)
        cdk.CfnOutput(self, "SecretArn", value=cfn_secret.ref)
