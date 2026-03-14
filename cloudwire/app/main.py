from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4

import boto3
from botocore.config import Config
from botocore.exceptions import (
    BotoCoreError,
    ClientError,
    ConnectTimeoutError,
    CredentialRetrievalError,
    EndpointConnectionError,
    NoCredentialsError,
    PartialCredentialsError,
    ReadTimeoutError,
)
from fastapi import APIRouter, FastAPI, File, HTTPException, Query, Request, UploadFile, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

from .models import (
    _REGION_RE,
    APIErrorResponse,
    GraphResponse,
    ResourceResponse,
    ScanJobCreateResponse,
    ScanJobStatusResponse,
    ScanRequest,
    TagKeysResponse,
    TagResourcesResponse,
    TagValuesResponse,
    normalize_service_name,
)
from .graph_store import GraphStore
from .scan_jobs import ScanJob, ScanJobStore
from .scanner import AWSGraphScanner, ScanCancelledError, ScanExecutionOptions
from .services import get_services_payload
from .terraform_parser import (
    MAX_BYTES_PER_FILE as _TF_MAX_FILE_BYTES,
    MAX_FILES as _TF_MAX_FILES,
    MAX_TOTAL_BYTES as _TF_MAX_TOTAL_BYTES,
    TerraformParser,
    validate_tfstate_content,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Static-file directory (cloudwire/static/ relative to this package)
# ---------------------------------------------------------------------------
_STATIC_DIR = Path(__file__).parent.parent / "static"


class APIError(Exception):
    def __init__(
        self,
        *,
        status_code: int,
        code: str,
        message: str,
        details: Optional[Any] = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details


def _error_payload(code: str, message: str, details: Optional[Any] = None) -> Dict[str, Any]:
    return {
        "error": {
            "code": code,
            "message": message,
            "details": details,
        }
    }


def _normalize_services(services: List[str]) -> List[str]:
    normalized = []
    for service in services:
        key = normalize_service_name(service)
        if key and key not in normalized:
            normalized.append(key)
    return normalized


def _resolve_option(value: Optional[bool], default: bool) -> bool:
    return default if value is None else value


def _resolve_scan_options(payload: ScanRequest) -> ScanExecutionOptions:
    default_iam = payload.mode == "deep"
    default_describes = payload.mode == "deep"
    return ScanExecutionOptions(
        mode=payload.mode,
        include_iam_inference=_resolve_option(payload.include_iam_inference, default_iam),
        include_resource_describes=_resolve_option(payload.include_resource_describes, default_describes),
    )


def _cache_ttl_seconds(mode: str) -> int:
    return 300 if mode == "quick" else 1800


def _friendly_exception_message(exc: Exception) -> str:
    if isinstance(exc, (NoCredentialsError, PartialCredentialsError, CredentialRetrievalError)):
        return "AWS credentials were not found. Set AWS credentials or run saml2aws login before scanning."
    if isinstance(exc, (EndpointConnectionError, ConnectTimeoutError, ReadTimeoutError)):
        return "Unable to reach the AWS API endpoint for the selected region."
    if isinstance(exc, ClientError):
        code = exc.response.get("Error", {}).get("Code", "")
        if code in {"ExpiredToken", "ExpiredTokenException", "RequestExpired"}:
            return "Your AWS session has expired. Refresh credentials and try again."
        if code in {"AccessDenied", "AccessDeniedException", "UnauthorizedOperation"}:
            return "AWS access was denied for this operation. Verify the assumed role permissions."
        # Do not echo raw AWS error messages — they may contain account IDs and ARNs
        logger.warning("AWS ClientError [%s]: %s", code, exc.response.get("Error", {}).get("Message", ""))
        return f"AWS API request failed ({code or 'ClientError'})."
    if isinstance(exc, BotoCoreError):
        return "The AWS SDK failed to complete the request."
    return "Unexpected server error."


def _resolve_account_id(region: str) -> str:
    session = boto3.session.Session(region_name=region)
    client = session.client(
        "sts",
        config=Config(
            retries={"mode": "adaptive", "max_attempts": 10},
            max_pool_connections=8,
            connect_timeout=3,
            read_timeout=10,
        ),
    )
    try:
        identity = client.get_caller_identity()
        return str(identity.get("Account", "unknown"))
    except (NoCredentialsError, PartialCredentialsError, CredentialRetrievalError) as exc:
        raise APIError(
            status_code=status.HTTP_401_UNAUTHORIZED,
            code="aws_credentials_missing",
            message=_friendly_exception_message(exc),
        ) from exc
    except ClientError as exc:
        aws_code = exc.response.get("Error", {}).get("Code", "")
        status_code = (
            status.HTTP_403_FORBIDDEN
            if aws_code in {"AccessDenied", "AccessDeniedException", "UnauthorizedOperation"}
            else status.HTTP_401_UNAUTHORIZED
            if aws_code in {"ExpiredToken", "ExpiredTokenException", "RequestExpired"}
            else status.HTTP_502_BAD_GATEWAY
        )
        raise APIError(
            status_code=status_code,
            code="aws_account_lookup_failed",
            message=_friendly_exception_message(exc),
            details={"aws_error_code": aws_code or None, "region": region},
        ) from exc
    except (EndpointConnectionError, ConnectTimeoutError, ReadTimeoutError) as exc:
        raise APIError(
            status_code=status.HTTP_502_BAD_GATEWAY,
            code="aws_endpoint_unreachable",
            message=_friendly_exception_message(exc),
            details={"region": region},
        ) from exc
    except BotoCoreError as exc:
        raise APIError(
            status_code=status.HTTP_502_BAD_GATEWAY,
            code="aws_client_error",
            message=_friendly_exception_message(exc),
            details={"region": region},
        ) from exc


job_store = ScanJobStore(max_workers=4)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    yield
    job_store.shutdown()


app = FastAPI(title="CloudWire API", version="0.1.0", lifespan=lifespan)


# ---------------------------------------------------------------------------
# Security headers middleware
# ---------------------------------------------------------------------------

class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline';"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        return response


app.add_middleware(SecurityHeadersMiddleware)


# ---------------------------------------------------------------------------
# Exception handlers
# ---------------------------------------------------------------------------

@app.exception_handler(APIError)
async def api_error_handler(_: Request, exc: APIError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content=_error_payload(exc.code, exc.message, exc.details),
    )


@app.exception_handler(HTTPException)
async def http_exception_handler(_: Request, exc: HTTPException) -> JSONResponse:
    detail = exc.detail
    if isinstance(detail, dict) and "error" in detail:
        payload = detail
    elif isinstance(detail, str):
        payload = _error_payload("http_error", detail)
    else:
        payload = _error_payload("http_error", "Request failed.", detail)
    return JSONResponse(status_code=exc.status_code, content=payload)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content=_error_payload(
            "validation_error",
            "Request validation failed.",
            exc.errors(),
        ),
    )


@app.exception_handler(Exception)
async def unexpected_exception_handler(_: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled API exception", exc_info=exc)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content=_error_payload("internal_error", "Unexpected server error."),
    )


# ---------------------------------------------------------------------------
# Tag discovery helper
# ---------------------------------------------------------------------------

def _tagging_client(region: str):
    session = boto3.session.Session(region_name=region)
    return session.client(
        "resourcegroupstaggingapi",
        config=Config(
            retries={"mode": "adaptive", "max_attempts": 10},
            max_pool_connections=8,
            connect_timeout=3,
            read_timeout=10,
        ),
    )


def _validate_region(region: str) -> str:
    cleaned = region.strip()
    if not cleaned or not _REGION_RE.match(cleaned):
        raise APIError(
            status_code=422,
            code="validation_error",
            message=f"'{cleaned}' is not a valid AWS region identifier (e.g. us-east-1)",
        )
    return cleaned


def _service_from_arn(arn: str) -> str:
    parts = arn.split(":")
    service = parts[2] if len(parts) > 2 else ""
    return service if service else ""


# ---------------------------------------------------------------------------
# Scan runner (background thread)
# ---------------------------------------------------------------------------

def _services_from_tag_arns(tag_arns: List[str]) -> List[str]:
    """Extract and normalize unique service names from a list of ARNs."""
    seen: set = set()
    result: List[str] = []
    for arn in tag_arns:
        raw = _service_from_arn(arn)
        if not raw:
            continue
        canonical = normalize_service_name(raw)
        if canonical and canonical not in seen:
            seen.add(canonical)
            result.append(canonical)
    return result


def _seed_missing_tag_arns(
    graph_store: "GraphStore",
    tag_arns: List[str],
    region: str,
) -> int:
    """Ensure every tag-discovered ARN has a node in the graph.

    Services without a dedicated scanner rely on the generic tagging-API
    fallback, which may fail for certain service prefixes.  This function
    creates lightweight stub nodes for any ARNs that scanners didn't cover,
    so they survive the subsequent ``filter_by_arns`` pass and remain
    visible in the graph.  Stub nodes are marked with ``stub=True``.

    If *tag_filters* is provided, fetches tags from the tagging API so
    stub nodes carry the tags the user searched for.

    Returns the number of newly seeded nodes.
    """
    # Build a set of ARNs already present in the graph (O(n) once)
    existing_arns: set = set()
    payload = graph_store.get_graph_payload()
    for node in payload.get("nodes", []):
        for field in ("arn", "real_arn"):
            val = node.get(field)
            if val:
                existing_arns.add(val)

    missing_arns = [arn for arn in tag_arns if arn not in existing_arns]
    if not missing_arns:
        return 0

    # Best-effort: fetch tags for the missing ARNs from the tagging API
    # Use the ResourceARNList parameter to fetch tags for specific ARNs
    arn_tags: Dict[str, Dict[str, str]] = {}
    try:
        client = _tagging_client(region)
        paginator = client.get_paginator("get_resources")
        # Fetch in batches of 100 (API limit for ResourceARNList)
        for i in range(0, len(missing_arns), 100):
            batch = missing_arns[i:i + 100]
            for page in paginator.paginate(
                ResourceARNList=batch,
                ResourcesPerPage=100,
            ):
                for entry in page.get("ResourceTagMappingList", []):
                    entry_arn = entry.get("ResourceARN", "")
                    arn_tags[entry_arn] = {
                        t["Key"]: t["Value"]
                        for t in entry.get("Tags", [])
                    }
    except Exception as exc:
        logger.debug("Tag fetch for seed nodes failed: %s", exc)

    seeded = 0
    for arn in missing_arns:
        raw_service = _service_from_arn(arn)
        service = normalize_service_name(raw_service) if raw_service else "unknown"
        node_id = f"{service}:{arn}"

        # Extract a human-friendly label from the ARN
        resource_part = arn.split(":", 5)[-1] if len(arn.split(":")) >= 6 else arn
        label = resource_part.rsplit("/", 1)[-1] if "/" in resource_part else resource_part

        # Determine resource type from ARN structure
        resource_type = ""
        if "/" in resource_part:
            resource_type = resource_part.split("/")[0]
        elif ":" in resource_part:
            resource_type = resource_part.split(":")[0]

        node_attrs: Dict[str, Any] = {
            "arn": arn,
            "label": label,
            "service": service,
            "type": resource_type or "resource",
            "region": region,
            "stub": True,
        }
        tags = arn_tags.get(arn)
        if tags:
            node_attrs["tags"] = tags

        graph_store.add_node(node_id, **node_attrs)
        seeded += 1
    return seeded


def _run_scan_job(
    *,
    job_id: str,
    region: str,
    services: List[str],
    account_id: str,
    options: ScanExecutionOptions,
    tag_arns: Optional[List[str]] = None,
) -> None:
    job_store.mark_running(job_id)
    if job_store.is_cancel_requested(job_id):
        job_store.mark_cancelled(job_id)
        return
    job = job_store.get_job(job_id)

    # Auto-include all services referenced in tag ARNs so no tagged resources
    # are silently dropped when the frontend doesn't include them.
    services = list(services)  # work on a local copy to avoid mutating the caller's list
    if tag_arns:
        arn_services = _services_from_tag_arns(tag_arns)
        existing = set(services)
        for svc in arn_services:
            if svc not in existing:
                services.append(svc)
                existing.add(svc)
        # Update services_total on the job so the progress bar is accurate
        job.services_total = len(services)

    scanner = AWSGraphScanner(job.graph_store, options=options)

    def on_progress(event: str, service: str, services_done: int, services_total: int) -> None:
        job_store.update_progress(
            job_id,
            event=event,
            current_service=service,
            services_done=services_done,
            services_total=services_total,
        )

    try:
        scanner.scan(
            region=region,
            services=services,
            account_id=account_id,
            progress_callback=on_progress,
            should_cancel=lambda: job_store.is_cancel_requested(job_id),
        )
        if job_store.is_cancel_requested(job_id):
            job_store.mark_cancelled(job_id)
            return
        # Post-scan: seed any tag-discovered ARNs that the scanners missed
        # (e.g. services without a dedicated scanner whose generic tagging-API
        # query failed) so they still appear in the graph.
        if tag_arns:
            seeded = _seed_missing_tag_arns(job.graph_store, tag_arns, region)
            if seeded:
                logger.info("Seeded %d tag-discovered resource(s) not found by scanners", seeded)

            allowed = set(tag_arns)
            stats = job.graph_store.filter_by_arns(allowed)
            if stats["removed"]:
                job.graph_store.add_warning(
                    f"Tag filter: kept {stats['seeds']} matched + {stats['neighbors']} connected, "
                    f"removed {stats['removed']} unrelated (from {stats['total']} total scanned)."
                )
        job_store.mark_completed(job_id, ttl_seconds=_cache_ttl_seconds(options.mode))
    except ScanCancelledError:
        job.graph_store.add_warning("Scan cancelled by user request.")
        job_store.mark_cancelled(job_id)
    except Exception as exc:
        logger.exception("Scan job %s failed with unhandled exception", job_id)
        message = _friendly_exception_message(exc)
        job.graph_store.add_warning(f"scan failed: {message}")
        job_store.mark_failed(job_id, message)


# ---------------------------------------------------------------------------
# API routes (all under /api prefix)
# ---------------------------------------------------------------------------

api = APIRouter(prefix="/api")


@api.get("/health")
def health() -> Dict[str, Any]:
    return {"service": "cloudwire", "status": "ok"}


@api.get("/services")
def list_services() -> Dict[str, Any]:
    return get_services_payload()


@api.get("/graph", response_model=GraphResponse, responses={500: {"model": APIErrorResponse}})
def get_graph() -> Dict[str, Any]:
    return job_store.get_latest_graph_payload()


@api.get(
    "/resource/{resource_id:path}",
    response_model=ResourceResponse,
    responses={404: {"model": APIErrorResponse}, 500: {"model": APIErrorResponse}},
)
def get_resource(resource_id: str, job_id: Optional[str] = Query(default=None)) -> Dict[str, Any]:
    try:
        return job_store.get_resource_payload(resource_id, job_id=job_id)
    except KeyError as exc:
        raise APIError(
            status_code=status.HTTP_404_NOT_FOUND,
            code="resource_not_found",
            message="Resource was not found in the selected graph.",
        ) from exc


@api.post(
    "/scan",
    response_model=ScanJobCreateResponse,
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        401: {"model": APIErrorResponse},
        403: {"model": APIErrorResponse},
        422: {"model": APIErrorResponse},
        502: {"model": APIErrorResponse},
        500: {"model": APIErrorResponse},
    },
)
def create_scan_job(payload: ScanRequest) -> Dict[str, Any]:
    services = _normalize_services(payload.services)
    options = _resolve_scan_options(payload)
    account_id = _resolve_account_id(payload.region)

    tag_arns = payload.tag_arns

    cache_key = ScanJobStore.build_cache_key(
        account_id=account_id,
        region=payload.region,
        services=services,
        mode=options.mode,
        include_iam_inference=options.include_iam_inference,
        include_resource_describes=options.include_resource_describes,
        tag_arns=tag_arns,
    )
    reusable_job_id, cached = job_store.find_reusable_job(
        cache_key=cache_key,
        force_refresh=payload.force_refresh,
    )
    if reusable_job_id:
        status_payload = job_store.get_status_payload(reusable_job_id)
        return {
            "job_id": reusable_job_id,
            "status": status_payload["status"],
            "cached": cached,
            "status_url": f"/api/scan/{reusable_job_id}",
            "graph_url": f"/api/scan/{reusable_job_id}/graph",
        }

    job = job_store.create_job(
        cache_key=cache_key,
        account_id=account_id,
        region=payload.region,
        services=services,
        mode=options.mode,
        include_iam_inference=options.include_iam_inference,
        include_resource_describes=options.include_resource_describes,
    )
    # Capture tag_arns in local scope for the lambda closure
    _tag_arns = tag_arns
    job_store.submit_job(
        job.id,
        lambda: _run_scan_job(
            job_id=job.id,
            region=payload.region,
            services=services,
            account_id=account_id,
            options=options,
            tag_arns=_tag_arns,
        ),
    )
    return {
        "job_id": job.id,
        "status": job.status,
        "cached": False,
        "status_url": f"/api/scan/{job.id}",
        "graph_url": f"/api/scan/{job.id}/graph",
    }


