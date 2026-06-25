"""API request tracker for Gardena Smart System.

Persists request counts across HA restarts using hass.helpers.storage
so the weekly quota display stays accurate even after a restart.
"""
from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Deque

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_STORAGE_KEY = "gardena_smart_system_api_tracker"
_STORAGE_VERSION = 1


@dataclass
class APIRequestRecord:
    """A single API request record."""

    timestamp: float
    method: str
    endpoint: str
    status_code: int | None = None
    source: str = ""


class APIRequestTracker:
    """Tracks API requests for diagnostics and quota monitoring.

    In-memory history is kept for the current session (up to max_history
    entries).  On top of that, a persistent weekly counter is stored via
    hass.helpers.storage so it survives HA restarts.  The persistent store
    records (timestamp, method, endpoint) tuples for the last 7 days and is
    pruned on every write.
    """

    def __init__(self, max_history: int = 200) -> None:
        self._history: Deque[APIRequestRecord] = deque(maxlen=max_history)
        self._persistent_records: list[dict] = []   # loaded from storage
        self._store = None                           # set by async_load()

    # ------------------------------------------------------------------
    # Persistence — call once at coordinator startup
    # ------------------------------------------------------------------

    async def async_load(self, hass: "HomeAssistant") -> None:
        """Load persisted records from storage."""
        from homeassistant.helpers.storage import Store
        self._store = Store(hass, _STORAGE_VERSION, _STORAGE_KEY)
        data = await self._store.async_load()
        if data and isinstance(data.get("records"), list):
            cutoff = time.time() - 7 * 86400
            self._persistent_records = [
                r for r in data["records"] if r.get("timestamp", 0) >= cutoff
            ]

    async def _async_save(self) -> None:
        """Prune old records and persist to storage."""
        if not self._store:
            return
        cutoff = time.time() - 7 * 86400
        self._persistent_records = [
            r for r in self._persistent_records if r.get("timestamp", 0) >= cutoff
        ]
        await self._store.async_save({"records": self._persistent_records})

    # ------------------------------------------------------------------
    # Recording
    # ------------------------------------------------------------------

    def record(
        self,
        method: str,
        endpoint: str,
        status_code: int | None = None,
        source: str = "",
    ) -> None:
        """Record an API request (in-memory + persistent)."""
        now = time.time()
        self._history.append(
            APIRequestRecord(
                timestamp=now,
                method=method,
                endpoint=endpoint,
                status_code=status_code,
                source=source,
            )
        )
        self._persistent_records.append(
            {
                "timestamp": now,
                "method": method,
                "endpoint": endpoint,
                "status_code": status_code,
                "source": source,
            }
        )
        # Fire-and-forget save — schedule on the event loop if available
        if self._store:
            import asyncio
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    loop.create_task(self._async_save())
            except RuntimeError:
                pass

    # ------------------------------------------------------------------
    # Stats — use persistent records for cross-restart accuracy
    # ------------------------------------------------------------------

    def _week_records(self) -> list[dict]:
        cutoff = time.time() - 7 * 86400
        return [r for r in self._persistent_records if r.get("timestamp", 0) >= cutoff]

    def _day_records(self) -> list[dict]:
        cutoff = time.time() - 86400
        return [r for r in self._persistent_records if r.get("timestamp", 0) >= cutoff]

    @property
    def requests_this_week(self) -> int:
        """Count requests in the last 7 days (persists across restarts)."""
        return len(self._week_records())

    @property
    def requests_today(self) -> int:
        """Count requests in the last 24 hours (persists across restarts)."""
        return len(self._day_records())

    @property
    def total_requests(self) -> int:
        """Total requests in in-memory history (current session only)."""
        return len(self._history)

    @property
    def recent_requests(self) -> list[dict]:
        """Return the last 50 requests as dicts (most recent first)."""
        records = list(self._history)[-50:]
        records.reverse()
        return [
            {
                "timestamp": r.timestamp,
                "method": r.method,
                "endpoint": r.endpoint,
                "status_code": r.status_code,
                "source": r.source,
            }
            for r in records
        ]

    def requests_by_endpoint(self) -> dict[str, int]:
        """Count requests per endpoint in the last 7 days."""
        counts: dict[str, int] = {}
        for r in self._week_records():
            key = f"{r['method']} {r['endpoint']}"
            counts[key] = counts.get(key, 0) + 1
        return counts