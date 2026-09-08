"""The ADK layer: the QC supervisor agent, holding ClickHouse as a real tool.

Why this exists, and why it is not decoration.

`agent/pipeline.py` runs the deterministic spine (ingest -> measure -> classify ->
remediate -> verify -> report). That order is fixed on purpose: a delivery QC pass
is not something a model should be free to reorder, and ffmpeg, not Gemini, produces
every number that reaches a verdict.

But two questions in that spine are genuinely judgement, not arithmetic:

  1. Is this title's defect profile normal for this catalog, or is this master an
     outlier worth a human's attention before anyone spends a remediation pass on it?
  2. Given the catalog's history, is the repair we just made consistent with repairs
     that worked before?

Answering those requires querying the QC corpus, and the shape of the query depends
on what the previous answer was. That is an agent loop, so it is written as one:
a `google.adk` `LlmAgent` whose tools are the official `mcp-clickhouse` MCP server,
reached through ADK's `McpToolset`.

This is the difference between "our code runs a fixed SQL string and hands the rows
to a model" and "the model decides which questions to ask the database". The former
is compliant with the ClickHouse track rule; only the latter is what the rule is
actually for.

Deliberately NOT done here: the measurement inserts and the repair itself stay out of
the agent's hands. An agent that can rewrite a master is a worse product, not a
better demo.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

SUPERVISOR_INSTRUCTION = """You are a broadcast delivery QC supervisor reviewing one
film master against the rest of the catalog.

You have tools that query a ClickHouse database of QC measurements. The schema is
database `deliverable`:

  findings(run_id, title_id, title, run_at, stage, check, spec, measured, target,
           unit, passed, auto_fixable, detail)
      stage is 'before' or 'after' a remediation pass.
  loudness_samples(run_id, title_id, stage, t_seconds, momentary, short_term,
                   integrated, true_peak)
      one row per 100ms of audio.
  catalog_status  - a view: one verdict row per title, latest run only.

Method, in order:
  1. Call list_tables on the `deliverable` database first, so you are working from
     the real schema rather than from this description.
  2. Query catalog_status to learn what "normal" looks like across the catalog.
  3. Query findings for the title under review and compare it to that baseline.
  4. If, and only if, the loudness numbers look unusual, query loudness_samples to
     find where in the running time the problem sits.

Rules:
  - Never dispute a measured number. ffmpeg produced it; it is ground truth.
  - Never invent a number that is not in a query result. If you did not query it,
    say you did not.
  - Prefer one precise query to several vague ones, and say what each query was for.

Finish with a short operator note: is this master an outlier, where is its worst
moment, and is it safe to auto-remediate or does it want a human.
"""


def _server_env() -> dict[str, str]:
    """Credentials for the MCP server, from this process's environment."""
    env = os.environ.copy()
    env.setdefault("CLICKHOUSE_HOST", os.getenv("CLICKHOUSE_HOST", "localhost"))
    env.setdefault("CLICKHOUSE_PORT", os.getenv("CLICKHOUSE_PORT", "8123"))
    env.setdefault("CLICKHOUSE_USER", os.getenv("CLICKHOUSE_USER", "default"))
    env.setdefault("CLICKHOUSE_PASSWORD", os.getenv("CLICKHOUSE_PASSWORD", ""))
    env.setdefault("CLICKHOUSE_SECURE", os.getenv("CLICKHOUSE_SECURE", "false"))
    return env


def _stdio_params():
    """Locate the official mcp-clickhouse server.

    Same lookup order as qc/mcp_store.py: the running interpreter's own venv first,
    because running under .venv/bin/python does not put .venv/bin on PATH, which is
    the usual way this silently fails.
    """
    from mcp import StdioServerParameters

    candidates = [
        Path(sys.executable).parent / "mcp-clickhouse",
        Path(__file__).resolve().parent.parent / ".venv" / "bin" / "mcp-clickhouse",
    ]
    for candidate in candidates:
        if candidate.exists():
            return StdioServerParameters(command=str(candidate), args=[], env=_server_env())

    found = shutil.which("mcp-clickhouse")
    if found:
        return StdioServerParameters(command=found, args=[], env=_server_env())

    # mcp-clickhouse ships no __main__, so call the published entrypoint directly.
    return StdioServerParameters(
        command=sys.executable,
        args=["-c", "from mcp_clickhouse.main import main; main()"],
        env=_server_env(),
    )


def clickhouse_toolset():
    """The official mcp-clickhouse server, as an ADK toolset.

    Built fresh per agent rather than cached in a module global: the toolset owns a
    subprocess and an async context, and sharing one across event loops is how this
    deadlocks under a threaded web server.
    """
    from google.adk.tools.mcp_tool import StdioConnectionParams
    from google.adk.tools.mcp_tool.mcp_toolset import McpToolset

    return McpToolset(
        connection_params=StdioConnectionParams(server_params=_stdio_params(), timeout=60)
    )


def supervisor_agent():
    """The ADK agent that reviews one master against the catalog."""
    from google.adk.agents import LlmAgent

    return LlmAgent(
        name="qc_supervisor",
        model=MODEL,
        instruction=SUPERVISOR_INSTRUCTION,
        tools=[clickhouse_toolset()],
    )


async def review_title(title_id: str, timeout_s: float = 90.0) -> dict:
    """Run the supervisor over one title. Returns its note plus the tools it called.

    The tool-call list is returned, not just the prose, so the UI can show which
    ClickHouse queries the agent actually chose to run. An agent's reasoning is only
    trustworthy if the queries behind it are visible.
    """
    import asyncio

    from google.adk.runners import InMemoryRunner
    from google.genai import types

    runner = InMemoryRunner(agent=supervisor_agent(), app_name="deliverable")
    session = await runner.session_service.create_session(
        app_name="deliverable", user_id="qc"
    )

    message = types.Content(
        role="user",
        parts=[types.Part(text=f"Review title_id '{title_id}' against the catalog.")],
    )

    note_parts: list[str] = []
    tool_calls: list[dict] = []

    async def _drive() -> None:
        async for event in runner.run_async(
            user_id="qc", session_id=session.id, new_message=message
        ):
            content = getattr(event, "content", None)
            if not content or not getattr(content, "parts", None):
                continue
            for part in content.parts:
                call = getattr(part, "function_call", None)
                if call is not None:
                    tool_calls.append({"tool": call.name, "args": dict(call.args or {})})
                text = getattr(part, "text", None)
                if text:
                    note_parts.append(text)

    try:
        await asyncio.wait_for(_drive(), timeout=timeout_s)
        timed_out = False
    except asyncio.TimeoutError:
        # A slow agent must not take the request down. Report what it managed.
        timed_out = True

    return {
        "title_id": title_id,
        "note": "\n".join(p.strip() for p in note_parts if p.strip()),
        "tool_calls": tool_calls,
        "timed_out": timed_out,
        "model": MODEL,
        "framework": "google-adk",
    }