@api.get(
    "/scan/{job_id}",
    response_model=ScanJobStatusResponse,
    responses={404: {"model": APIErrorResponse}, 500: {"model": APIErrorResponse}},
)
def get_scan_job(job_id: str) -> Dict[str, Any]:
    try:
        return job_store.get_status_payload(job_id)
    except KeyError as exc:
        raise APIError(
            status_code=status.HTTP_404_NOT_FOUND,
            code="job_not_found",
            message=f"Scan job '{job_id}' was not found.",
            details={"job_id": job_id},
        ) from exc


@api.get(
    "/scan/{job_id}/graph",
    response_model=GraphResponse,
    responses={404: {"model": APIErrorResponse}, 500: {"model": APIErrorResponse}},
)
def get_scan_job_graph(job_id: str) -> Dict[str, Any]:
    try:
        return job_store.get_graph_payload(job_id)
    except KeyError as exc:
        raise APIError(
            status_code=status.HTTP_404_NOT_FOUND,
            code="job_not_found",
            message=f"Scan job '{job_id}' was not found.",
            details={"job_id": job_id},
        ) from exc


@api.post(
    "/scan/{job_id}/stop",
    response_model=ScanJobStatusResponse,
    status_code=status.HTTP_202_ACCEPTED,
    responses={404: {"model": APIErrorResponse}, 500: {"model": APIErrorResponse}},
)
def stop_scan_job(job_id: str) -> Dict[str, Any]:
    try:
        job_store.request_cancel(job_id)
        return job_store.get_status_payload(job_id)
    except KeyError as exc:
        raise APIError(
            status_code=status.HTTP_404_NOT_FOUND,
            code="job_not_found",
            message=f"Scan job '{job_id}' was not found.",
            details={"job_id": job_id},
        ) from exc


