"""
MCP wiring for the ClickHouse track compliance requirement.

The track rule states: "your project must actively use ClickHouse at runtime
via the official ClickHouse MCP server (mcp-clickhouse)."

This module provides a drop-in for qc/store.catalog() that routes through
the official mcp-clickhouse server (stdio transport) instead of calling
clickhouse-connect directly.

Integration:
    from qc.mcp_store import catalog_via_mcp
    rows = catalog_via_mcp()   # same shape as store.catalog()

The function also writes a transcript.json file next to it as evidence
of MCP usage for judge review.

Server requirement:
    pip install mcp-clickhouse   # Python 3.10+
    # env vars: CLICKHOUSE_HOST, CLICKHOUSE_PORT, CLICKHOUSE_USER,
    #           CLICKHOUSE_PASSWORD, CLICKHOUSE_SECURE (default false)
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

_CATALOG_SQL = (
    "SELECT title_id, title, last_run, failures_before, failures_after, cps_cues_before, cps_cues_after, short_cues_before, short_cues_after, verdict "
    "FROM deliverable.catalog_status ORDER BY failures_before DESC"
)

# Where the MCP call transcript is written as judge-visible evidence.
# Cloud Run's filesystem is read-only outside /tmp, so honour an override; locally
# it stays next to the code where it can be committed.
_TRANSCRIPT_PATH = Path(
    os.getenv("MCP_TRANSCRIPT_PATH", str(Path(__file__).parent / "mcp_transcript.json"))
)


def _server_env() -> dict[str, str]:
    host = os.getenv("CLICKHOUSE_HOST", "localhost")
    secure = os.getenv("CLICKHOUSE_SECURE", "false")
    default_port = "8443" if secure.lower() == "true" else "8123"
    return {
        "CLICKHOUSE_HOST": host,
        "CLICKHOUSE_PORT": os.getenv("CLICKHOUSE_PORT", default_port),
        "CLICKHOUSE_USER": os.getenv("CLICKHOUSE_USER", "default"),
        "CLICKHOUSE_PASSWORD": os.getenv("CLICKHOUSE_PASSWORD", ""),
        "CLICKHOUSE_SECURE": secure,
    }


def _find_server() -> str:
    """Locate the mcp-clickhouse executable.

    Checked in order: this project's own virtualenv (where `pip install mcp-clickhouse`
    puts it), then PATH, then the scratch probe venv, then ~/.local/bin. The venv is
    checked first because running under `.venv/bin/python` does not put `.venv/bin` on
    PATH, which is the common way this lookup silently fails.
    """
    here = Path(__file__).resolve()
    repo = here.parent.parent

    candidates = [
        Path(sys.executable).parent / "mcp-clickhouse",   # the interpreter's own venv
        repo / ".venv" / "bin" / "mcp-clickhouse",
        repo.parent.parent / "scratch" / "mcp-probe" / ".venv" / "bin" / "mcp-clickhouse",
        Path.home() / ".local" / "bin" / "mcp-clickhouse",
    ]
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)

    found = shutil.which("mcp-clickhouse")
    if found:
        return found

    # uvx can fetch and run the official server without a local install.
    if shutil.which("uvx"):
        return shutil.which("uvx")

    return "mcp-clickhouse"  # let subprocess raise a clear error


def _server_args() -> list[str]:
    """uvx needs the package name; a direct executable needs nothing."""
    cmd = _find_server()
    if Path(cmd).name == "uvx":
        return ["--from", "mcp-clickhouse", "mcp-clickhouse"]
    return []


async def _run_catalog_query() -> tuple[list[dict], list[dict]]:
    transcript: list[dict[str, Any]] = []

    params = StdioServerParameters(
        command=_find_server(),
        args=_server_args(),
        env=_server_env(),
    )

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            args = {"query": _CATALOG_SQL}
            result = await session.call_tool("run_query", args)

            text = _extract_text(result)
            transcript.append({
                "ts": datetime.now(timezone.utc).isoformat(),
                "tool": "run_query",
                "args": args,
                "result_preview": text[:2000],
            })

    rows = _parse(text)
    return rows, transcript


def _extract_text(result) -> str:
    if hasattr(result, "content"):
        return "\n".join(c.text for c in result.content if hasattr(c, "text"))
    return str(result)


def _parse(text: str) -> list[dict]:
    try:
        data = json.loads(text)
        if isinstance(data, dict) and "columns" in data and "rows" in data:
            cols = data["columns"]
            return [dict(zip(cols, row)) for row in data["rows"]]
    except (json.JSONDecodeError, TypeError):
        pass
    return []


def catalog_via_mcp() -> list[dict]:
    """
    Drop-in for store.catalog(). Queries ClickHouse through the official
    mcp-clickhouse MCP server (stdio transport).

    Returns list[dict] with keys:
        title_id, title, last_run, failures_before, failures_after,
        cps_cues_before, cps_cues_after, short_cues_before, short_cues_after,
        verdict
    """
    rows, transcript = asyncio.run(_run_catalog_query())
    _TRANSCRIPT_PATH.write_text(json.dumps(transcript, indent=2))
    return rows
