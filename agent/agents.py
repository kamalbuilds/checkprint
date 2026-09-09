"""The ADK agents. Three of them, and each one changes the output file or the verdict.

The rule this module is built to obey: an agent that only writes nicer prose is a
wrapper, not an agent. So there are exactly three, and removing any one of them
changes something a judge can measure, not something on a diagram.

  window_scout        Writes its own SQL, through the official mcp-clickhouse MCP
                      server, over the 100ms loudness series, and returns the
                      passages of this master that fail. Those passages are the
                      ONLY seconds of audio the remediator is allowed to touch.
                      Delete it and the repair is a single gain over the whole
                      programme: a different WAV, not a different diagram.

  repair_planner      Turns findings plus located windows into an ordered list of
                      actions drawn from a fixed whitelist. It holds no tools and
                      cannot run SQL, so it cannot invent a passage. Delete it and
                      nothing is planned, the remediate step no-ops, and the
                      pipeline raises rather than reporting an unchanged file as a
                      completed repair.

  regression_auditor  After the repair has been re-measured and written back, this
                      one queries ClickHouse again and compares the before stage
                      against the after stage on the same sample grid. It exists to
                      catch the damage the deterministic check cannot see: the
                      pipeline's own verify step only counts failures, so a repair
                      that fixed integrated loudness by flattening the mix passes
                      it. Delete it and the ship or do-not-ship line in the
                      operator note loses its only evidence of collateral damage.

What is deliberately NOT an agent: measurement, remediation and re-measurement.
Those are ffmpeg, called from deterministic function nodes. A model that can decide
to skip the re-measure is a model that can report a repair that never happened.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

#: The only tables an agent may read. mcp-clickhouse will happily run anything the
#: connected user can run, so the fence is here rather than in the database.
READABLE = {
    "deliverable.findings",
    "deliverable.loudness_samples",
    "deliverable.catalog_status",
    "deliverable.worst_windows",
    "deliverable.fail_windows",
    "findings",
    "loudness_samples",
    "catalog_status",
    "worst_windows",
    "fail_windows",
}

_WRITE = re.compile(
    r"(?i)\b(insert|alter|drop|truncate|create|rename|attach|detach|optimize|"
    r"grant|revoke|kill|system|delete)\b"
)


class ToolRefused(RuntimeError):
    """A tool call was blocked by the guardrail rather than sent to ClickHouse."""


# --- the guardrail --------------------------------------------------------


def select_only(tool, args: dict[str, Any], tool_context) -> dict[str, Any] | None:
    """before_tool_callback: refuse anything that is not a read.

    Returning a dict from a before_tool_callback short-circuits the call, so the
    model receives the refusal as the tool result and can try a different query.
    That is better than raising: a refusal the model can read is a correction,
    an exception is a crash.

    This is a guardrail, not an agent. It is here because the alternative is
    trusting a language model with a write connection to the database that holds
    the QC record, which is not a trade anybody should take for a demo.
    """
    query = ""
    for key in ("query", "sql", "statement"):
        if isinstance(args.get(key), str):
            query = args[key]
            break
    if not query:
        return None   # list_databases / list_tables carry no SQL, and are reads

    stripped = re.sub(r"(?s)/\*.*?\*/", " ", query).strip().rstrip(";").strip()
    if not re.match(r"(?i)^(select|with)\b", stripped):
        return {"error": "refused: only SELECT and WITH statements are permitted"}
    if _WRITE.search(stripped):
        return {"error": "refused: the statement contains a write or DDL keyword"}

    referenced = set(re.findall(r"(?i)\bfrom\s+([a-z_][\w.]*)", stripped))
    referenced |= set(re.findall(r"(?i)\bjoin\s+([a-z_][\w.]*)", stripped))

    # Names the statement defines for itself. A CTE alias is not a table, and
    # refusing it would refuse exactly the good queries: gap-and-island grouping,
    # windowing and percentile-then-filter over a 100ms series all want a WITH
    # clause, which is precisely what the scout is asked to write. The bodies of
    # those CTEs are still checked, because this scan covers the whole statement,
    # so `WITH x AS (SELECT * FROM default.secrets) SELECT * FROM x` is still
    # refused on `default.secrets`.
    defined = {n.lower() for n in re.findall(r"(?i)\b([a-z_]\w*)\s+as\s*\(", stripped)}

    unknown = {t for t in referenced
               if t.lower() not in READABLE and t.lower() not in defined}
    if unknown:
        return {
            "error": (
                "refused: "
                + ", ".join(sorted(unknown))
                + " is not a readable table. Readable: "
                + ", ".join(sorted(t for t in READABLE if "." in t))
                + ". Common table expressions and subquery aliases are fine."
            )
        }
    return None


# --- the MCP toolset ------------------------------------------------------


def _server_env() -> dict[str, str]:
    env = os.environ.copy()
    secure = os.getenv("CLICKHOUSE_SECURE", "false")
    env.setdefault("CLICKHOUSE_HOST", os.getenv("CLICKHOUSE_HOST", "localhost"))
    env.setdefault("CLICKHOUSE_PORT",
                   os.getenv("CLICKHOUSE_PORT", "8443" if secure.lower() == "true" else "8123"))
    env.setdefault("CLICKHOUSE_USER", os.getenv("CLICKHOUSE_USER", "default"))
    env.setdefault("CLICKHOUSE_PASSWORD", os.getenv("CLICKHOUSE_PASSWORD", ""))
    env.setdefault("CLICKHOUSE_SECURE", secure)
    return env


class McpRequired(RuntimeError):
    """The official mcp-clickhouse server is not reachable, so the run does not start.

    This is a hard stop rather than a degraded mode on purpose. The whole point of
    the window scout is that its SQL is what selects the seconds of audio the
    repair touches. An agent that loses its tools does not stop having opinions: on
    the first run of this graph the MCP subprocess died at startup, the scout was
    handed no tools, and it cheerfully returned five passages with plausible
    timecodes, one of them at 1782 seconds into a sixty-second scan. The
    confirmation step caught them, which is why it exists, but a pipeline that can
    reach that point at all is a pipeline that can ship an invented window on the
    day the confirmation query has a bug. So: no MCP, no run.
    """


def _script_interpreter(script: Path) -> str | None:
    """The python a console script's shebang points at."""
    try:
        first = script.read_text(errors="replace").splitlines()[0]
    except (OSError, IndexError):
        return None
    if not first.startswith("#!"):
        return None
    return first[2:].strip().strip('"')


