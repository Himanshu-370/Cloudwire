"""Thread-safe TTL cache for cost data."""

from __future__ import annotations

import time
from dataclasses import dataclass
from threading import Lock
from typing import Any, Dict, Optional

from .ce_client import CostResult


@dataclass
class _CacheEntry:
    service_result: CostResult
    resource_result: CostResult
    expires_at: float  # monotonic time


class CostCache:
    _TTL_SECONDS = 3600  # 1 hour
    _ERROR_TTL_SECONDS = 120  # 2 minutes for error results

    def __init__(self) -> None:
        self._lock = Lock()
        self._store: Dict[str, _CacheEntry] = {}

    def _key(self, account_id: str, region: str) -> str:
        return f"{account_id}|{region}"

    def get(
        self, account_id: str, region: str
    ) -> Optional[tuple[CostResult, CostResult]]:
        key = self._key(account_id, region)
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            if time.monotonic() > entry.expires_at:
                del self._store[key]
                return None
            return entry.service_result, entry.resource_result

    def put(
        self,
        account_id: str,
        region: str,
        service_result: CostResult,
        resource_result: CostResult,
    ) -> None:
        key = self._key(account_id, region)
        has_error = bool(service_result.error or resource_result.error)
        ttl = self._ERROR_TTL_SECONDS if has_error else self._TTL_SECONDS
        with self._lock:
            self._store[key] = _CacheEntry(
                service_result=service_result,
                resource_result=resource_result,
                expires_at=time.monotonic() + ttl,
            )


# Module-level singleton — persists across scan jobs within the same server process
cost_cache = CostCache()