# ---------------------------------------------------------------------------
# Tag discovery endpoints
# ---------------------------------------------------------------------------

def _handle_tagging_error(exc: Exception, region: str, operation: str):
    """Convert AWS errors from tagging API to APIError."""
    logger.warning("Tag API error in %s (region=%s): %s: %s", operation, region, type(exc).__name__, exc)
    if isinstance(exc, (NoCredentialsError, PartialCredentialsError, CredentialRetrievalError)):
        raise APIError(
            status_code=status.HTTP_401_UNAUTHORIZED,
            code="aws_credentials_missing",
            message=_friendly_exception_message(exc),
        ) from exc
    if isinstance(exc, ClientError):
        aws_code = exc.response.get("Error", {}).get("Code", "")
        if aws_code in ("AccessDenied", "AccessDeniedException", "UnauthorizedAccess", "UnauthorizedOperation"):
            raise APIError(
                status_code=status.HTTP_403_FORBIDDEN,
                code="tags_access_denied",
                message=f"Access denied for {operation}. Ensure the IAM role has tag:GetTagKeys, tag:GetTagValues, and tag:GetResources permissions.",
                details={"aws_error_code": aws_code, "region": region},
            ) from exc
        raise APIError(
            status_code=status.HTTP_502_BAD_GATEWAY,
            code="tags_api_error",
            message=f"AWS tagging API request failed ({aws_code or 'ClientError'}).",
            details={"aws_error_code": aws_code, "region": region},
        ) from exc
    if isinstance(exc, (EndpointConnectionError, ConnectTimeoutError, ReadTimeoutError)):
        raise APIError(
            status_code=status.HTTP_502_BAD_GATEWAY,
            code="aws_endpoint_unreachable",
            message=_friendly_exception_message(exc),
            details={"region": region},
        ) from exc
    if isinstance(exc, BotoCoreError):
        raise APIError(
            status_code=status.HTTP_502_BAD_GATEWAY,
            code="tags_api_error",
            message=_friendly_exception_message(exc),
            details={"region": region},
        ) from exc
    # Fallback for unexpected exception types
    raise APIError(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        code="unexpected_error",
        message=_friendly_exception_message(exc),
    ) from exc