def _server_works(script: Path) -> bool:
    """Can this copy of mcp-clickhouse actually import itself?

    Worth checking rather than assuming, because a broken copy is the common case
    on a developer machine and it fails in a way that looks like a network problem.
    `google-adk` pins `mcp>=1.24,<2` while `mcp-clickhouse` pulls `fastmcp>=4`
    which wants `mcp>=2`, so installing both into one virtualenv leaves an
    mcp-clickhouse executable on disk that dies at import with
    "No module named 'mcp.server.request_state'". The MCP session then reports
    "Connection closed", which reads as a transport fault and sends you looking in
    the wrong place. The Dockerfile keeps the server in its own virtualenv for this
    reason; this check is what stops a local run from silently preferring the
    broken sibling.
    """
    interpreter = _script_interpreter(script)
    if not interpreter or not Path(interpreter).exists():
        return False
    import subprocess

    try:
        out = subprocess.run(
            [interpreter, "-c", "import mcp_clickhouse"],
            capture_output=True, timeout=45,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return out.returncode == 0


def resolve_server() -> Path:
    """The path to a mcp-clickhouse that works, or a refusal explaining why not.

    `MCP_CLICKHOUSE_BIN` wins if set, and is trusted without probing, so an
    operator can point this at a server the probe cannot introspect.
    """
    override = os.getenv("MCP_CLICKHOUSE_BIN")
    if override:
        return Path(override)

    candidates: list[Path] = [
        Path(sys.executable).parent / "mcp-clickhouse",
        Path(__file__).resolve().parent.parent / ".venv" / "bin" / "mcp-clickhouse",
    ]
    found = shutil.which("mcp-clickhouse")
    if found:
        candidates.append(Path(found))
    candidates.append(Path("/usr/local/bin/mcp-clickhouse"))

    seen: list[Path] = []
    for candidate in candidates:
        if candidate in seen or not candidate.exists():
            continue
        seen.append(candidate)
        if _server_works(candidate):
            return candidate

    raise McpRequired(
        "no working mcp-clickhouse found. Checked: "
        + ", ".join(str(c) for c in seen or candidates)
        + ". Install it into its OWN virtualenv (it needs fastmcp>=4, which wants "
        "mcp>=2, while google-adk pins mcp<2) and point MCP_CLICKHOUSE_BIN at it."
    )


def _stdio_params():
    """Launch parameters for the official mcp-clickhouse server."""
    from mcp import StdioServerParameters

    return StdioServerParameters(command=str(resolve_server()), args=[], env=_server_env())


async def probe_mcp(timeout_s: float = 60.0) -> dict:
    """Open a real MCP session and list the tools the server offers.

    Called before the graph starts. If this fails the run does not begin, so a
    judge never sees a QC pass whose windows were produced by a model with no
    database behind it.
    """
    from mcp import ClientSession
    from mcp.client.stdio import stdio_client

    params = _stdio_params()
    try:
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = await session.list_tools()
                names = [t.name for t in tools.tools]
    except Exception as exc:
        raise McpRequired(
            f"mcp-clickhouse at {params.command} did not answer: {str(exc)[:300]}"
        ) from exc

    if not any("query" in n for n in names):
        raise McpRequired(
            f"mcp-clickhouse answered but offers no query tool; got {names}"
        )
    return {"server": params.command, "tools": names}


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


# --- what the agents must return ------------------------------------------


class FailWindow(BaseModel):
    """One passage of one master, located by a query the model wrote itself."""

    start_s: float = Field(description="start of the passage in seconds")
    end_s: float = Field(description="end of the passage in seconds")
    metric: Literal["true_peak", "short_term_high", "short_term_low"]
    measured: float = Field(description="the worst value inside the passage")
    unit: str
    rows: int = Field(description="how many 100ms samples the query returned for it")
    sql: str = Field(description="the exact statement that located this passage")


class ScoutResult(BaseModel):
    windows: list[FailWindow] = Field(default_factory=list)
    note: str = ""


class PlannedRepair(BaseModel):
    check: str
    action: Literal["loudness_normalise", "attenuate_windows", "retime_cues", "manual_review"]
    rationale: str


class RepairPlan(BaseModel):
    repairs: list[PlannedRepair] = Field(default_factory=list)
    blocking: list[str] = Field(default_factory=list)
    operator_note: str = ""


# --- the agents -----------------------------------------------------------


SCOUT_INSTRUCTION = """You locate the passages of one film master that fail a
delivery specification, by querying ClickHouse yourself.

The title under review is `{title_id}` and its run id is `{run_id}`. The
programme measured {integrated_lufs} LUFS integrated against a -23.0 LUFS target,
so bringing it to target needs a global gain of {required_gain_db} dB. The
true-peak ceiling is -1.0 dBTP.

Schema, database `deliverable`:

  loudness_samples(run_id, title_id, stage, t_seconds Float32, momentary Float32,
                   short_term Float32, integrated Float32, true_peak Float32)
      One row per 100 milliseconds of audio. stage is 'before' or 'after'.
      true_peak is the per-frame peak in dBFS.
  findings(run_id, title_id, title, run_at, stage, check, spec, measured, target,
           unit, passed, auto_fixable, detail)
  worst_windows, catalog_status: views over the two tables above.

Your job, in order:

1. Find the passages where `true_peak` is already above -1.0, or where it is high
   enough that the global gain of {required_gain_db} dB would push it above -1.0.
   Those are the passages that will force the normaliser to compress the whole
   programme instead of applying one constant gain. Call this metric
   "true_peak".
2. Group adjacent 100ms samples into contiguous passages. Do that in SQL, not by
   eye: `t_seconds` is regular, so a run of consecutive offending samples can be
   found by grouping on `t_seconds - row_number` or by bucketing and filtering.
   Report `start_s`, `end_s`, the worst `true_peak` inside the passage, and how
   many rows backed it.
3. Ignore passages shorter than 0.4 seconds. A single sample is a tick, not a
   passage, and the remediator will refuse them anyway.
4. Return at most 12 passages, worst first.

Rules you may not break:

  - Every window you return must come from a query you actually ran, and you must
    put that exact statement in the `sql` field. If you did not query it, do not
    return it.
  - Never invent a number. If a query returned nothing, return no windows and say
    so in `note`.
  - You may only read. Any attempt to write is refused by the tool layer.
  - Do not report passages that are merely QUIET. A quiet passage is a decision
    somebody made in a mix, and this tool does not lift quiet passages.
"""


PLANNER_INSTRUCTION = """You are a broadcast delivery QC supervisor deciding what
to repair on one film master, and in what order.

Failing checks, measured by ffmpeg:
{failures_json}

Passages located in the loudness series by the window scout:
{windows_json}

Decide, for each failing check, whether it can be repaired without a human
re-master, and in what order the repairs should run.

The actions that exist, and nothing else:
  attenuate_windows  pull down only the located passages. Use this when there are
                     located true-peak passages, and put it BEFORE any
                     normalisation, because attenuating first is what lets the
                     normaliser apply one constant gain instead of compressing.
  loudness_normalise a two-pass normalise of the whole programme to the target.
  retime_cues        extend subtitle cue out-times into genuinely free space.
                     Partial by construction on a densely packed track, and you
                     must say so.
  manual_review      the defect needs a person.

Rules you may not break:
  - Never dispute a measured number. ffmpeg produced it, it is ground truth.
  - Black frames and frozen frames are NEVER auto-repairable. A two-second black
    segment may be a reel change, a fade, or damage, and only a person can tell.
  - Subtitle line length is never auto-repairable. Rewriting somebody's text is a
    human judgement.
  - Do not plan attenuate_windows when the scout located no windows.

`operator_note` is two sentences to the delivery operator: what will be fixed and
what will still need them.
"""


AUDITOR_INSTRUCTION = """You audit a repair that has already happened, by querying
ClickHouse, and your job is to find the damage the pass/fail checks cannot see.

Title `{title_id}`, run `{run_id}`. Both stages are in the database: 'before' is
the master as delivered, 'after' is the repaired file. The deterministic verify
step has already confirmed the failure count went down. That is not what you are
for.

  loudness_samples(run_id, title_id, stage, t_seconds, momentary, short_term,
                   integrated, true_peak) - one row per 100ms, both stages.
  findings(...) - one row per spec check per stage.

The repair attenuated these passages, and only these passages:
{windows_json}

Compare the two stages on the same `t_seconds` grid and answer three questions,
each backed by a query you ran:

1. Did anything change OUTSIDE the passages listed above by more than the global
   gain that was applied everywhere? If so, the repair touched material it was
   not supposed to touch, and that is a blocking finding.
2. Did the spread of short-term loudness collapse? Compare the same percentile of
   short_term in each stage. A programme whose loud and quiet passages have been
   pulled toward each other has been re-mixed, not normalised.
3. Did any passage that was previously under the -1.0 dBTP ceiling end up over
   it?

Return a short operator note. Say plainly whether this master is safe to ship,
and cite the query behind each claim. Never state a number you did not query.
"""


def window_scout():
    """Writes its own SQL over the 100ms series. The windows it returns are the
    only seconds of audio the remediator is permitted to change."""
    from google.adk.agents import LlmAgent

    return LlmAgent(
        name="window_scout",
        model=MODEL,
        instruction=SCOUT_INSTRUCTION,
        tools=[clickhouse_toolset()],
        before_tool_callback=select_only,
        output_schema=ScoutResult,
        output_key="scout",
    )


def repair_planner():
    """Holds no tools on purpose: it cannot query, so it cannot invent a passage."""
    from google.adk.agents import LlmAgent

    return LlmAgent(
        name="repair_planner",
        model=MODEL,
        instruction=PLANNER_INSTRUCTION,
        output_schema=RepairPlan,
        output_key="plan",
    )


def regression_auditor():
    """Looks for the damage a failure count cannot show."""
    from google.adk.agents import LlmAgent

    return LlmAgent(
        name="regression_auditor",
        model=MODEL,
        instruction=AUDITOR_INSTRUCTION,
        tools=[clickhouse_toolset()],
        before_tool_callback=select_only,
        output_key="audit",
    )


# --- reading the model's answer back --------------------------------------


def parse_scout(raw: Any) -> ScoutResult:
    """Whatever came back from the scout, as a ScoutResult, or empty.

    ADK validates against `output_schema` and stores the parsed object, but the
    stored value has been a dict, a JSON string and a model instance across
    versions, so all three are accepted here rather than assumed.
    """
    if isinstance(raw, ScoutResult):
        return raw
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return ScoutResult(note="scout returned unparseable output")
    if isinstance(raw, dict):
        try:
            return ScoutResult.model_validate(raw)
        except Exception:
            return ScoutResult(note="scout output did not match the window schema")
    return ScoutResult()


ALLOWED_ACTIONS = {"loudness_normalise", "attenuate_windows", "retime_cues", "manual_review"}


def parse_plan(raw: Any) -> RepairPlan:
    """The plan as a RepairPlan, dropping individual repairs rather than the lot.

    A single unrecognised action must not discard the repairs the model got right:
    that would turn one hallucinated line into a run that quietly does nothing,
    which is the failure mode this pipeline exists to refuse.
    """
    if isinstance(raw, RepairPlan):
        return raw
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return RepairPlan()
    if not isinstance(raw, dict):
        return RepairPlan()

    keep = [r for r in (raw.get("repairs") or [])
            if isinstance(r, dict) and r.get("action") in ALLOWED_ACTIONS]
    try:
        return RepairPlan.model_validate({**raw, "repairs": keep})
    except Exception:
        return RepairPlan(
            blocking=[b for b in (raw.get("blocking") or []) if isinstance(b, str)],
            operator_note=str(raw.get("operator_note") or ""),
        )


def sanction_plan(plan: RepairPlan, findings: list[dict],
                  windows: list[dict]) -> RepairPlan:
    """Strike out anything the executor is not allowed to do, whatever was planned.

    The model chooses the plan. It does not get to widen the set of defects this
    tool is willing to touch. Three rules, each of which has a test that goes red
    when the rule is removed:

      - a repair may only target a check ffmpeg marked auto_fixable, so black
        frames and over-long subtitle lines stay with a human no matter how
        confidently they were planned;
      - `attenuate_windows` requires at least one passage that actually survived
        confirmation and the attenuation cap, so a plan cannot cite passages that
        the data did not support;
      - `manual_review` is always allowed, because escalating is never the unsafe
        direction.
    """
    fixable = {f["check"] for f in findings if not f["passed"] and f.get("auto_fixable")}
    treated = any(w.get("treated") for w in windows)

    kept = []
    for r in plan.repairs:
        if r.action == "manual_review":
            kept.append(r)
        elif r.action == "attenuate_windows" and not treated:
            continue
        elif r.check in fixable:
            kept.append(r)
    return RepairPlan(repairs=kept, blocking=plan.blocking,
                      operator_note=plan.operator_note)
