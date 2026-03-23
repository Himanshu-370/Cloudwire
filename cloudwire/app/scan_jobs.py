from __future__ import annotations

import hashlib
import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from threading import Lock
from typing import Any, Callable, Dict, List, Optional
from uuid import uuid4

logger = logging.getLogger(__name__)

from .graph_store import GraphStore
from .models import JobStatus, ScanMode

_MAX_RETAINED_TERMINAL_JOBS = 50
_MAX_IN_FLIGHT_JOBS = 8


class TooManyJobsError(Exception):
    """Raised when the in-flight job limit is exceeded."""
    pass


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _progress_percent(done: int, total: int) -> int:
    if total <= 0:
        return 0
    return max(0, min(100, int((done / total) * 100)))


@dataclass
class CacheEntry:
    job_id: str
    expires_at: datetime


@dataclass
class ScanJob:
    id: str
    cache_key: str
    account_id: str
    region: str
    services: List[str]
    mode: ScanMode
    include_iam_inference: bool
    include_resource_describes: bool
    status: JobStatus = "queued"
    progress_percent: int = 0
    current_service: Optional[str] = None
    services_done: int = 0
    services_total: int = 0
    created_at: str = field(default_factory=_utc_now_iso)
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    error: Optional[str] = None
    cancellation_requested: bool = False
    active_services: List[str] = field(default_factory=list)
    graph_store: GraphStore = field(default_factory=GraphStore)