@api.get(
    "/tags/keys",
    response_model=TagKeysResponse,
    responses={401: {"model": APIErrorResponse}, 403: {"model": APIErrorResponse}, 502: {"model": APIErrorResponse}},
)
def get_tag_keys(region: str = Query(default="us-east-1")) -> Dict[str, Any]:
    region = _validate_region(region)
    try:
        client = _tagging_client(region)
        keys = []
        paginator = client.get_paginator("get_tag_keys")
        for page in paginator.paginate(PaginationConfig={"MaxItems": 5000}):
            keys.extend(page.get("TagKeys", []))
        return {"keys": sorted(set(keys))}
    except Exception as exc:
        _handle_tagging_error(exc, region, "get_tag_keys")


@api.get(
    "/tags/values",
    response_model=TagValuesResponse,
    responses={401: {"model": APIErrorResponse}, 403: {"model": APIErrorResponse}, 502: {"model": APIErrorResponse}},
)
def get_tag_values(
    region: str = Query(default="us-east-1"),
    key: str = Query(..., min_length=1, max_length=128),
) -> Dict[str, Any]:
    region = _validate_region(region)
    try:
        client = _tagging_client(region)
        values = []
        paginator = client.get_paginator("get_tag_values")
        for page in paginator.paginate(Key=key, PaginationConfig={"MaxItems": 5000}):
            values.extend(page.get("TagValues", []))
        return {"key": key, "values": sorted(set(values))}
    except Exception as exc:
        _handle_tagging_error(exc, region, "get_tag_values")


