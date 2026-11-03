# Cost estimate

This is an estimate, not a quote. Rates are us-east-1 on-demand list prices as recorded when this document was written. They were not re-fetched from the AWS price list during the build of this repository. Verify before relying on them.

Run `python3 scripts/estimate_cost.py [key=value ...]` to recompute for other settings. The script and this table use the same rates.

## Default configuration

1 task, 0.5 vCPU, 1 GB, ARM64, NAT egress, internal ALB, 730 hours per month.

| Item | Basis | Monthly USD |
|---|---|---|
| Fargate | 0.5 vCPU x $0.03238 + 1 GB x $0.00356, per hour | 14.42 |
| Application Load Balancer | $0.0225 per hour + 1 LCU assumed at $0.008 per hour | 22.27 |
| NAT gateway | $0.045 per hour + $0.045 per GB, 5 GB assumed | 33.08 |
| Public IPv4 on the NAT gateway | $0.005 per hour | 3.65 |
| Secrets Manager | $0.40 per secret | 0.40 |
| CloudWatch Logs | $0.50 per GB ingested, 1 GB assumed | 0.50 |
| CloudWatch alarms | 2 standard alarms at $0.10 | 0.20 |
| ECR storage | $0.10 per GB-month, 0.5 GB assumed | 0.05 |
| **Total** | | **74.56** |

## Variants

| Change | Effect on the total |
|---|---|
| `egress_mode=public_ip` | removes the NAT rows (36.73) and adds a public IPv4 per task (3.65); total 41.48 |
| `cpu_arch=x86_64` | Fargate rises from 14.42 to about 18.02 |
| `public_https=true` | adds 2 public IPv4 addresses for the ALB (7.30) |
| `desired_count=2` | Fargate doubles |
| Stopped (`desired_count=0`) | Fargate stops; ALB, NAT and the rest continue to bill |

## Not included

- Model API usage (Anthropic, OpenAI). This depends on the workload and is the largest variable cost for an active agent.
- Data transfer out to the internet, beyond the 5 GB NAT assumption.
- Route 53, ACM (public certificates are free), WAF.
- Taxes and credits.
- CloudWatch Logs storage beyond the first month and queries.

## Assumptions to check

1. 730 hours per month of continuous operation. A demo run for 4 hours costs a small fraction, but the NAT gateway and ALB bill from creation until deletion.
2. 1 LCU for the ALB. Actual LCU use for low traffic is below 1; the estimate is conservative.
3. Interface VPC endpoints are not used. Each would cost about $0.01 per hour per AZ and does not replace the NAT for model API traffic. The S3 gateway endpoint is free.
4. The CDK bootstrap bucket and ECR repository are shared across stacks in the account. Each deployed image version remains in ECR until removed.
5. AWS Budgets: the first two budgets per account are free. The budget created here covers the whole account, not only this stack.

## Pricing sources

- Fargate: https://aws.amazon.com/fargate/pricing/
- Elastic Load Balancing: https://aws.amazon.com/elasticloadbalancing/pricing/
- NAT gateway and public IPv4: https://aws.amazon.com/vpc/pricing/
- Secrets Manager: https://aws.amazon.com/secrets-manager/pricing/
- CloudWatch: https://aws.amazon.com/cloudwatch/pricing/
- ECR: https://aws.amazon.com/ecr/pricing/

## Controls

- `monthly_budget_usd` with `notification_email` creates an AWS Budget alerting at 80 percent of actual spend and 100 percent of forecast spend.
- Run `scripts/destroy.sh --purge` when finished. Billing stops for deleted resources.
