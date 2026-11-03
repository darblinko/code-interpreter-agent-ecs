#!/usr/bin/env python3
"""Print an itemized monthly cost estimate for the chosen CDK context. Estimate only.

Rates are us-east-1 list prices as recorded in docs/cost.md. Verify them at
https://aws.amazon.com/pricing/ before relying on the result. Usage:
    estimate_cost.py [key=value ...]
"""
import json
import sys
from pathlib import Path

HOURS = 730
FARGATE = {"arm64": (0.03238, 0.00356), "x86_64": (0.04048, 0.004445)}  # per vCPU-h, per GB-h
ALB_HOUR, ALB_LCU_HOUR, ASSUMED_LCU = 0.0225, 0.008, 1.0
NAT_HOUR, NAT_GB, ASSUMED_NAT_GB = 0.045, 0.045, 5
PUBLIC_IPV4_HOUR = 0.005
SECRET_MONTH, ALARM_MONTH, LOG_GB, ASSUMED_LOG_GB, ECR_GB_MONTH, ASSUMED_ECR_GB = 0.40, 0.10, 0.50, 1.0, 0.10, 0.5


def load_context(overrides: list[str]) -> dict:
    cdk_json = Path(__file__).resolve().parents[1] / "infra" / "cdk.json"
    context = json.loads(cdk_json.read_text())["context"]
    for pair in overrides:
        key, _, value = pair.partition("=")
        context[key] = value
    return context


def is_true(value) -> bool:
    return str(value).lower() in ("true", "1", "yes")


def estimate(context: dict) -> list[tuple[str, float]]:
    vcpu_rate, gb_rate = FARGATE[str(context["cpu_arch"])]
    tasks = int(context["desired_count"])
    vcpu, gb = int(context["cpu"]) / 1024, int(context["memory_mib"]) / 1024
    items = [(f"Fargate {tasks} task(s), {vcpu} vCPU, {gb} GB", tasks * (vcpu * vcpu_rate + gb * gb_rate) * HOURS)]
    public = is_true(context["public_https"])
    items.append(("Application Load Balancer (hours + 1 LCU assumed)", (ALB_HOUR + ALB_LCU_HOUR * ASSUMED_LCU) * HOURS))
    if public:
        items.append(("Public IPv4 for internet-facing ALB (2 AZs)", 2 * PUBLIC_IPV4_HOUR * HOURS))
    if context["egress_mode"] == "nat":
        items.append((f"NAT gateway (hours + {ASSUMED_NAT_GB} GB processed)", NAT_HOUR * HOURS + NAT_GB * ASSUMED_NAT_GB))
        items.append(("Public IPv4 on NAT gateway", PUBLIC_IPV4_HOUR * HOURS))
    else:
        items.append(("Public IPv4 on task(s)", tasks * PUBLIC_IPV4_HOUR * HOURS))
    items += [
        ("Secrets Manager (1 secret)", SECRET_MONTH),
        (f"CloudWatch Logs ({ASSUMED_LOG_GB} GB ingested)", LOG_GB * ASSUMED_LOG_GB),
        ("CloudWatch alarms (2)", 2 * ALARM_MONTH),
        (f"ECR storage ({ASSUMED_ECR_GB} GB)", ECR_GB_MONTH * ASSUMED_ECR_GB),
    ]
    return items


def main() -> None:
    context = load_context(sys.argv[1:])
    items = estimate(context)
    print("Estimated monthly cost in us-east-1 (ESTIMATE, excludes model API usage and data transfer out):")
    for label, amount in items:
        print(f"  {label:<58} ${amount:8.2f}")
    print(f"  {'Total':<58} ${sum(a for _, a in items):8.2f}")
    if is_true(context["autoscaling"]):
        print("  Autoscaling is on: cost scales with task count up to max_count.")
    print(f"  AWS Budget alert threshold: ${float(context['monthly_budget_usd']):.2f} per month (account level).")


if __name__ == "__main__":
    main()
