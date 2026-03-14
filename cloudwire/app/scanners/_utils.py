"""Shared constants and utilities for scanner modules."""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Tuple

logger = logging.getLogger("cloudwire.app.scanner")

# Matches the minimal valid AWS ARN structure:
#   arn:aws[suffix]:<service>:<region>:<account-id>:<resource>
# where account-id is either empty (global services like S3/IAM) or 12 digits.
# The resource part must be non-empty.
_ARN_PATTERN = re.compile(
    r"^arn:aws[a-z-]*:[a-z0-9-]+:[a-z0-9-]*:(\d{12}|):[\w/:.*@-]+"
)


def _safe_list(value: Any) -> List[Any]:
    if isinstance(value, list):
        return value
    if value is None:
        return []
    return [value]


def _service_from_arn(arn: str) -> str:
    """Extract the AWS service segment from an ARN, returning '' on failure."""
    parts = arn.split(":")
    return parts[2] if len(parts) > 2 and parts[2] else ""


# Well-known Lambda environment variable suffixes that imply a resource reference.
# Mapping of suffix -> (service, node_type).
_ENV_VAR_CONVENTIONS: Dict[str, Tuple[str, str]] = {
    "_TABLE_NAME": ("dynamodb", "table"),
    "_TABLE": ("dynamodb", "table"),
    "_QUEUE_URL": ("sqs", "queue"),
    "_QUEUE_NAME": ("sqs", "queue"),
    "_BUCKET": ("s3", "bucket"),
    "_BUCKET_NAME": ("s3", "bucket"),
    "_STREAM_NAME": ("kinesis", "stream"),
    "_CLUSTER_NAME": ("ecs", "cluster"),
    "_CLUSTER": ("ecs", "cluster"),
    "_CACHE_ENDPOINT": ("elasticache", "cluster"),
}