@api.get(
    "/tags/resources",
    response_model=TagResourcesResponse,
    responses={401: {"model": APIErrorResponse}, 403: {"model": APIErrorResponse}, 502: {"model": APIErrorResponse}},
)
def get_tag_resources(
    region: str = Query(default="us-east-1"),
    tag_filters: str = Query(..., description="JSON array of {Key, Values} filter objects"),
) -> Dict[str, Any]:
    import json as _json

    region = _validate_region(region)

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
        client = _tagging_client(region)
        arns_set: set = set()
        arns: list = []
        paginator = client.get_paginator("get_resources")
        _MAX_DISCOVERED_RESOURCES = 5000
        for page in paginator.paginate(
            TagFilters=parsed_filters,
            ResourcesPerPage=100,
            PaginationConfig={"MaxItems": _MAX_DISCOVERED_RESOURCES},
        ):
            for entry in page.get("ResourceTagMappingList", []):
                arn = entry.get("ResourceARN")
                if arn and arn not in arns_set:
                    arns_set.add(arn)
                    arns.append(arn)

        # Global services (CloudFront, Route53, IAM) store tags in us-east-1.
        # Query us-east-1 as well when the selected region is different.
        if region != "us-east-1":
            try:
                global_client = _tagging_client("us-east-1")
                global_paginator = global_client.get_paginator("get_resources")
                for page in global_paginator.paginate(
                    TagFilters=parsed_filters,
                    ResourcesPerPage=100,
                    PaginationConfig={"MaxItems": _MAX_DISCOVERED_RESOURCES},
                ):
                    for entry in page.get("ResourceTagMappingList", []):
                        arn = entry.get("ResourceARN")
                        if arn and arn not in arns_set:
                            # Only include global services, not regional us-east-1 resources
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
        _handle_tagging_error(exc, region, "get_resources")


