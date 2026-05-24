"""MCP Streamable HTTP client for beads-mcp."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

import httpx

logger = logging.getLogger(__name__)


@dataclass
class MCPClient:
    """Async MCP client using Streamable HTTP transport."""

    endpoint: str
    user_id: str
    verify_ssl: bool = False
    _session_id: str | None = field(default=None, init=False, repr=False)
    _request_id: int = field(default=0, init=False, repr=False)
    _client: httpx.AsyncClient | None = field(default=None, init=False, repr=False)

    def _next_id(self) -> int:
        self._request_id += 1
        return self._request_id

    async def connect(self) -> None:
        """Initialize MCP session."""
        self._client = httpx.AsyncClient(
            base_url=self.endpoint,
            verify=self.verify_ssl,
            timeout=30.0,
            follow_redirects=True,
        )
        # Initialize
        resp = await self._post({
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": f"beads-e2e-{self.user_id}", "version": "0.1.0"},
            },
        })
        if "mcp-session-id" in resp.headers:
            self._session_id = resp.headers["mcp-session-id"]
        self._parse_response(resp)

        # Send initialized notification (no id = notification)
        await self._post({
            "jsonrpc": "2.0",
            "method": "notifications/initialized",
        })
        logger.info("MCP session initialized for %s (session=%s)", self.user_id, self._session_id)

    async def call_tool(self, tool_name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        """Call an MCP tool and return the result."""
        msg = {
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": "tools/call",
            "params": {
                "name": tool_name,
                "arguments": arguments or {},
            },
        }
        resp = await self._post(msg)
        return self._parse_response(resp)

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._client:
            await self._client.aclose()
            self._client = None

    async def _post(self, body: dict) -> httpx.Response:
        """Send a JSON-RPC message."""
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self._session_id:
            headers["mcp-session-id"] = self._session_id
        assert self._client is not None
        resp = await self._client.post("/mcp", json=body, headers=headers)
        resp.raise_for_status()
        return resp

    def _parse_response(self, resp: httpx.Response) -> dict[str, Any]:
        """Parse JSON or SSE response into JSON-RPC result."""
        content_type = resp.headers.get("content-type", "")
        if "text/event-stream" in content_type:
            # Parse SSE: look for data: lines containing JSON-RPC responses
            result = None
            for line in resp.text.splitlines():
                if line.startswith("data:"):
                    data = json.loads(line[5:].strip())
                    if "error" in data:
                        raise MCPError(data["error"].get("message", "Unknown error"), data["error"])
                    if "result" in data:
                        result = data.get("result", {})
                        break
            if result is not None:
                return result
            raise MCPError("No result in SSE stream")
        else:
            data = resp.json()
            if "error" in data:
                raise MCPError(data["error"].get("message", "Unknown error"), data["error"])
            return data.get("result", {})


class MCPError(Exception):
    """MCP tool call error."""

    def __init__(self, message: str, detail: dict[str, Any] | None = None):
        super().__init__(message)
        self.detail = detail
