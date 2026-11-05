"""Stack 2: VPC, ECS Fargate service, ALB, logs, alarms, budget."""
from pathlib import Path

import aws_cdk as cdk
from aws_cdk import aws_budgets as budgets
from aws_cdk import aws_cloudwatch as cloudwatch
from aws_cdk import aws_cloudwatch_actions as cw_actions
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_ecr_assets as ecr_assets
from aws_cdk import aws_ecs as ecs
from aws_cdk import aws_elasticloadbalancingv2 as elbv2
from aws_cdk import aws_iam as iam
from aws_cdk import aws_logs as logs
from aws_cdk import aws_secretsmanager as secretsmanager
from aws_cdk import aws_sns as sns
from aws_cdk import aws_sns_subscriptions as subscriptions
from constructs import Construct

from .config import Config

SERVICE_STACK_NAME = "CodeInterpreterAgentService"
CONTAINER_PORT = 8080
REPO_ROOT = Path(__file__).resolve().parents[2]
# The Fargate default capability set minus SETUID, SETGID and KILL.
DROPPED_CAPABILITIES = (
    ecs.Capability.AUDIT_WRITE,
    ecs.Capability.CHOWN,
    ecs.Capability.DAC_OVERRIDE,
    ecs.Capability.FOWNER,
    ecs.Capability.FSETID,
    ecs.Capability.MKNOD,
    ecs.Capability.NET_BIND_SERVICE,
    ecs.Capability.NET_RAW,
    ecs.Capability.SETFCAP,
    ecs.Capability.SETPCAP,
    ecs.Capability.SYS_CHROOT,
)
IMAGE_EXCLUDES = ["infra", "cdk.out", ".git", ".venv", "docs", "scripts", ".env", ".env.*", "**/__pycache__"]


