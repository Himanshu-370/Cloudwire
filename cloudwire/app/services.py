"""Canonical service registry — single source of truth for AWS service metadata."""

from __future__ import annotations

from typing import Dict, List

SERVICE_ALIASES: Dict[str, str] = {
    "api-gateway": "apigateway",
    "apigw": "apigateway",
    "execute-api": "apigateway",
    "event-bridge": "eventbridge",
    "events": "eventbridge",
    "cognito-idp": "cognito",
    "elasticloadbalancing": "elb",
    "states": "stepfunctions",
    "monitoring": "cloudwatch",
    "logs": "cloudwatch",
    "es": "opensearch",
    "aoss": "opensearch",
    "elasticfilesystem": "efs",
    "rds-data": "rds",
    "redshift-data": "redshift",
    "waf": "wafv2",
    "waf-regional": "wafv2",
    "msk": "kafka",
    "elasticmapreduce": "emr",
    "emr-serverless": "emr",
    "elastic-beanstalk": "elasticbeanstalk",
    "certificate-manager": "acm",
    "amazonmq": "mq",
    "firehose": "firehose",
    "codepipeline": "codepipeline",
    "codebuild": "codebuild",
}


def normalize_service_name(service: str) -> str:
    key = service.lower().strip()
    return SERVICE_ALIASES.get(key, key)


# ---------------------------------------------------------------------------
# Service registry
#
# Each entry is keyed by the canonical service id (same key used in
# scanner.service_scanners) and carries display metadata consumed by both
# the backend API and the frontend.
# ---------------------------------------------------------------------------

SERVICE_REGISTRY: Dict[str, dict] = {
    # API & Integration
    "apigateway":    {"label": "API Gateway",      "group": "API & Integration",    "default": True},
    "eventbridge":   {"label": "EventBridge",       "group": "API & Integration",    "default": True},
    "appsync":       {"label": "AppSync",           "group": "API & Integration",    "default": False},
    "mq":            {"label": "Amazon MQ",         "group": "API & Integration",    "default": False},
    # Compute
    "lambda":        {"label": "Lambda",            "group": "Compute",              "default": True},
    "ec2":           {"label": "EC2",               "group": "Compute",              "default": False},
    "ecs":           {"label": "ECS",               "group": "Compute",              "default": False},
    "eks":           {"label": "EKS",               "group": "Compute",              "default": False},
    "stepfunctions": {"label": "Step Functions",    "group": "Compute",              "default": False},
    "glue":          {"label": "Glue",              "group": "Compute",              "default": False},
    "emr":           {"label": "EMR",               "group": "Compute",              "default": False},
    "elasticbeanstalk": {"label": "Elastic Beanstalk", "group": "Compute",           "default": False},
    "batch":         {"label": "Batch",             "group": "Compute",              "default": False},
    # Queues & Streams
    "sqs":           {"label": "SQS",               "group": "Queues & Streams",     "default": True},
    "sns":           {"label": "SNS",               "group": "Queues & Streams",     "default": False},
    "kinesis":       {"label": "Kinesis",           "group": "Queues & Streams",     "default": False},
    "kafka":         {"label": "MSK",               "group": "Queues & Streams",     "default": False},
    "firehose":      {"label": "Kinesis Firehose",  "group": "Queues & Streams",     "default": False},
    # Database & Storage
    "dynamodb":      {"label": "DynamoDB",          "group": "Database & Storage",   "default": True},
    "s3":            {"label": "S3",                "group": "Database & Storage",    "default": False},
    "rds":           {"label": "RDS",               "group": "Database & Storage",    "default": False},
    "elasticache":   {"label": "ElastiCache",       "group": "Database & Storage",    "default": False},
    "redshift":      {"label": "Redshift",          "group": "Database & Storage",    "default": False},
    "opensearch":    {"label": "OpenSearch",        "group": "Database & Storage",    "default": False},
    "efs":           {"label": "EFS",               "group": "Database & Storage",    "default": False},
    "ecr":           {"label": "ECR",               "group": "Database & Storage",    "default": False},
    # Networking
    "vpc":           {"label": "VPC Network",       "group": "Networking",           "default": True},
    "cloudfront":    {"label": "CloudFront",        "group": "Networking",           "default": False},
    "route53":       {"label": "Route 53",          "group": "Networking",           "default": False},
    "elb":           {"label": "ELB",               "group": "Networking",           "default": False},
    "acm":           {"label": "ACM",               "group": "Networking",           "default": False},
    # Security & Identity
    "iam":           {"label": "IAM",               "group": "Security & Identity",  "default": False},
    "cognito":       {"label": "Cognito",           "group": "Security & Identity",  "default": False},
    "secretsmanager": {"label": "Secrets Manager",  "group": "Security & Identity",  "default": False},
    "kms":           {"label": "KMS",               "group": "Security & Identity",  "default": False},
    "wafv2":         {"label": "WAF",               "group": "Security & Identity",  "default": False},
    "guardduty":     {"label": "GuardDuty",         "group": "Security & Identity",  "default": False},
    # Monitoring & Management
    "cloudwatch":    {"label": "CloudWatch",        "group": "Monitoring & Mgmt",    "default": False},
    "cloudtrail":    {"label": "CloudTrail",        "group": "Monitoring & Mgmt",    "default": False},
    "cloudformation": {"label": "CloudFormation",   "group": "Monitoring & Mgmt",    "default": False},
    # Analytics & ML
    "athena":        {"label": "Athena",            "group": "Analytics & ML",       "default": False},
    "sagemaker":     {"label": "SageMaker",         "group": "Analytics & ML",       "default": False},
    # Developer Tools
    "codepipeline":  {"label": "CodePipeline",      "group": "Developer Tools",      "default": False},
    "codebuild":     {"label": "CodeBuild",         "group": "Developer Tools",      "default": False},
}

# Derived helpers --------------------------------------------------------

DEFAULT_SERVICES: List[str] = [sid for sid, meta in SERVICE_REGISTRY.items() if meta["default"]]

# Stable group ordering (matches the frontend's original order).
_GROUP_ORDER = [
    "API & Integration",
    "Compute",
    "Queues & Streams",
    "Database & Storage",
    "Networking",
    "Security & Identity",
    "Monitoring & Mgmt",
    "Analytics & ML",
    "Developer Tools",
]


def get_services_payload() -> dict:
    """Build the JSON payload returned by ``GET /api/services``."""
    groups: Dict[str, list] = {}
    for sid, meta in SERVICE_REGISTRY.items():
        groups.setdefault(meta["group"], []).append(
            {"id": sid, "label": meta["label"], "group": meta["group"], "default": meta["default"]}
        )

    # Flatten groups in display order.
    services: list = []
    for group_name in _GROUP_ORDER:
        services.extend(groups.get(group_name, []))
    # Append any groups not in _GROUP_ORDER (future-proofing).
    for group_name, items in groups.items():
        if group_name not in _GROUP_ORDER:
            services.extend(items)

    return {
        "services": services,
        "aliases": dict(SERVICE_ALIASES),
    }
