from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional

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
from fastapi import APIRouter, FastAPI, HTTPException, Query, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

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
from .scan_jobs import ScanJobStore
from .scanner import AWSGraphScanner, ScanCancelledError, ScanExecutionOptions

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
        message = exc.response.get("Error", {}).get("Message")
        return message or f"AWS API request failed with {code or 'ClientError'}."
    if isinstance(exc, BotoCoreError):
        return "The AWS SDK failed to complete the request."
    return str(exc) or "Unexpected server error."


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
        # Post-scan ARN filtering for tag-based scans
        if tag_arns:
            allowed = set(tag_arns)
            removed = job.graph_store.filter_by_arns(allowed)
            if removed:
                job.graph_store.add_warning(f"Tag filter removed {removed} resource(s) not matching selected tags or their neighbors.")
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


@api.get("/graph", response_model=GraphResponse, responses={500: {"model": APIErrorResponse}})
def get_graph() -> Dict[str, Any]:
    return job_store.get_latest_graph_payload()


@api.get(
    "/resource/{resource_id}",
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
            message=f"Resource '{resource_id}' was not found in the selected graph.",
            details={"resource_id": resource_id, "job_id": job_id},
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
        aws_message = exc.response.get("Error", {}).get("Message", "")
        if aws_code in ("AccessDenied", "AccessDeniedException", "UnauthorizedAccess", "UnauthorizedOperation"):
            raise APIError(
                status_code=status.HTTP_403_FORBIDDEN,
                code="tags_access_denied",
                message=f"Access denied for {operation}. Ensure the IAM role has tag:GetTagKeys, tag:GetTagValues, and tag:GetResources permissions. ({aws_code}: {aws_message})",
                details={"aws_error_code": aws_code, "region": region},
            ) from exc
        raise APIError(
            status_code=status.HTTP_502_BAD_GATEWAY,
            code="tags_api_error",
            message=f"AWS tagging API error: {aws_code}: {aws_message}" if aws_message else _friendly_exception_message(exc),
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
        for page in paginator.paginate():
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
    key: str = Query(..., min_length=1),
) -> Dict[str, Any]:
    region = _validate_region(region)
    try:
        client = _tagging_client(region)
        values = []
        paginator = client.get_paginator("get_tag_values")
        for page in paginator.paginate(Key=key):
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

    try:
        parsed_filters = _json.loads(tag_filters)
        if not isinstance(parsed_filters, list):
            raise ValueError("tag_filters must be a JSON array")
        for i, entry in enumerate(parsed_filters):
            if not isinstance(entry, dict):
                raise ValueError(f"tag_filters[{i}] must be an object")
            if "Key" not in entry:
                raise ValueError(f"tag_filters[{i}] is missing required field 'Key'")
            if not isinstance(entry.get("Key"), str):
                raise ValueError(f"tag_filters[{i}].Key must be a string")
            if "Values" in entry and not isinstance(entry["Values"], list):
                raise ValueError(f"tag_filters[{i}].Values must be an array")
    except (ValueError, _json.JSONDecodeError) as exc:
        raise APIError(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            code="validation_error",
            message=f"Invalid tag_filters JSON: {exc}",
        ) from exc

    try:
        client = _tagging_client(region)
        arns = []
        paginator = client.get_paginator("get_resources")
        for page in paginator.paginate(
            TagFilters=parsed_filters,
            ResourcesPerPage=100,
        ):
            for entry in page.get("ResourceTagMappingList", []):
                arn = entry.get("ResourceARN")
                if arn:
                    arns.append(arn)

        services = sorted(s for s in set(_service_from_arn(arn) for arn in arns) if s)
        return {"arns": arns, "services": services}
    except Exception as exc:
        _handle_tagging_error(exc, region, "get_resources")


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