# ---------------------------------------------------------------------------
# Terraform state file parsing
# ---------------------------------------------------------------------------

# Simple in-memory rate limiter for terraform parse endpoint.
# Allows at most _TF_RATE_LIMIT requests per _TF_RATE_WINDOW seconds.
import time as _time
import threading as _threading

_TF_ALLOWED_EXTENSIONS = {".tfstate", ".json", ".tf"}
_TF_JOB_TTL_SECONDS = 1800
_TF_RATE_LIMIT = 10
_TF_RATE_WINDOW = 60  # seconds
_tf_rate_lock = _threading.Lock()
_tf_rate_timestamps: list = []


def _tf_rate_check() -> None:
    """Raise APIError(429) if the terraform parse rate limit is exceeded."""
    now = _time.monotonic()
    with _tf_rate_lock:
        # Prune expired entries
        cutoff = now - _TF_RATE_WINDOW
        while _tf_rate_timestamps and _tf_rate_timestamps[0] < cutoff:
            _tf_rate_timestamps.pop(0)
        if len(_tf_rate_timestamps) >= _TF_RATE_LIMIT:
            raise APIError(
                status_code=429,
                code="rate_limit_exceeded",
                message=f"Too many terraform parse requests. Limit is {_TF_RATE_LIMIT} per {_TF_RATE_WINDOW}s.",
            )
        _tf_rate_timestamps.append(now)


