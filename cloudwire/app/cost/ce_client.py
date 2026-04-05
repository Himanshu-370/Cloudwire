"""AWS Cost Explorer API client with rate limiting and error handling."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from threading import Lock
from typing import Any, Dict, List, Optional

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)

# CE rate limit: 5 req/s -> 200ms between calls
_MIN_INTERVAL_S = 0.2
_rate_lock = Lock()
_last_call_time = 0.0

# CE service name -> CloudWire canonical service name
CE_SERVICE_MAP: Dict[str, str] = {
    "Amazon Elastic Compute Cloud - Compute": "ec2",
    "Amazon EC2": "ec2",
    "EC2 - Other": "ec2",
    "Amazon Simple Storage Service": "s3",
    "Amazon Relational Database Service": "rds",
    "Amazon DynamoDB": "dynamodb",
    "Amazon ElastiCache": "elasticache",
    "Amazon Redshift": "redshift",
    "AWS Lambda": "lambda",
    "Amazon Simple Queue Service": "sqs",
    "Amazon Simple Notification Service": "sns",
    "Amazon API Gateway": "apigateway",
    "Amazon CloudFront": "cloudfront",
    "Amazon Elastic Container Service": "ecs",
    "AWS Step Functions": "stepfunctions",
    "Amazon Kinesis": "kinesis",
    "Amazon Virtual Private Cloud": "vpc",
    "AWS Glue": "glue",
    "Amazon EventBridge": "eventbridge",
    "Amazon OpenSearch Service": "opensearch",
    "Amazon Cognito": "cognito",
    "AWS Key Management Service": "kms",
    "Amazon Route 53": "route53",
    "Amazon CloudWatch": "cloudwatch",
}

# Services that support resource-level cost breakdown via GetCostAndUsageWithResources
RESOURCE_LEVEL_SERVICES = {"ec2", "rds", "s3", "dynamodb", "elasticache", "redshift"}


@dataclass
class CostResult:
    resource_costs: Dict[str, float] = field(default_factory=dict)
    service_totals: Dict[str, float] = field(default_factory=dict)
    resource_level_available: bool = False
    period_start: str = ""
    period_end: str = ""
    error: Optional[str] = None


def _rate_limit() -> None:
    global _last_call_time
    with _rate_lock:
        now = time.monotonic()
        elapsed = now - _last_call_time
        if elapsed < _MIN_INTERVAL_S:
            time.sleep(_MIN_INTERVAL_S - elapsed)
        _last_call_time = time.monotonic()


def _get_period() -> tuple[str, str]:
    now = datetime.now(timezone.utc)
    start = now.replace(day=1).strftime("%Y-%m-%d")
    # End is tomorrow (exclusive) for CE API
    end = now.strftime("%Y-%m-%d")
    if now.day == 1:
        end = now.strftime("%Y-%m-%d")
    return start, end


def _ce_client(session: boto3.session.Session) -> Any:
    return session.client("ce", region_name="us-east-1")


def fetch_service_costs(session: boto3.session.Session, region: str) -> CostResult:
    """Fetch service-level cost totals for the current month, filtered to a region."""
    period_start, period_end = _get_period()
    if period_start == period_end:
        return CostResult(period_start=period_start, period_end=period_end)

    result = CostResult(period_start=period_start, period_end=period_end)

    try:
        client = _ce_client(session)
        _rate_limit()

        kwargs: Dict[str, Any] = {
            "TimePeriod": {"Start": period_start, "End": period_end},
            "Granularity": "MONTHLY",
            "Metrics": ["UnblendedCost"],
            "GroupBy": [{"Type": "DIMENSION", "Key": "SERVICE"}],
            "Filter": {
                "Dimensions": {
                    "Key": "REGION",
                    "Values": [region],
                }
            },
        }

        while True:
            response = client.get_cost_and_usage(**kwargs)
            for time_result in response.get("ResultsByTime", []):
                for group in time_result.get("Groups", []):
                    service_name = group["Keys"][0]
                    amount = float(group["Metrics"]["UnblendedCost"]["Amount"])
                    canonical = CE_SERVICE_MAP.get(service_name)
                    if canonical:
                        result.service_totals[canonical] = (
                            result.service_totals.get(canonical, 0.0) + amount
                        )

            token = response.get("NextPageToken")
            if not token:
                break
            kwargs["NextPageToken"] = token
            _rate_limit()

    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code == "OptInRequired":
            result.error = (
                "AWS Cost Explorer is not activated for this account. "
                "Enable it at https://console.aws.amazon.com/billing/home#/costexplorer — "
                "activation takes up to 24 hours."
            )
        elif code in ("AccessDeniedException", "AccessDenied"):
            result.error = (
                "Cost Explorer access denied. Add the ce:GetCostAndUsage "
                "permission to your IAM role."
            )
        else:
            result.error = f"Cost Explorer API error: {code}"
        logger.warning("CE service cost fetch failed: %s", exc)
    except Exception as exc:
        result.error = f"Failed to fetch cost data: {type(exc).__name__}"
        logger.exception("Unexpected CE error")

    return result


def fetch_resource_costs(
    session: boto3.session.Session, region: str
) -> CostResult:
    """Fetch resource-level costs for services that support it."""
    period_start, period_end = _get_period()
    if period_start == period_end:
        return CostResult(period_start=period_start, period_end=period_end)

    result = CostResult(
        period_start=period_start,
        period_end=period_end,
        resource_level_available=False,
    )

    try:
        client = _ce_client(session)
        _rate_limit()

        kwargs: Dict[str, Any] = {
            "TimePeriod": {"Start": period_start, "End": period_end},
            "Granularity": "MONTHLY",
            "Metrics": ["UnblendedCost"],
            "GroupBy": [
                {"Type": "DIMENSION", "Key": "RESOURCE_ID"},
            ],
            "Filter": {
                "Dimensions": {
                    "Key": "REGION",
                    "Values": [region],
                }
            },
        }

        while True:
            response = client.get_cost_and_usage_with_resources(**kwargs)
            result.resource_level_available = True

            for time_result in response.get("ResultsByTime", []):
                for group in time_result.get("Groups", []):
                    resource_id = group["Keys"][0]
                    amount = float(group["Metrics"]["UnblendedCost"]["Amount"])
                    if amount > 0 and resource_id:
                        result.resource_costs[resource_id] = (
                            result.resource_costs.get(resource_id, 0.0) + amount
                        )

            token = response.get("NextPageToken")
            if not token:
                break
            kwargs["NextPageToken"] = token
            _rate_limit()

    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code == "ValidationException":
            logger.info(
                "Resource-level cost data not available (not enabled). "
                "Falling back to service-level only."
            )
            result.error = (
                "Resource-level cost data is not available. "
                "Enable hourly/resource-level granularity in AWS Cost Explorer settings "
                "for per-resource cost breakdowns. Service-level totals are shown instead."
            )
        elif code == "OptInRequired":
            result.error = (
                "AWS Cost Explorer is not activated for this account. "
                "Enable it at https://console.aws.amazon.com/billing/home#/costexplorer — "
                "activation takes up to 24 hours."
            )
        elif code in ("AccessDeniedException", "AccessDenied"):
            result.error = (
                "Cost Explorer access denied. Add the ce:GetCostAndUsageWithResources "
                "permission to your IAM role."
            )
        else:
            result.error = f"Cost Explorer API error: {code}"
        logger.warning("CE resource cost fetch failed: %s", exc)
    except Exception as exc:
        result.error = f"Failed to fetch resource cost data: {type(exc).__name__}"
        logger.exception("Unexpected CE resource cost error")

    return result
