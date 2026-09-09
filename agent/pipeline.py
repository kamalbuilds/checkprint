"""The Checkprint agent: a deterministic, multi-step delivery QC pipeline.

The pipeline is an ADK graph. `agent/graph.py` holds the topology; this module is
the entry point the web layer calls, and the place where the two things that must
never be negotiable live: the model is required, and the numbers are ffmpeg's.

  ingest       archive.org fetch                                  deterministic
  scan_audio   ffmpeg ebur128, ten readings a second               deterministic
  scan_picture black / frozen / subtitle battery                   deterministic
  stage_before write both to ClickHouse                            deterministic
  window_scout writes its own SQL over the 100ms series                   MODEL
  confirm_..   re-derive every window from the table itself        deterministic
  planner      picks actions from a fixed whitelist                       MODEL
  remediate    ffmpeg: attenuate the located passages, normalise   deterministic
  verify       re-measure, and fail loudly if the fix did not land deterministic
  auditor      compare the two stages for collateral damage               MODEL
  report       assemble the operator note                          deterministic

The model never produces a number that reaches a verdict. It decides which
passages to look at and which repairs to run; ffmpeg produces every figure. That
is what makes each claim reproducible by somebody holding only the public file.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from agent import agents
from agent.agents import McpRequired  # noqa: F401  (re-exported: callers catch it)
from qc import measure as m, store  # noqa: F401  (re-exported for tests)

MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")


class GeminiRequired(RuntimeError):
    """Raised when the pipeline has no model. It does not degrade gracefully.

    There is deliberately no deterministic fallback. A fallback that produced the
    same plan shape would make the model decorative: you could delete Gemini and
    the product would behave identically. Which passages of a master are worth
    touching, in what order, and which need a person, is a judgement call, so it is
    the model's. Execution stays in ffmpeg.
    """


def model_available() -> bool:
    """True when Vertex AI or the Gemini API is configured in this process."""
    if os.getenv("GOOGLE_GENAI_USE_VERTEXAI", "").lower() == "true" \
            and os.getenv("GOOGLE_CLOUD_PROJECT"):
        return True
    return bool(os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY"))


def require_model() -> None:
    if not model_available():
        raise GeminiRequired(
            "Gemini credentials are required. Set GOOGLE_API_KEY, or "
            "GOOGLE_GENAI_USE_VERTEXAI=true with GOOGLE_CLOUD_PROJECT. Three of "
            "the graph's nodes are Gemini agents and there is no offline "
            "substitute for them by design."
        )


# --- step results ---------------------------------------------------------


@dataclass
class StepResult:
    step: str
    ok: bool
    summary: str
    data: dict = field(default_factory=dict)


@dataclass
class PipelineRun:
    title_id: str
    title: str
    run_id: uuid.UUID
    steps: list[StepResult] = field(default_factory=list)
    trace: list[dict] = field(default_factory=list)
    #: Which mcp-clickhouse binary answered, and the tools it offered, recorded at
    #: the start of the run. Evidence rather than assertion: the run could not have
    #: started without it.
    mcp: dict = field(default_factory=dict)

    def add(self, step: StepResult) -> StepResult:
        self.steps.append(step)
        return step

    def as_dict(self) -> dict:
        return {
            "run_id": str(self.run_id),
            "title_id": self.title_id,
            "title": self.title,
            "steps": [
                {"step": s.step, "ok": s.ok, "summary": s.summary, "data": s.data}
                for s in self.steps
            ],
            "trace": self.trace,
            "mcp": self.mcp,
        }


# --- driving the graph ----------------------------------------------------


def _refusal(response) -> str | None:
    """The guardrail's refusal text out of a tool response, if that is what it is.

    ADK wraps a tool result differently depending on the tool, so the payload has
    been seen both as `{"error": ...}` and as `{"result": {"error": ...}}`. Both
    shapes are read rather than one being assumed.
    """
    if not isinstance(response, dict):
        return None
    for candidate in (response, response.get("result")):
        if isinstance(candidate, dict):
            error = candidate.get("error")
            if isinstance(error, str) and error.startswith("refused"):
                return error
    return None


async def _drive(identifier: str, workdir: Path, seconds: int, max_bytes: int,
                 run_id: uuid.UUID, on_step: Callable[[list[dict]], None] | None):
    """Run the ADK workflow once and return (final state, agent trace)."""
    from google.adk.runners import InMemoryRunner
    from google.genai import types

    from agent import graph

    if on_step is not None:
        graph._PROGRESS[str(run_id)] = on_step

    workflow = graph.build_workflow()
    runner = InMemoryRunner(node=workflow, app_name="checkprint")
    session = await runner.session_service.create_session(
        app_name="checkprint",
        user_id="qc",
        state={
            "identifier": identifier,
            "title_id": identifier,
            "workdir": str(workdir),
            "seconds": seconds,
            "max_bytes": max_bytes,
            "run_id": str(run_id),
        },
    )

    trace: list[dict] = []
    message = types.Content(role="user",
                            parts=[types.Part(text=f"Run delivery QC on {identifier}.")])
    try:
        async for event in runner.run_async(user_id="qc", session_id=session.id,
                                            new_message=message):
            author = getattr(event, "author", "") or ""
            for call in (event.get_function_calls() or []):
                trace.append({"agent": author, "tool": call.name,
                              "args": dict(call.args or {})})
            # A refusal from the SELECT-only guardrail comes back as an ordinary
            # tool response, so without this it is invisible: a query the guard
            # blocked looks exactly like a query that legitimately found nothing.
            # "0 passages located" has to be distinguishable from "the tool layer
            # would not let me ask", or a guardrail bug reads as a clean master.
            for resp in (event.get_function_responses() or []):
                error = _refusal(getattr(resp, "response", None))
                if error:
                    trace.append({"agent": author, "tool": resp.name,
                                  "refused": error[:400]})
            content = getattr(event, "content", None)
            for part in (getattr(content, "parts", None) or []):
                text = getattr(part, "text", None)
                if text and text.strip() and author:
                    trace.append({"agent": author, "text": text.strip()[:1200]})
    finally:
        graph._PROGRESS.pop(str(run_id), None)

    final = await runner.session_service.get_session(
        app_name="checkprint", user_id="qc", session_id=session.id
    )
    return dict(final.state), trace


def run_pipeline(identifier: str, workdir: Path, seconds: int = 300,
                 max_bytes: int = 24_000_000, ch=None,
                 on_step: Callable[[list[dict]], None] | None = None) -> PipelineRun:
    """Full measure, locate, repair, re-verify, audit pass over one title."""
    require_model()
    mcp = asyncio.run(agents.probe_mcp())
    workdir.mkdir(parents=True, exist_ok=True)
    run_id = uuid.uuid4()

    state, trace = asyncio.run(
        _drive(identifier, workdir, seconds, max_bytes, run_id, on_step)
    )

    run = PipelineRun(title_id=identifier,
                      title=state.get("title") or identifier,
                      run_id=run_id,
                      trace=trace,
                      mcp=mcp)
    for s in state.get("steps") or []:
        run.add(StepResult(step=s["step"], ok=s["ok"], summary=s["summary"],
                           data=s.get("data") or {}))

    # A run that produced no verify step never proved anything, whatever else it
    # did. Say so rather than handing the UI a half-finished record that renders
    # as a completed pass.
    if not any(s.step == "verify" for s in run.steps):
        done = ", ".join(s.step for s in run.steps) or "nothing"
        raise RuntimeError(
            f"the graph stopped before verify; completed steps were {done}. "
            "No repair is reported without a re-measurement behind it."
        )
    return run