@api.post(
    "/terraform/parse",
    responses={400: {"model": APIErrorResponse}, 413: {"model": APIErrorResponse}, 429: {"model": APIErrorResponse}},
)
async def parse_terraform(
    files: List[UploadFile] = File(..., description="One or more .tfstate files"),
) -> Dict[str, Any]:
    """Parse uploaded Terraform state files and return the graph."""
    _tf_rate_check()
    if len(files) > _TF_MAX_FILES:
        raise APIError(
            status_code=status.HTTP_400_BAD_REQUEST,
            code="too_many_files",
            message=f"Maximum {_TF_MAX_FILES} files allowed, got {len(files)}.",
        )

    # Read and validate all files.
    # Extension check runs before read to fail fast on obviously wrong uploads.
    # Per-file size is enforced inside validate_tfstate_content; the cumulative
    # cap is enforced here to limit total in-process memory for all files combined.
    state_dicts: List[Dict[str, Any]] = []
    hcl_dicts: List[Dict[str, Any]] = []
    filenames: List[str] = []
    total_bytes = 0

    for upload in files:
        safe_name = Path(upload.filename or "unknown").name if upload.filename else "unknown"
        # Enforce extension before reading the body — avoids allocating memory for
        # obviously invalid uploads.
        if not any(safe_name.lower().endswith(ext) for ext in _TF_ALLOWED_EXTENSIONS):
            raise APIError(
                status_code=status.HTTP_400_BAD_REQUEST,
                code="invalid_file_type",
                message=f"File '{safe_name}' has an unsupported extension. Only .tf, .tfstate, and .json files are accepted.",
            )

        raw = await upload.read()
        total_bytes += len(raw)
        if total_bytes > _TF_MAX_TOTAL_BYTES:
            raise APIError(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                code="upload_too_large",
                message=f"Total upload size exceeds {_TF_MAX_TOTAL_BYTES // (1024 * 1024)} MB.",
            )

        try:
            if safe_name.lower().endswith(".tf"):
                from .hcl_parser import validate_hcl_content
                data = validate_hcl_content(raw, safe_name)
                hcl_dicts.append(data)
            else:
                data = validate_tfstate_content(raw, safe_name)
                state_dicts.append(data)
        except ValueError as exc:
            raise APIError(
                status_code=status.HTTP_400_BAD_REQUEST,
                code="invalid_file",
                message=str(exc),
            )
        filenames.append(safe_name)

    if not state_dicts and not hcl_dicts:
        raise APIError(
            status_code=status.HTTP_400_BAD_REQUEST,
            code="no_files",
            message="No valid Terraform files provided.",
        )

    # Parse into a unified graph — .tfstate first (has real ARNs), then .tf
    graph_store = GraphStore()
    all_warnings: List[str] = []

    if state_dicts:
        tf_parser = TerraformParser(graph_store)
        tf_summary = tf_parser.parse(state_dicts)
        all_warnings.extend(tf_summary["warnings"])

    if hcl_dicts:
        from .hcl_parser import HCLParser
        hcl_parser = HCLParser(graph_store)
        hcl_summary = hcl_parser.parse(hcl_dicts)
        all_warnings.extend(hcl_summary["warnings"])

    # Store as a synthetic completed job so fetchResource works.
    # The job is NOT registered as _latest_graph_id — terraform-parsed graphs
    # are session-local to this upload and must not overwrite the shared live
    # scan graph visible to all callers of GET /api/graph.
    # A short TTL (30 min) ensures in-memory cleanup via the normal prune path.
    job_id = str(uuid4())
    job = ScanJob(
        id=job_id,
        cache_key=f"terraform:{job_id}",
        account_id="terraform",
        region="terraform",
        services=[],
        mode="quick",
        include_iam_inference=False,
        include_resource_describes=False,
        status="completed",
        progress_percent=100,
        services_total=0,
        services_done=0,
        finished_at=datetime.now(timezone.utc).isoformat(),
        graph_store=graph_store,
    )

    job_store.register_external_job(job, ttl_seconds=_TF_JOB_TTL_SECONDS)

    graph_payload = graph_store.get_graph_payload()
    metadata = graph_payload.get("metadata", {})
    return {
        "job_id": job_id,
        "graph": graph_payload,
        "resource_count": metadata.get("node_count", 0),
        "edge_count": metadata.get("edge_count", 0),
        "file_count": len(filenames),
        "files": filenames,
        "warnings": all_warnings,
    }


app.include_router(api)

# ---------------------------------------------------------------------------
# Static file serving — must be registered AFTER all API routes
# ---------------------------------------------------------------------------

if _STATIC_DIR.is_dir() and ((_STATIC_DIR / "assets").is_dir()):
    app.mount("/assets", StaticFiles(directory=str(_STATIC_DIR / "assets")), name="assets")


@app.get("/{full_path:path}", include_in_schema=False)
def spa_fallback(full_path: str) -> FileResponse:
    index = _STATIC_DIR / "index.html"
    if not index.is_file():
        return JSONResponse(
            status_code=503,
            content=_error_payload(
                "frontend_not_built",
                "Frontend assets not found. Run `make build` to compile the UI.",
            ),
        )
    return FileResponse(str(index))
