"""Terraform file parsing API routes."""

from __future__ import annotations

import logging
import time as _time
import threading as _threading
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List
from uuid import uuid4

from fastapi import APIRouter, File, UploadFile, status

from ..errors import APIError
from ..graph_store import GraphStore
from ..models import APIErrorResponse
from ..scan_jobs import ScanJob, ScanJobStore
from ..terraform_parser import (
    MAX_FILES as _TF_MAX_FILES,
    MAX_TOTAL_BYTES as _TF_MAX_TOTAL_BYTES,
    TerraformParser,
    validate_tfstate_content,
)

logger = logging.getLogger(__name__)

_TF_ALLOWED_EXTENSIONS = {".tfstate", ".json", ".tf"}
_TF_JOB_TTL_SECONDS = 1800
_TF_RATE_LIMIT = 10
_TF_RATE_WINDOW = 60  # seconds
_tf_rate_lock = _threading.Lock()
_tf_rate_timestamps: deque = deque()


def _tf_rate_check() -> None:
    """Raise APIError(429) if the terraform parse rate limit is exceeded."""
    now = _time.monotonic()
    with _tf_rate_lock:
        cutoff = now - _TF_RATE_WINDOW
        while _tf_rate_timestamps and _tf_rate_timestamps[0] < cutoff:
            _tf_rate_timestamps.popleft()
        if len(_tf_rate_timestamps) >= _TF_RATE_LIMIT:
            raise APIError(
                status_code=429,
                code="rate_limit_exceeded",
                message=f"Too many terraform parse requests. Limit is {_TF_RATE_LIMIT} per {_TF_RATE_WINDOW}s.",
            )
        _tf_rate_timestamps.append(now)


def register_routes(api: APIRouter, job_store: ScanJobStore) -> None:
    """Register terraform parsing routes on the given router."""

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

        state_dicts: List[Dict[str, Any]] = []
        hcl_dicts: List[Dict[str, Any]] = []
        filenames: List[str] = []
        total_bytes = 0

        for upload in files:
            safe_name = Path(upload.filename or "unknown").name if upload.filename else "unknown"
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
                    from ..hcl_parser import validate_hcl_content
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
                ) from exc
            filenames.append(safe_name)

        if not state_dicts and not hcl_dicts:
            raise APIError(
                status_code=status.HTTP_400_BAD_REQUEST,
                code="no_files",
                message="No valid Terraform files provided.",
            )

        graph_store = GraphStore()
        all_warnings: List[str] = []

        if state_dicts:
            tf_parser = TerraformParser(graph_store)
            tf_summary = tf_parser.parse(state_dicts)
            all_warnings.extend(tf_summary["warnings"])

        if hcl_dicts:
            from ..hcl_parser import HCLParser
            hcl_parser = HCLParser(graph_store)
            hcl_summary = hcl_parser.parse(hcl_dicts)
            all_warnings.extend(hcl_summary["warnings"])

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
