"""Tag discovery API routes."""

from __future__ import annotations

import json as _json
import logging
from typing import Any, Dict

from fastapi import APIRouter, Query, status

from ..aws_clients import tagging_client, validate_region
from ..errors import APIError, handle_tagging_error
from ..models import (
    APIErrorResponse,
    TagKeysResponse,
    TagResourcesResponse,
    TagValuesResponse,
    normalize_service_name,
)
from ..scanners._utils import _service_from_arn

logger = logging.getLogger(__name__)


def register_routes(api: APIRouter) -> None:
    """Register all tag-discovery routes on the given router."""

    @api.get(
        "/tags/keys",
        response_model=TagKeysResponse,
        responses={401: {"model": APIErrorResponse}, 403: {"model": APIErrorResponse}, 502: {"model": APIErrorResponse}},
    )
    def get_tag_keys(region: str = Query(default="us-east-1")) -> Dict[str, Any]:
        region = validate_region(region)
        try:
            client = tagging_client(region)
            keys = []
            paginator = client.get_paginator("get_tag_keys")
            for page in paginator.paginate(PaginationConfig={"MaxItems": 5000}):
                keys.extend(page.get("TagKeys", []))
            return {"keys": sorted(set(keys))}
        except Exception as exc:
            handle_tagging_error(exc, region, "get_tag_keys")

    @api.get(
        "/tags/values",
        response_model=TagValuesResponse,
        responses={401: {"model": APIErrorResponse}, 403: {"model": APIErrorResponse}, 502: {"model": APIErrorResponse}},
    )
    def get_tag_values(
        region: str = Query(default="us-east-1"),
        key: str = Query(..., min_length=1, max_length=128),
    ) -> Dict[str, Any]:
        region = validate_region(region)
        try:
            client = tagging_client(region)
            values = []
            paginator = client.get_paginator("get_tag_values")
            for page in paginator.paginate(Key=key, PaginationConfig={"MaxItems": 5000}):
                values.extend(page.get("TagValues", []))
            return {"key": key, "values": sorted(set(values))}
        except Exception as exc:
            handle_tagging_error(exc, region, "get_tag_values")

    @api.get(
        "/tags/resources",
        response_model=TagResourcesResponse,
        responses={401: {"model": APIErrorResponse}, 403: {"model": APIErrorResponse}, 502: {"model": APIErrorResponse}},
    )
    def get_tag_resources(
        region: str = Query(default="us-east-1"),
        tag_filters: str = Query(..., description="JSON array of {Key, Values} filter objects"),
    ) -> Dict[str, Any]:
        region = validate_region(region)

        _MAX_TAG_FILTER_ENTRIES = 20
        _MAX_TAG_KEY_LEN = 256
        _MAX_TAG_VALUE_LEN = 512
        _MAX_TAG_VALUES_PER_KEY = 50

        try:
            parsed_filters = _json.loads(tag_filters)
            if not isinstance(parsed_filters, list):
                raise ValueError("tag_filters must be a JSON array")
            if len(parsed_filters) > _MAX_TAG_FILTER_ENTRIES:
                raise ValueError(f"tag_filters may not exceed {_MAX_TAG_FILTER_ENTRIES} entries")
            for i, entry in enumerate(parsed_filters):
                if not isinstance(entry, dict):
                    raise ValueError(f"tag_filters[{i}] must be an object")
                if "Key" not in entry:
                    raise ValueError(f"tag_filters[{i}] is missing required field 'Key'")
                if not isinstance(entry.get("Key"), str):
                    raise ValueError(f"tag_filters[{i}].Key must be a string")
                if len(entry["Key"]) > _MAX_TAG_KEY_LEN:
                    raise ValueError(f"tag_filters[{i}].Key exceeds maximum length of {_MAX_TAG_KEY_LEN}")
                if "Values" in entry:
                    if not isinstance(entry["Values"], list):
                        raise ValueError(f"tag_filters[{i}].Values must be an array")
                    if len(entry["Values"]) > _MAX_TAG_VALUES_PER_KEY:
                        raise ValueError(f"tag_filters[{i}].Values may not exceed {_MAX_TAG_VALUES_PER_KEY} items")
                    for j, v in enumerate(entry["Values"]):
                        if not isinstance(v, str) or len(v) > _MAX_TAG_VALUE_LEN:
                            raise ValueError(f"tag_filters[{i}].Values[{j}] must be a string of at most {_MAX_TAG_VALUE_LEN} characters")
        except (ValueError, _json.JSONDecodeError) as exc:
            logger.debug("tag_filters validation failed: %s", exc)
            raise APIError(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                code="validation_error",
                message="tag_filters parameter is malformed or contains invalid values.",
            ) from exc

        try:
            client = tagging_client(region)
            arns_set: set = set()
            arns: list = []
            paginator = client.get_paginator("get_resources")
            _MAX_DISCOVERED_RESOURCES = 5000
            for page in paginator.paginate(
                TagFilters=parsed_filters, ResourcesPerPage=100,
                PaginationConfig={"MaxItems": _MAX_DISCOVERED_RESOURCES},
            ):
                for entry in page.get("ResourceTagMappingList", []):
                    arn = entry.get("ResourceARN")
                    if arn and arn not in arns_set:
                        arns_set.add(arn)
                        arns.append(arn)

            if region != "us-east-1":
                try:
                    global_client = tagging_client("us-east-1")
                    global_paginator = global_client.get_paginator("get_resources")
                    for page in global_paginator.paginate(
                        TagFilters=parsed_filters, ResourcesPerPage=100,
                        PaginationConfig={"MaxItems": _MAX_DISCOVERED_RESOURCES},
                    ):
                        for entry in page.get("ResourceTagMappingList", []):
                            arn = entry.get("ResourceARN")
                            if arn and arn not in arns_set:
                                raw_svc = _service_from_arn(arn)
                                svc = normalize_service_name(raw_svc) if raw_svc else ""
                                if svc in ("cloudfront", "route53", "iam", "wafv2", "organizations"):
                                    arns_set.add(arn)
                                    arns.append(arn)
                except Exception as exc:
                    logger.debug("Global service tag discovery from us-east-1 failed: %s", exc)

            services = sorted(s for s in set(normalize_service_name(_service_from_arn(arn)) for arn in arns) if s)
            return {"arns": arns, "services": services}
        except Exception as exc:
            handle_tagging_error(exc, region, "get_resources")