class ScanJobStore:
    def __init__(self, *, max_workers: int = 4) -> None:
        self._jobs: Dict[str, ScanJob] = {}
        self._in_flight: Dict[str, str] = {}
        self._cache: Dict[str, CacheEntry] = {}
        self._latest_graph_id: Optional[str] = None
        self._lock = Lock()
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="scan-job")

    def shutdown(self) -> None:
        # Request cancellation for all active jobs before shutting down
        with self._lock:
            for job in self._jobs.values():
                if job.status in {"queued", "running"}:
                    job.cancellation_requested = True
        self._executor.shutdown(wait=True, cancel_futures=True)

    def _prune_terminal_jobs_locked(self) -> None:
        terminal_states = {"completed", "failed", "cancelled"}
        terminal = [job for job in self._jobs.values() if job.status in terminal_states]
        if len(terminal) <= _MAX_RETAINED_TERMINAL_JOBS:
            return
        terminal.sort(key=lambda j: j.finished_at or "", reverse=True)
        for job in terminal[_MAX_RETAINED_TERMINAL_JOBS:]:
            if self._latest_graph_id == job.id:
                continue
            cached = self._cache.get(job.cache_key)
            if cached and cached.job_id == job.id:
                continue
            self._jobs.pop(job.id, None)

    def _prune_expired_cache_locked(self) -> None:
        now = datetime.now(timezone.utc)
        expired_keys = [key for key, value in self._cache.items() if value.expires_at <= now]
        for key in expired_keys:
            self._cache.pop(key, None)

    def register_external_job(self, job: ScanJob, *, ttl_seconds: int = 1800) -> None:
        """Register a pre-completed job built outside the normal scan pipeline (e.g. Terraform parse)."""
        with self._lock:
            self._jobs[job.id] = job
            self._cache[job.cache_key] = CacheEntry(
                job_id=job.id,
                expires_at=datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds),
            )
            # Prune both expired cache entries and stale terminal jobs so that
            # repeated Terraform parses do not accumulate orphaned cache keys or
            # job objects between live-scan runs (which is the only other path
            # that calls _prune_expired_cache_locked).
            self._prune_expired_cache_locked()
            self._prune_terminal_jobs_locked()

    def find_reusable_job(self, *, cache_key: str, force_refresh: bool) -> tuple[Optional[str], bool]:
        if force_refresh:
            return None, False

        with self._lock:
            self._prune_expired_cache_locked()

            in_flight_id = self._in_flight.get(cache_key)
            if in_flight_id:
                job = self._jobs.get(in_flight_id)
                if job and job.status in {"queued", "running"}:
                    return in_flight_id, False
                self._in_flight.pop(cache_key, None)

            cached = self._cache.get(cache_key)
            if cached and cached.job_id in self._jobs:
                return cached.job_id, True

            return None, False

    def _count_in_flight_locked(self) -> int:
        """Count jobs that are queued or running. Must be called under self._lock."""
        return sum(1 for job in self._jobs.values() if job.status in {"queued", "running"})

    def create_job(
        self,
        *,
        cache_key: str,
        account_id: str,
        region: str,
        services: List[str],
        mode: ScanMode,
        include_iam_inference: bool,
        include_resource_describes: bool,
    ) -> ScanJob:
        job_id = str(uuid4())
        job = ScanJob(
            id=job_id,
            cache_key=cache_key,
            account_id=account_id,
            region=region,
            services=services,
            mode=mode,
            include_iam_inference=include_iam_inference,
            include_resource_describes=include_resource_describes,
            services_total=len(services),
        )
        with self._lock:
            if self._count_in_flight_locked() >= _MAX_IN_FLIGHT_JOBS:
                raise TooManyJobsError(
                    f"Too many concurrent scan jobs (limit {_MAX_IN_FLIGHT_JOBS}). "
                    "Wait for a running scan to finish or cancel one."
                )
            self._jobs[job_id] = job
            self._in_flight[cache_key] = job_id
            self._prune_terminal_jobs_locked()
        return job

    def submit_job(self, job_id: str, runner: Callable[[], None]) -> None:
        self._executor.submit(self._run_job_wrapper, job_id, runner)

    def _run_job_wrapper(self, job_id: str, runner: Callable[[], None]) -> None:
        try:
            runner()
        except Exception as exc:
            logger.exception("Unhandled exception in scan job %s", job_id)
            self.mark_failed(job_id, f"Unhandled scan failure: {type(exc).__name__} - {exc}")

    def mark_running(self, job_id: str) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                logger.error("mark_running called for unknown job %s", job_id)
                return
            if job.status != "queued":
                return
            if job.cancellation_requested:
                job.status = "cancelled"
                job.error = "Cancelled by user"
                job.finished_at = _utc_now_iso()
                self._in_flight.pop(job.cache_key, None)
                return
            job.status = "running"
            job.started_at = _utc_now_iso()

    def _refresh_current_service(self, job: ScanJob) -> None:
        active = sorted(set(job.active_services))
        if not active:
            job.current_service = "stop requested" if job.cancellation_requested and job.status in {"queued", "running"} else None
            return
        if len(active) == 1:
            label = active[0]
        elif len(active) == 2:
            label = ", ".join(active)
        else:
            preview = ", ".join(active[:2])
            label = f"{len(active)} active ({preview}...)"
        job.current_service = f"{label} | stop requested" if job.cancellation_requested else label

    def update_progress(
        self,
        job_id: str,
        *,
        event: str,
        current_service: Optional[str],
        services_done: int,
        services_total: int,
    ) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                logger.error("update_progress called for unknown job %s", job_id)
                return
            if job.status not in {"queued", "running"}:
                return
            if current_service:
                if event == "start":
                    if current_service not in job.active_services:
                        job.active_services.append(current_service)
                elif event == "finish":
                    job.active_services = [service for service in job.active_services if service != current_service]
            job.services_done = services_done
            job.services_total = services_total
            job.progress_percent = _progress_percent(services_done, services_total)
            if job.status == "queued":
                job.status = "running"
                job.started_at = _utc_now_iso()
            self._refresh_current_service(job)

    def mark_completed(self, job_id: str, *, ttl_seconds: int) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                logger.error("mark_completed called for unknown job %s", job_id)
                return
            if job.status not in {"queued", "running"}:
                return
            job.status = "completed"
            job.progress_percent = 100
            job.current_service = None
            job.services_done = job.services_total
            job.finished_at = _utc_now_iso()
            job.active_services = []
            self._latest_graph_id = job_id

            self._in_flight.pop(job.cache_key, None)
            self._cache[job.cache_key] = CacheEntry(
                job_id=job_id,
                expires_at=datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds),
            )

    def mark_failed(self, job_id: str, error: str) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                logger.error("mark_failed called for unknown job %s (error: %s)", job_id, error)
                return
            if job.status not in {"queued", "running"}:
                return
            job.status = "failed"
            job.error = error
            job.current_service = None
            job.active_services = []
            job.finished_at = _utc_now_iso()
            self._in_flight.pop(job.cache_key, None)

    def request_cancel(self, job_id: str) -> bool:
        with self._lock:
            if job_id not in self._jobs:
                raise KeyError(job_id)
            job = self._jobs[job_id]
            if job.status not in {"queued", "running"}:
                return False
            job.cancellation_requested = True
            if job.status == "queued":
                job.status = "cancelled"
                job.error = "Cancelled by user"
                job.current_service = None
                job.active_services = []
                job.finished_at = _utc_now_iso()
                self._in_flight.pop(job.cache_key, None)
                return True
            self._refresh_current_service(job)
            return True

    def is_cancel_requested(self, job_id: str) -> bool:
        with self._lock:
            if job_id not in self._jobs:
                return False
            return self._jobs[job_id].cancellation_requested

    def mark_cancelled(self, job_id: str, reason: str = "Cancelled by user") -> None:
        with self._lock:
            if job_id not in self._jobs:
                return
            job = self._jobs[job_id]
            if job.status not in {"queued", "running", "cancelled"}:
                return
            job.cancellation_requested = True
            job.status = "cancelled"
            job.error = reason
            job.current_service = None
            job.active_services = []
            job.finished_at = _utc_now_iso()
            self._in_flight.pop(job.cache_key, None)

    def update_services_total(self, job_id: str, total: int) -> None:
        """Thread-safe update of a job's services_total count."""
        with self._lock:
            job = self._jobs.get(job_id)
            if job is not None:
                job.services_total = total

    def get_job(self, job_id: str) -> ScanJob:
        with self._lock:
            if job_id not in self._jobs:
                raise KeyError(job_id)
            return self._jobs[job_id]

    def get_status_payload(self, job_id: str) -> Dict[str, Any]:
        with self._lock:
            if job_id not in self._jobs:
                raise KeyError(job_id)
            job = self._jobs[job_id]
            job_snapshot = {
                "job_id": job.id,
                "status": job.status,
                "cancellation_requested": job.cancellation_requested,
                "mode": job.mode,
                "region": job.region,
                "services": list(job.services),
                "progress_percent": job.progress_percent,
                "current_service": job.current_service,
                "services_done": job.services_done,
                "services_total": job.services_total,
                "created_at": job.created_at,
                "started_at": job.started_at,
                "finished_at": job.finished_at,
                "error": job.error,
            }
            graph_store = job.graph_store  # stable reference, set at construction and never reassigned
        # Called outside the job store lock to avoid holding it during serialization.
        # Thread-safe because GraphStore has its own internal lock.
        graph_payload = graph_store.get_graph_payload()
        metadata = graph_payload.get("metadata", {})
        return {
            **job_snapshot,
            "node_count": metadata.get("node_count", 0),
            "edge_count": metadata.get("edge_count", 0),
            "warnings": metadata.get("warnings", []),
        }

    def get_graph_payload(self, job_id: str) -> Dict[str, Any]:
        job = self.get_job(job_id)
        return job.graph_store.get_graph_payload()

    def get_latest_graph_payload(self) -> Dict[str, Any]:
        with self._lock:
            latest_id = self._latest_graph_id
        if not latest_id:
            return GraphStore().get_graph_payload()
        return self.get_graph_payload(latest_id)

    def get_resource_payload(self, resource_id: str, job_id: Optional[str] = None) -> Dict[str, Any]:
        if job_id:
            job = self.get_job(job_id)
            return job.graph_store.get_resource_payload(resource_id)

        with self._lock:
            latest_id = self._latest_graph_id
        if not latest_id:
            raise KeyError(resource_id)
        job = self.get_job(latest_id)
        return job.graph_store.get_resource_payload(resource_id)

    @staticmethod
    def build_cache_key(
        *,
        account_id: str,
        region: str,
        services: List[str],
        mode: ScanMode,
        include_iam_inference: bool,
        include_resource_describes: bool,
        tag_arns: Optional[List[str]] = None,
    ) -> str:
        ordered_services = ",".join(sorted(services))
        parts = [
            account_id,
            region,
            ordered_services,
            mode,
            f"iam={int(include_iam_inference)}",
            f"describe={int(include_resource_describes)}",
        ]
        if tag_arns:
            arns_str = ",".join(sorted(tag_arns))
            parts.append(f"tags={hashlib.sha256(arns_str.encode()).hexdigest()[:16]}")
        return "|".join(parts)
