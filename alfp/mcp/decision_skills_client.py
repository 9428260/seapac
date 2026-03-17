"""
Client wrappers for ALFP decision MCP skills.
"""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path
from typing import Any

import anyio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


_SERVER_SCRIPT = Path(__file__).resolve().parent / "decision_skills_server.py"


async def _call_tool_async(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    server = StdioServerParameters(command="python3", args=[str(_SERVER_SCRIPT)])
    async with stdio_client(server) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream, read_timeout_seconds=timedelta(seconds=60)) as session:
            await session.initialize()
            result = await session.call_tool(name, arguments=arguments)
            if getattr(result, "structuredContent", None):
                return dict(result.structuredContent)
            if getattr(result, "content", None):
                texts = [item.text for item in result.content if getattr(item, "text", None)]
                if texts:
                    return json.loads("\n".join(texts))
            raise RuntimeError(f"MCP tool {name} returned no structured content")


def call_decision_skill(name: str, **arguments: Any) -> dict[str, Any]:
    return anyio.run(_call_tool_async, name, arguments)