class ServiceStack(cdk.Stack):
    def __init__(
        self, scope: Construct, construct_id: str, secret: secretsmanager.ISecret, **kwargs
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)
        self.config = Config.from_scope(self)
        removal = cdk.RemovalPolicy.DESTROY if self.config.destroy_on_delete else cdk.RemovalPolicy.RETAIN

        self.vpc = self._build_vpc()
        self.alb_sg, self.service_sg = self._build_security_groups()
        log_group = logs.LogGroup(
            self, "Logs", retention=logs.RetentionDays.TWO_WEEKS, removal_policy=removal
        )
        task_definition = self._build_task_definition(secret, log_group)
        self.service = self._build_service(task_definition)
        self.alb = self._build_load_balancer()
        self._build_alarms()
        self._build_budget()

    def _build_vpc(self) -> ec2.Vpc:
        use_nat = self.config.egress_mode == "nat"
        subnets = [ec2.SubnetConfiguration(name="public", subnet_type=ec2.SubnetType.PUBLIC, cidr_mask=24)]
        if use_nat:
            subnets.append(
                ec2.SubnetConfiguration(
                    name="private", subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS, cidr_mask=24
                )
            )
        vpc = ec2.Vpc(
            self, "Vpc", max_azs=2, nat_gateways=1 if use_nat else 0, subnet_configuration=subnets
        )
        # Gateway endpoints carry no hourly charge. ECR image layers are served from S3.
        vpc.add_gateway_endpoint("S3", service=ec2.GatewayVpcEndpointAwsService.S3)
        return vpc

    def _build_security_groups(self) -> tuple[ec2.SecurityGroup, ec2.SecurityGroup]:
        alb_sg = ec2.SecurityGroup(
            self, "AlbSg", vpc=self.vpc, allow_all_outbound=False, description="ALB ingress"
        )
        if self.config.public_https:
            peer = ec2.Peer.ipv4(self.config.public_ingress_cidr)
            alb_sg.add_ingress_rule(peer, ec2.Port.tcp(443), "HTTPS from allowed CIDR")
            alb_sg.add_ingress_rule(peer, ec2.Port.tcp(80), "HTTP redirect from allowed CIDR")
        else:
            alb_sg.add_ingress_rule(
                ec2.Peer.ipv4(self.vpc.vpc_cidr_block), ec2.Port.tcp(80), "HTTP from inside the VPC"
            )
        service_sg = ec2.SecurityGroup(
            self, "ServiceSg", vpc=self.vpc, allow_all_outbound=False, description="Fargate tasks"
        )
        service_sg.add_ingress_rule(alb_sg, ec2.Port.tcp(CONTAINER_PORT), "From the ALB only")
        service_sg.add_egress_rule(ec2.Peer.any_ipv4(), ec2.Port.tcp(443), "HTTPS to model APIs and ECR")
        return alb_sg, service_sg

    def _build_task_definition(
        self, secret: secretsmanager.ISecret, log_group: logs.LogGroup
    ) -> ecs.FargateTaskDefinition:
        arch = self.config.cpu_arch
        task_definition = ecs.FargateTaskDefinition(
            self,
            "Task",
            cpu=self.config.cpu,
            memory_limit_mib=self.config.memory_mib,
            runtime_platform=ecs.RuntimePlatform(
                operating_system_family=ecs.OperatingSystemFamily.LINUX,
                cpu_architecture=(
                    ecs.CpuArchitecture.ARM64 if arch == "arm64" else ecs.CpuArchitecture.X86_64
                ),
            ),
            # Deliberately empty. Code the model runs can reach these credentials.
            task_role=iam.Role(
                self,
                "TaskRole",
                assumed_by=iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
                description="Intentionally has no policies",
            ),
        )
        for volume in ("workspace", "tmp"):
            task_definition.add_volume(name=volume)  # ephemeral task volume
        container = task_definition.add_container(
            "app",
            image=self._build_image(arch),
            readonly_root_filesystem=True,
            essential=True,
            stop_timeout=cdk.Duration.seconds(30),
            logging=ecs.LogDrivers.aws_logs(stream_prefix="app", log_group=log_group),
            secrets=self._container_secrets(secret),
            environment={"WORKSPACE_ROOT": "/workspace"},
            health_check=ecs.HealthCheck(
                command=[
                    "CMD-SHELL",
                    "python -c \"import urllib.request as u; u.urlopen('http://127.0.0.1:8080/health', timeout=3)\"",
                ],
                interval=cdk.Duration.seconds(30),
                timeout=cdk.Duration.seconds(5),
                retries=3,
                start_period=cdk.Duration.seconds(30),
            ),
            linux_parameters=self._linux_parameters(),
        )
        container.add_port_mappings(ecs.PortMapping(container_port=CONTAINER_PORT))
        container.add_mount_points(
            ecs.MountPoint(container_path="/workspace", source_volume="workspace", read_only=False),
            ecs.MountPoint(container_path="/tmp", source_volume="tmp", read_only=False),
        )
        return task_definition

    def _linux_parameters(self) -> ecs.LinuxParameters:
        params = ecs.LinuxParameters(self, "LinuxParams", init_process_enabled=True)
        # Fargate accepts only SYS_PTRACE in "add", so the needed capabilities are kept by
        # dropping every other one from the Fargate default set. The entrypoint starts as root,
        # switches to the app user and keeps SETUID, SETGID and KILL so the API process can
        # start code workers under the sandbox UID and kill them. See docs/security.md.
        params.drop_capabilities(*DROPPED_CAPABILITIES)
        return params

    def _build_image(self, arch: str) -> ecs.ContainerImage:
        asset = ecr_assets.DockerImageAsset(
            self,
            "Image",
            directory=str(REPO_ROOT),
            file="Dockerfile",
            exclude=IMAGE_EXCLUDES,
            platform=(
                ecr_assets.Platform.LINUX_ARM64 if arch == "arm64" else ecr_assets.Platform.LINUX_AMD64
            ),
        )
        return ecs.ContainerImage.from_docker_image_asset(asset)

    def _container_secrets(self, secret: secretsmanager.ISecret) -> dict[str, ecs.Secret]:
        fields = ["ANTHROPIC_API_KEY"]
        if self.config.openai_enabled:
            fields.append("OPENAI_API_KEY")
        if self.config.api_token_enabled:
            fields.append("SERVICE_API_TOKEN")
        return {name: ecs.Secret.from_secrets_manager(secret, name) for name in fields}

    def _build_service(self, task_definition: ecs.FargateTaskDefinition) -> ecs.FargateService:
        in_public = self.config.egress_mode == "public_ip"
        cluster = ecs.Cluster(self, "Cluster", vpc=self.vpc)
        service = ecs.FargateService(
            self,
            "Service",
            cluster=cluster,
            task_definition=task_definition,
            desired_count=self.config.desired_count,
            assign_public_ip=in_public,
            vpc_subnets=ec2.SubnetSelection(
                subnet_type=ec2.SubnetType.PUBLIC if in_public else ec2.SubnetType.PRIVATE_WITH_EGRESS
            ),
            security_groups=[self.service_sg],
            circuit_breaker=ecs.DeploymentCircuitBreaker(rollback=True),
            min_healthy_percent=100,
            max_healthy_percent=200,
            health_check_grace_period=cdk.Duration.seconds(60),
        )
        if self.config.autoscaling:
            scaling = service.auto_scale_task_count(
                min_capacity=self.config.desired_count, max_capacity=self.config.max_count
            )
            scaling.scale_on_cpu_utilization("Cpu", target_utilization_percent=60)
        return service

    def _build_load_balancer(self) -> elbv2.ApplicationLoadBalancer:
        public = self.config.public_https
        alb = elbv2.ApplicationLoadBalancer(
            self,
            "Alb",
            vpc=self.vpc,
            internet_facing=public,
            security_group=self.alb_sg,
            idle_timeout=cdk.Duration.seconds(300),
            vpc_subnets=ec2.SubnetSelection(
                subnet_type=(
                    ec2.SubnetType.PRIVATE_WITH_EGRESS
                    if self.config.egress_mode == "nat" and not public
                    else ec2.SubnetType.PUBLIC
                )
            ),
        )
        # open=False stops the listener from adding a 0.0.0.0/0 ingress rule.
        if public:
            listener = alb.add_listener(
                "Https",
                port=443,
                open=False,
                certificates=[elbv2.ListenerCertificate.from_arn(self.config.certificate_arn)],
            )
            alb.add_redirect(source_port=80, target_port=443, open=False)
            scheme = "https"
        else:
            listener = alb.add_listener("Http", port=80, open=False)
            scheme = "http"
        listener.add_targets(
            "Service",
            port=CONTAINER_PORT,
            protocol=elbv2.ApplicationProtocol.HTTP,
            targets=[self.service],
            deregistration_delay=cdk.Duration.seconds(30),
            health_check=elbv2.HealthCheck(path="/health", healthy_http_codes="200"),
        )
        cdk.CfnOutput(self, "BaseUrl", value=f"{scheme}://{alb.load_balancer_dns_name}")
        cdk.CfnOutput(self, "AlbScheme", value="internet-facing" if public else "internal")
        return alb

    def _build_alarms(self) -> None:
        action = None
        if self.config.notification_email:
            topic = sns.Topic(self, "AlarmTopic")
            topic.add_subscription(subscriptions.EmailSubscription(self.config.notification_email))
            action = cw_actions.SnsAction(topic)
        metrics = {
            "CpuHigh": self.service.metric_cpu_utilization(period=cdk.Duration.minutes(5)),
            "MemoryHigh": self.service.metric_memory_utilization(period=cdk.Duration.minutes(5)),
        }
        for name, metric in metrics.items():
            alarm = cloudwatch.Alarm(
                self,
                name,
                metric=metric,
                threshold=80,
                evaluation_periods=3,
                comparison_operator=cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
                treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
            )
            if action:
                alarm.add_alarm_action(action)

    def _build_budget(self) -> None:
        email = self.config.notification_email
        if not email:
            cdk.CfnOutput(self, "BudgetAlarm", value="disabled (set notification_email to enable)")
            return
        subscriber = budgets.CfnBudget.SubscriberProperty(subscription_type="EMAIL", address=email)

        def notification(kind: str, threshold: float) -> budgets.CfnBudget.NotificationWithSubscribersProperty:
            return budgets.CfnBudget.NotificationWithSubscribersProperty(
                notification=budgets.CfnBudget.NotificationProperty(
                    notification_type=kind,
                    comparison_operator="GREATER_THAN",
                    threshold=threshold,
                    threshold_type="PERCENTAGE",
                ),
                subscribers=[subscriber],
            )

        budgets.CfnBudget(
            self,
            "MonthlyBudget",
            budget=budgets.CfnBudget.BudgetDataProperty(
                budget_type="COST",
                time_unit="MONTHLY",
                budget_limit=budgets.CfnBudget.SpendProperty(
                    amount=self.config.monthly_budget_usd, unit="USD"
                ),
            ),
            notifications_with_subscribers=[
                notification("ACTUAL", 80),
                notification("FORECASTED", 100),
            ],
        )
