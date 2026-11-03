# Deploy walkthrough

For a first deployment to AWS. Expect 45 to 60 minutes the first time, mostly account setup. Cost while running is about $75 per month on defaults (`docs/cost.md`), billed per hour from creation until you destroy.

## 1. Create an AWS account

1. Create an account at https://aws.amazon.com/ and enable MFA on the root user.
2. Do not use the root user for the steps below.
3. If this is a shared or production account, create a separate account for this demo (AWS Organizations) instead.

## 2. Create an IAM identity for deployment

CDK creates IAM roles and many resource types, so the deploying identity needs broad permissions. For a personal sandbox account:

1. In the console, open IAM, then Users, then Create user. Name it `cdk-deployer`.
2. Attach the AWS managed policy `AdministratorAccess`. Use IAM Identity Center with a short session instead if your organization provides it.
3. Create an access key for the CLI (use case: Command Line Interface). Store it in a password manager.

## 3. Install the tools and configure credentials

Required: AWS CLI v2, Node 18 or later, Python 3.11 or later, Docker (running).

```
aws configure            # access key, secret, default region (for example us-east-1)
aws sts get-caller-identity
```

The second command prints the account id. Confirm it is the account you intend to use.

If your computer is not ARM-based and you keep the default `cpu_arch=arm64`, enable emulation once:

```
docker run --privileged --rm tonistiigi/binfmt --install arm64
```

## 4. Run the preflight

```
scripts/preflight.sh
```

It checks the AWS CLI, credentials (shows the account id), region, CDK bootstrap, Docker and the secret. It deploys nothing. Expect one failure at this point: CDK is not bootstrapped.

## 5. Bootstrap CDK (once per account and region)

Bootstrap creates a bucket, an ECR repository and IAM roles that CDK uses. It is not part of the scripts, so you run it yourself:

```
cd infra
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
npx aws-cdk@2.1147.0 bootstrap --app ".venv/bin/python app.py"
cd ..
scripts/preflight.sh      # bootstrap check should now pass
```

## 6. Create the secret, then fill it

```
scripts/deploy.sh secrets
```

Read the summary, type `CodeInterpreterAgentSecrets` to proceed. The stack creates an empty secret. Store your key without putting it in shell history:

```
read -r -s -p "Anthropic key: " K; echo
aws secretsmanager put-secret-value --secret-id code-interpreter-agent/api-keys \
  --secret-string "{\"ANTHROPIC_API_KEY\":\"$K\"}"
unset K
```

For OpenAI as well, include `"OPENAI_API_KEY"` in the JSON and deploy with `openai_enabled=true`.

## 7. Deploy the service

```
scripts/deploy.sh service
```

Optional overrides: `egress_mode=public_ip` (cheaper), `notification_email=you@example.com` (budget and alarm email), `cpu_arch=x86_64`. The script prints the summary and the monthly estimate, then waits for you to type `CodeInterpreterAgentService`. The first deployment builds the image locally and takes roughly 10 to 15 minutes.

If the deployment fails, CloudFormation rolls back to the previous state. Check the ECS service events and the CloudWatch log group named in the stack resources.

## 8. Test

The ALB is internal, so it answers only to callers inside the VPC. Options:

1. AWS CloudShell in the VPC: in the console, open CloudShell, choose Actions, Create VPC environment, select the stack's VPC and a private subnet, then run `BASE_URL=<BaseUrl output> scripts/smoke.sh` after cloning this repository there. The ALB security group allows port 80 from the whole VPC CIDR.
2. A bastion or an existing instance in the VPC.
3. Public mode for a short test: redeploy with `public_https=true certificate_arn=<ACM arn> public_ingress_cidr=<your ip>/32`, and point a DNS name matching the certificate at the ALB. Remove it afterwards.

The smoke script calls `GET /health` and one `POST /v1/run`. The body is configurable with `SMOKE_BODY` to match the application's request schema.

## 9. Destroy

```
scripts/destroy.sh --purge
```

Type each stack name when asked. `--purge` also removes the retained secret (without the recovery window) and log group. Without `--purge` they remain and keep their names, which blocks redeploying the secrets stack until you delete the secret. Alternatively deploy with `destroy_on_delete=true` so the stacks remove them.

Not removed: the CDK bootstrap stack `CDKToolkit` and image assets in its ECR repository. They cost cents per month. Delete the stack and empty the bucket and repository manually if you want a clean account.

Check the AWS Billing console the next day for any remaining NAT gateway, load balancer or public IPv4 charges.
