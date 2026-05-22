"""CRUD metric tracking for beads operations."""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import pandas as pd


CRUD_MAP: dict[str, str] = {
    "create": "C",
    "list": "R",
    "show": "R",
    "ready": "R",
    "stats": "R",
    "update": "U",
    "claim": "U",
    "dep": "U",
    "close": "U",
    "reopen": "U",
}


@dataclass
class MetricEvent:
    """A single recorded beads operation."""

    timestamp: datetime
    user_id: str
    tool_name: str
    crud_category: str
    latency_ms: float
    success: bool
    issue_id: str | None = None
    error: str | None = None


class MetricCollector:
    """Thread-safe collector for MetricEvents with aggregation methods."""

    def __init__(self) -> None:
        self._events: list[MetricEvent] = []
        self._lock = asyncio.Lock()
        self._start_time: float = time.monotonic()

    async def record(
        self,
        user_id: str,
        tool_name: str,
        latency_ms: float,
        success: bool,
        issue_id: str | None = None,
        error: str | None = None,
    ) -> None:
        """Record a metric event."""
        event = MetricEvent(
            timestamp=datetime.now(timezone.utc),
            user_id=user_id,
            tool_name=tool_name,
            crud_category=CRUD_MAP.get(tool_name, "?"),
            latency_ms=latency_ms,
            success=success,
            issue_id=issue_id,
            error=error,
        )
        async with self._lock:
            self._events.append(event)

    @property
    def events(self) -> list[MetricEvent]:
        return list(self._events)

    @property
    def elapsed_seconds(self) -> float:
        return time.monotonic() - self._start_time

    def count_by_crud(self) -> dict[str, int]:
        counts: dict[str, int] = {"C": 0, "R": 0, "U": 0, "D": 0}
        for e in self._events:
            if e.crud_category in counts:
                counts[e.crud_category] += 1
        return counts

    def count_by_user(self) -> dict[str, dict[str, int]]:
        result: dict[str, dict[str, int]] = defaultdict(lambda: {"C": 0, "R": 0, "U": 0, "D": 0})
        for e in self._events:
            if e.crud_category in result[e.user_id]:
                result[e.user_id][e.crud_category] += 1
        return dict(result)

    def count_by_tool(self) -> dict[str, int]:
        counts: dict[str, int] = defaultdict(int)
        for e in self._events:
            counts[e.tool_name] += 1
        return dict(counts)

    def latency_percentiles(self) -> dict[str, float]:
        if not self._events:
            return {"p50": 0, "p95": 0, "p99": 0}
        latencies = sorted(e.latency_ms for e in self._events)
        n = len(latencies)
        return {
            "p50": latencies[int(n * 0.5)],
            "p95": latencies[min(int(n * 0.95), n - 1)],
            "p99": latencies[min(int(n * 0.99), n - 1)],
        }

    def success_rate(self) -> float:
        if not self._events:
            return 1.0
        return sum(1 for e in self._events if e.success) / len(self._events)

    def error_count(self) -> int:
        return sum(1 for e in self._events if not e.success)

    def recent(self, n: int = 10) -> list[MetricEvent]:
        return self._events[-n:]

    def to_dataframe(self) -> pd.DataFrame:
        if not self._events:
            return pd.DataFrame()
        return pd.DataFrame([
            {
                "timestamp": e.timestamp,
                "user_id": e.user_id,
                "tool_name": e.tool_name,
                "crud": e.crud_category,
                "latency_ms": e.latency_ms,
                "success": e.success,
                "issue_id": e.issue_id,
                "error": e.error,
            }
            for e in self._events
        ])
