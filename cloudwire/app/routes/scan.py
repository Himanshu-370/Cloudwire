"""Scan API routes — creating, polling, stopping scans."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Query, status

from ..aws_clients import resolve_account_id, tagging_client
from ..errors import APIError, friendly_exception_message
from ..graph_store import GraphStore
from ..models import (
    APIErrorResponse,
    GraphResponse,
    ResourceResponse,
    ScanJobCreateResponse,
    ScanJobStatusResponse,
    ScanRequest,
    normalize_service_name,
)
from ..scan_jobs import ScanJobStore, TooManyJobsError
from ..scanner import AWSGraphScanner, ScanCancelledError, ScanExecutionOptions
from ..scanners._utils import _service_from_arn
from ..services import get_services_payload

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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


def _services_from_tag_arns(tag_arns: List[str]) -> List[str]:
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
    graph_store: GraphStore,
    tag_arns: List[str],
    region: str,
) -> int:
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

    arn_tags: Dict[str, Dict[str, str]] = {}
    try:
        client = tagging_client(region)
        paginator = client.get_paginator("get_resources")
        for i in range(0, len(missing_arns), 100):
            batch = missing_arns[i:i + 100]
            for page in paginator.paginate(ResourceARNList=batch, ResourcesPerPage=100):
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
        resource_part = arn.split(":", 5)[-1] if len(arn.split(":")) >= 6 else arn
        label = resource_part.rsplit("/", 1)[-1] if "/" in resource_part else resource_part
        resource_type = ""
        if "/" in resource_part:
            resource_type = resource_part.split("/")[0]
        elif ":" in resource_part:
            resource_type = resource_part.split(":")[0]

        node_attrs: Dict[str, Any] = {
            "arn": arn, "label": label, "service": service,
            "type": resource_type or "resource", "region": region, "stub": True,
        }
        tags = arn_tags.get(arn)
        if tags:
            node_attrs["tags"] = tags
        graph_store.add_node(node_id, **node_attrs)
        seeded += 1
    return seeded


def _run_scan_job(
    *,
    job_store: ScanJobStore,
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

    services = list(services)
    if tag_arns:
        arn_services = _services_from_tag_arns(tag_arns)
        existing = set(services)
        for svc in arn_services:
            if svc not in existing:
                services.append(svc)
                existing.add(svc)
        job_store.update_services_total(job_id, len(services))

    scanner = AWSGraphScanner(job.graph_store, options=options)

    def on_progress(event: str, service: str, services_done: int, services_total: int) -> None:
        job_store.update_progress(
            job_id, event=event, current_service=service,
            services_done=services_done, services_total=services_total,
        )

    try:
        scanner.scan(
            region=region, services=services, account_id=account_id,
            progress_callback=on_progress,
            should_cancel=lambda: job_store.is_cancel_requested(job_id),
        )
        if job_store.is_cancel_requested(job_id):
            job_store.mark_cancelled(job_id)
            return
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
        message = friendly_exception_message(exc)
        job.graph_store.add_warning(f"scan failed: {message}")
        job_store.mark_failed(job_id, message)


# ---------------------------------------------------------------------------
# Route factory
# ---------------------------------------------------------------------------

def register_routes(api: APIRouter, job_store: ScanJobStore) -> None:
    """Register all scan-related routes on the given router."""

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
            401: {"model": APIErrorResponse}, 403: {"model": APIErrorResponse},
            422: {"model": APIErrorResponse}, 429: {"model": APIErrorResponse},
            502: {"model": APIErrorResponse}, 500: {"model": APIErrorResponse},
        },
    )
    def create_scan_job(payload: ScanRequest) -> Dict[str, Any]:
        services = _normalize_services(payload.services)
        options = _resolve_scan_options(payload)
        account_id = resolve_account_id(payload.region)
        tag_arns = payload.tag_arns

        cache_key = ScanJobStore.build_cache_key(
            account_id=account_id, region=payload.region, services=services,
            mode=options.mode, include_iam_inference=options.include_iam_inference,
            include_resource_describes=options.include_resource_describes, tag_arns=tag_arns,
        )
        reusable_job_id, cached = job_store.find_reusable_job(cache_key=cache_key, force_refresh=payload.force_refresh)
        if reusable_job_id:
            status_payload = job_store.get_status_payload(reusable_job_id)
            return {
                "job_id": reusable_job_id, "status": status_payload["status"], "cached": cached,
                "status_url": f"/api/scan/{reusable_job_id}", "graph_url": f"/api/scan/{reusable_job_id}/graph",
            }

        try:
            job = job_store.create_job(
                cache_key=cache_key, account_id=account_id, region=payload.region,
                services=services, mode=options.mode,
                include_iam_inference=options.include_iam_inference,
                include_resource_describes=options.include_resource_describes,
            )
        except TooManyJobsError as exc:
            raise APIError(status_code=status.HTTP_429_TOO_MANY_REQUESTS, code="too_many_scans", message=str(exc)) from exc

        _tag_arns = tag_arns
        job_store.submit_job(
            job.id,
            lambda: _run_scan_job(
                job_store=job_store, job_id=job.id, region=payload.region,
                services=services, account_id=account_id, options=options, tag_arns=_tag_arns,
            ),
        )
        return {
            "job_id": job.id, "status": job.status, "cached": False,
            "status_url": f"/api/scan/{job.id}", "graph_url": f"/api/scan/{job.id}/graph",
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
                status_code=status.HTTP_404_NOT_FOUND, code="job_not_found",
                message=f"Scan job '{job_id}' was not found.", details={"job_id": job_id},
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
                status_code=status.HTTP_404_NOT_FOUND, code="job_not_found",
                message=f"Scan job '{job_id}' was not found.", details={"job_id": job_id},
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
                status_code=status.HTTP_404_NOT_FOUND, code="job_not_found",
                message=f"Scan job '{job_id}' was not found.", details={"job_id": job_id},
            ) from exc
