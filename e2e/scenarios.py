"""Two-user concurrent simulation scenario for beads-acp."""

from __future__ import annotations

import asyncio
import logging
import random
import time
from typing import Any

from mcp_client import MCPClient, MCPError
from metrics import MetricCollector, CRUD_MAP

logger = logging.getLogger(__name__)


class Scenario:
    """Two-user development workflow simulation."""

    def __init__(
        self,
        client_a: MCPClient,
        client_b: MCPClient,
        collector: MetricCollector,
    ) -> None:
        self.client_a = client_a
        self.client_b = client_b
        self.collector = collector
        self._sync = {
            "bootstrap_done": asyncio.Event(),
            "triage_done": asyncio.Event(),
        }

    async def run(self) -> None:
        """Run the full simulation."""
        await asyncio.gather(
            self._user_a_flow(),
            self._user_b_flow(),
        )

    async def _call(
        self, client: MCPClient, tool: str, args: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Call a tool with metric recording and jitter."""
        await asyncio.sleep(random.uniform(0.5, 2.0))
        start = time.monotonic()
        try:
            result = await client.call_tool(tool, args)
            latency = (time.monotonic() - start) * 1000
            issue_id = None
            if isinstance(result, dict):
                content = result.get("content", [])
                if content and isinstance(content[0], dict):
                    text = content[0].get("text", "")
                    if text:
                        for word in text.split():
                            if "-" in word and len(word) < 20:
                                issue_id = word.strip(".,;:\"'()")
                                break
            await self.collector.record(
                user_id=client.user_id,
                tool_name=tool,
                latency_ms=latency,
                success=True,
                issue_id=issue_id,
            )
            logger.info("[%s] %s -> OK (%.0fms)", client.user_id, tool, latency)
            return result
        except (MCPError, Exception) as exc:
            latency = (time.monotonic() - start) * 1000
            await self.collector.record(
                user_id=client.user_id,
                tool_name=tool,
                latency_ms=latency,
                success=False,
                error=str(exc),
            )
            logger.warning("[%s] %s -> FAILED: %s (%.0fms)", client.user_id, tool, exc, latency)
            raise

    async def _user_a_flow(self) -> None:
        """User A: creates issues, works bugs, creates more issues."""
        c = self.client_a

        # Phase 1: Bootstrap — create 4 issues
        logger.info("[user-a] Phase 1: Bootstrap")
        await self._call(c, "stats")

        await self._call(c, "create", {
            "title": "Login page returns 500 on invalid email format",
            "type": "bug", "priority": "high",
        })
        await self._call(c, "create", {
            "title": "Add dark mode toggle to settings page",
            "type": "feature", "priority": "medium",
        })
        await self._call(c, "create", {
            "title": "Memory leak in WebSocket connection handler",
            "type": "bug", "priority": "critical",
        })
        await self._call(c, "create", {
            "title": "Export issues to CSV format",
            "type": "feature", "priority": "low",
        })

        result = await self._call(c, "list")
        self._sync["bootstrap_done"].set()

        # Phase 3: Development (waits for triage)
        await self._sync["triage_done"].wait()
        logger.info("[user-a] Phase 3: Development")

        await self._call(c, "create", {
            "title": "Typo in error message on signup page",
            "type": "bug", "priority": "low",
        })
        await self._call(c, "show", {"id": "3"})  # check memory leak progress
        await self._call(c, "ready")

        # Claim and work the login bug
        await self._call(c, "claim", {"id": "1"})
        await self._call(c, "update", {"id": "1", "status": "in_progress"})

        # Phase 4: Wrap-up
        logger.info("[user-a] Phase 4: Wrap-up")
        await self._call(c, "close", {"id": "1"})
        await self._call(c, "ready")
        await self._call(c, "list")
        await self._call(c, "reopen", {"id": "5"})
        await self._call(c, "stats")

    async def _user_b_flow(self) -> None:
        """User B: triages, claims critical bug, works features."""
        c = self.client_b

        # Phase 2: Triage (wait for bootstrap)
        await self._sync["bootstrap_done"].wait()
        logger.info("[user-b] Phase 2: Triage")

        await self._call(c, "list")
        await self._call(c, "show", {"id": "1"})
        await self._call(c, "show", {"id": "2"})
        await self._call(c, "show", {"id": "3"})
        await self._call(c, "show", {"id": "4"})

        # Claim the critical bug
        await self._call(c, "claim", {"id": "3"})
        await self._call(c, "update", {"id": "3", "description": "Investigating: appears to be unclosed connections in the pool"})
        await self._call(c, "ready")

        # Add dependency: CSV export depends on dark mode
        await self._call(c, "dep", {"id": "4", "depends_on": "2", "type": "blocks"})
        await self._call(c, "ready")  # verify CSV export no longer shows
        self._sync["triage_done"].set()

        # Phase 3: Development
        logger.info("[user-b] Phase 3: Development")
        await self._call(c, "update", {"id": "3", "status": "in_progress"})
        await self._call(c, "close", {"id": "3"})

        # Claim and work dark mode
        await self._call(c, "claim", {"id": "2"})
        await self._call(c, "update", {"id": "2", "status": "in_progress"})

        # Phase 4: Wrap-up
        logger.info("[user-b] Phase 4: Wrap-up")
        await self._call(c, "close", {"id": "2"})
        await self._call(c, "stats")
