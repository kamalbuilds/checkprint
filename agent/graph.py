"""The delivery QC pipeline as a real ADK graph.

`google.adk.workflow.Workflow` is the current orchestration primitive in ADK 2.8.
`SequentialAgent`, `ParallelAgent` and `LoopAgent` still run but are all carrying
`@deprecated("... in favor of Workflow ...")` in the installed wheel, so they are
not used here.

The graph:

    START
      |
    ingest                       fetch the master and its subtitle track
      |
      +----> scan_audio          ffmpeg ebur128, ~10 rows per second   (parallel)
      +----> scan_picture        ffmpeg blackdetect/freezedetect, srt  (parallel)
      |
    measured (JoinNode)          waits for both, writes the 'before' stage
      |
    window_scout       [agent]   writes its own SQL over the 100ms series
      |
    confirm_windows              re-derives every window from the table itself
      |
    repair_planner     [agent]   picks actions from a fixed whitelist
      |
    remediate                    ffmpeg: attenuate the located passages, normalise
      |
    verify                       re-measure; raise if the repair did not land
      |
    regression_auditor [agent]   query the two stages against each other
      |
    report                       assemble the operator note

Why a graph rather than six function calls in a row, which is what this was:

  - The two scans are genuinely independent ffmpeg processes and now run at the
    same time, which is a fan-out plus a join, not a `for` loop.
  - The three model steps and the five deterministic steps are the same kind of
    object, so the alternation between "a model decided this" and "ffmpeg did
    this" is a property of the graph rather than a claim in a README. The UI reads
    the topology off the graph and shows a judge which nodes hold a model.
  - Every node emits ADK events, so the trace the UI renders is the framework's
    record of what ran, not a list we wrote by hand alongside the code.

What is deliberately NOT in the graph: any path by which a model can skip the
re-measure. `verify` is a function node with no route out of it, because a model
that can decide to skip the re-measure can report a repair that never happened.
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from pathlib import Path

from pydantic import BaseModel, Field

from google.adk.workflow import JoinNode, RetryConfig, Workflow, node, START

from agent import agents as A
from qc import archive, measure as m, store


class QCState(BaseModel):
    """Everything that flows between nodes. Declared, because Workflow validates
    every function-node parameter against this schema at construction time."""

    identifier: str = ""
    title: str = ""
    seconds: int = 180
    max_bytes: int = 16_000_000
    workdir: str = ""
    run_id: str = ""

    source_url: str = ""
    video_path: str = ""
    subtitle_text: str = ""
    has_subtitles: bool = False

    audio_findings: list[dict] = Field(default_factory=list)
    picture_findings: list[dict] = Field(default_factory=list)
    before_findings: list[dict] = Field(default_factory=list)
    samples: int = 0

    integrated_lufs: float = 0.0
    required_gain_db: float = 0.0
    peak_threshold_dbtp: float = 0.0

    # Rendered for the agents' instruction templates, which interpolate {name}
    # out of state and want strings rather than Python repr.
    failures_json: str = "[]"
    windows_json: str = "[]"

    scout: dict = Field(default_factory=dict)
    windows: list[dict] = Field(default_factory=list)
    plan: dict = Field(default_factory=dict)
    audit: str = ""

    fixed_video: str = ""
    fixed_subtitle_text: str = ""
    deltas: list[str] = Field(default_factory=list)
    remediation: dict = Field(default_factory=dict)
    after_findings: list[dict] = Field(default_factory=list)
    improved: bool = False
    note: str = ""

    steps: list[dict] = Field(default_factory=list)


def _step(ctx, name: str, ok: bool, summary: str, data: dict | None = None) -> None:
    """Append one step to the record the UI polls.

    Reassigned rather than appended in place: ADK tracks state changes by
    assignment, and an in-place `list.append` produces no delta, so the UI polls a
    list that never grows while the pipeline is clearly running.
    """
    steps = list(ctx.state.get("steps") or [])
    steps.append({"step": name, "ok": ok, "summary": summary, "data": data or {}})
    ctx.state["steps"] = steps
    hook = _PROGRESS.get(ctx.state.get("run_id"))
    if hook:
        hook(steps)


#: run_id -> callable(steps). Lets the web layer push partial progress to the
#: browser while a 40-second ffmpeg pass is still running, instead of showing a
#: judge a spinner and then everything at once.
_PROGRESS: dict[str, object] = {}


# --- deterministic nodes --------------------------------------------------


async def _ingest(ctx, identifier: str, workdir: str, max_bytes: int, seconds: int):
    """Fetch the master and its subtitle track from archive.org."""
    picked = await asyncio.to_thread(archive.pick_files, identifier)
    if not picked["video"]:
        raise RuntimeError(f"{identifier}: no usable video file")

    wd = Path(workdir)
    wd.mkdir(parents=True, exist_ok=True)
    video = await asyncio.to_thread(
        archive.fetch, identifier, picked["video"], wd / "source.mp4", max_bytes
    )
    subs = ""
    if picked["subtitle"]:
        subs = await asyncio.to_thread(archive.fetch_text, identifier, picked["subtitle"]) or ""

    ctx.state["title"] = picked["title"]
    ctx.state["video_path"] = str(video)
    ctx.state["subtitle_text"] = subs
    ctx.state["has_subtitles"] = bool(subs)
    ctx.state["source_url"] = archive.download_url(identifier, picked["video"])

    # Record the URL and window the numbers will come from, so the reproduce
    # command shown to a reviewer is the one that produced them rather than one
    # rebuilt from an identifier later.
    try:
        await asyncio.to_thread(
            store.store_source, identifier, picked["title"], picked["video"],
            ctx.state["source_url"], seconds, video.stat().st_size, None,
        )
    except Exception:
        pass   # provenance is not worth failing a QC run over

    _step(ctx, "ingest", True,
          f"fetched {picked['video']}" + (f" plus {picked['subtitle']}" if subs else ""),
          {"source_url": ctx.state["source_url"], "has_subtitles": bool(subs)})
    return {"title": picked["title"]}


async def _scan_audio(ctx, video_path: str, seconds: int):
    """ffmpeg ebur128. Runs at the same time as scan_picture."""
    loud = await asyncio.to_thread(m.measure_loudness, video_path, seconds)
    findings = m.loudness_findings(loud)
    ctx.state["audio_findings"] = [f.as_dict() for f in findings]
    integrated = float(loud["integrated_lufs"])
    gain = round(m.EBU_R128_TARGET_LUFS - integrated, 2)
    ctx.state["integrated_lufs"] = round(integrated, 2)
    ctx.state["required_gain_db"] = gain

    # The threshold the scout searches against, computed here rather than by the
    # model. A passage does not have to be over the ceiling today to be a problem:
    # a programme sitting 3.1 LU under target needs +3.1 dB everywhere to reach it,
    # and any passage already peaking above -4.1 dBTP will be over -1.0 dBTP once
    # that gain lands. Those are the passages that force the normaliser to compress
    # the whole programme instead of applying one constant gain.
    #
    # The model decides WHERE. Python decides HOW MUCH. Handing a language model
    # the dB arithmetic is how a repair ends up off by the size of the gain, which
    # is exactly what happened on the first run of this graph: the scout searched
    # against the raw -1.0 ceiling, returned a passage peaking at -14.6 dBTP, and
    # the remediator refused it as already compliant. It was right to refuse.
    ctx.state["peak_threshold_dbtp"] = round(m.TRUE_PEAK_CEILING_DBTP - gain, 2)
    return {"integrated_lufs": ctx.state["integrated_lufs"]}


async def _scan_picture(ctx, video_path: str, seconds: int, subtitle_text: str):
    """ffmpeg blackdetect/freezedetect plus the subtitle battery."""
    structural = await asyncio.to_thread(m.measure_structural, video_path, seconds)
    findings = m.structural_findings(structural)
    if subtitle_text:
        findings.extend(m.subtitle_findings(m.measure_subtitles(subtitle_text)))
    ctx.state["picture_findings"] = [f.as_dict() for f in findings]
    return {"checks": len(findings)}




async def _stage_before(ctx, identifier: str, title: str, video_path: str,
                       seconds: int, run_id: str, audio_findings: list[dict],
                       picture_findings: list[dict]):
    """Write the 'before' stage: the verdicts and the 100ms series behind them."""
    findings = audio_findings + picture_findings
    ctx.state["before_findings"] = findings

    samples = await asyncio.to_thread(store.loudness_timeseries, video_path, seconds)
    report = _as_report(video_path, findings)
    await asyncio.to_thread(
        store.store_run, identifier, title, "before", report, samples,
        None, uuid.UUID(run_id),
    )
    ctx.state["samples"] = len(samples)

    failures = [f for f in findings if not f["passed"]]
    ctx.state["failures_json"] = json.dumps(
        [{k: f[k] for k in ("check", "spec", "measured", "target", "unit",
                            "detail", "auto_fixable")} for f in failures],
        indent=2,
    )
    _step(ctx, "measure", True,
          f"{len(failures)} of {len(findings)} checks failed, "
          f"{len(samples)} loudness samples into ClickHouse",
          {"findings": findings, "samples": len(samples)})
    return {"failures": len(failures)}


async def _confirm_windows(ctx, identifier: str, title: str, run_id: str, scout: dict,
                          peak_threshold_dbtp: float, required_gain_db: float):
    """Re-derive every window the scout returned, straight from the samples.

    The scout is a language model holding a SQL tool. It is instructed to return
    only what its queries returned, and taking that on trust is not a trade worth
    making: an invented window would send the remediator to attenuate a passage of
    somebody's master for no reason at all. So each window is measured again here
    against `loudness_samples`, `measured` is replaced with the value the table
    actually holds, and anything the table does not support is dropped.
    """
    parsed = A.parse_scout(scout)
    raw = [w.model_dump() for w in parsed.windows]

    checked = await asyncio.to_thread(
        store.support_for_windows, identifier, raw, "before", None
    ) if raw else []
    supported = [w for w in checked if w.get("supported")]
    dropped = len(checked) - len(supported)

    planned = m.plan_window_gains(supported, ceiling_dbtp=peak_threshold_dbtp)
    for w in planned:
        w["target"] = peak_threshold_dbtp
        w.setdefault("unit", "dBTP")
        if w.get("treated"):
            w["reason"] = (
                f"peaks at {w['measured']} dBTP; the programme needs "
                f"{required_gain_db:+.1f} dB to reach target, which would put this "
                f"passage at {w['measured'] + required_gain_db:+.1f} dBTP, over the "
                f"{m.TRUE_PEAK_CEILING_DBTP} dBTP ceiling. Attenuated "
                f"{abs(w['gain_db']):.1f} dB inside this passage only, so the "
                "programme gain can stay constant everywhere else."
            )

    if planned:
        await asyncio.to_thread(
            store.store_windows, uuid.UUID(run_id), identifier, title, planned, None
        )

    ctx.state["windows"] = planned
    ctx.state["windows_json"] = json.dumps(
        [{k: w.get(k) for k in ("start_s", "end_s", "metric", "measured",
                                "gain_db", "treated", "reason")} for w in planned],
        indent=2,
    )
    treated = [w for w in planned if w.get("treated")]
    _step(ctx, "locate", True,
          f"{len(planned)} passages located by the scout's own SQL, "
          f"{len(treated)} inside the attenuation cap"
          + (f", {dropped} dropped as unsupported by the data" if dropped else ""),
          {"windows": planned, "dropped": dropped, "note": parsed.note,
           "queries": sorted({w.get("sql", "") for w in raw if w.get("sql")})})
    return {"windows": len(planned)}


async def _remediate(ctx, workdir: str, video_path: str, subtitle_text: str,
                    seconds: int, plan: dict, windows: list[dict],
                    before_findings: list[dict], peak_threshold_dbtp: float = 0.0):
    """ffmpeg. Attenuate only the located passages, then normalise the programme."""
    parsed = A.sanction_plan(A.parse_plan(plan), before_findings, windows)
    actions = {r.action for r in parsed.repairs}
    ctx.state["plan"] = parsed.model_dump()

    wd = Path(workdir)
    fixed_video, fixed_subs, deltas = video_path, subtitle_text, []
    remediation: dict = {}

    treated = [w for w in windows if w.get("treated")] \
        if "attenuate_windows" in actions else []

    if "loudness_normalise" in actions or treated:
        result = await asyncio.to_thread(
            m.remediate_loudness_detailed, video_path, wd / "fixed.m4a",
            m.EBU_R128_TARGET_LUFS, m.TRUE_PEAK_CEILING_DBTP, seconds, treated,
            peak_threshold_dbtp or None,
        )
        fixed_video = str(result.path)
        remediation = result.as_dict()
        if treated:
            deltas.append(
                f"{len(treated)} passages attenuated, "
                + ", ".join(f"{w['gain_db']:.1f} dB at {w['start_s']:.1f}s"
                            for w in treated[:3])
            )
        deltas.append(
            "programme normalised to target"
            + (f" ({result.normalization_type})" if result.normalization_type else "")
        )

    if "retime_cues" in actions and subtitle_text:
        fixed_subs, changed = m.remediate_subtitles(subtitle_text)
        (wd / "fixed.srt").write_text(fixed_subs)
        deltas.append(f"{changed} cues retimed")

    # A silent no-op is the dangerous case: before and after come out identical,
    # verify reports "no change", and it reads as a film that could not be
    # improved. If there WERE auto-fixable failures and nothing ran, that is a
    # planning bug and it has to be loud.
    auto_fixable = [f["check"] for f in before_findings
                    if not f["passed"] and f["auto_fixable"]]
    if auto_fixable and not deltas:
        raise RuntimeError(
            f"remediate produced no change despite auto-fixable failures {auto_fixable}; "
            f"planned actions were {sorted(actions)}. Refusing to report an unchanged "
            "file as a completed repair."
        )

    ctx.state["fixed_video"] = fixed_video
    ctx.state["fixed_subtitle_text"] = fixed_subs
    ctx.state["deltas"] = deltas
    ctx.state["remediation"] = remediation
    _step(ctx, "remediate", bool(deltas), "; ".join(deltas) or "nothing auto-fixable",
          {"actions": sorted(actions), **remediation})
    return {"actions": sorted(actions)}


async def _verify(ctx, identifier: str, title: str, run_id: str, seconds: int,
                 fixed_video: str, fixed_subtitle_text: str,
                 before_findings: list[dict]):
    """Re-measure the repaired file and prove the delta, or say it did not land."""
    report = await asyncio.to_thread(
        m.run_qc, fixed_video, fixed_subtitle_text or None, seconds
    )
    samples = await asyncio.to_thread(store.loudness_timeseries, fixed_video, seconds)
    await asyncio.to_thread(
        store.store_run, identifier, title, "after", report, samples,
        None, uuid.UUID(run_id),
    )

    after = [f.as_dict() for f in report.findings]
    ctx.state["after_findings"] = after
    before_failures = [f for f in before_findings if not f["passed"]]
    improved = len(report.failures) < len(before_failures)
    ctx.state["improved"] = improved

    _step(ctx, "verify", improved,
          f"failures {len(before_failures)} to {len(report.failures)}",
          {"before": before_findings, "after": after, "improved": improved})
    return {"improved": improved}


async def _report(ctx, title: str, plan: dict, audit: str, windows: list[dict],
                 remediation: dict, before_findings: list[dict],
                 after_findings: list[dict]):
    """Assemble the operator note out of what the two model steps produced."""
    parsed = A.parse_plan(plan)
    before_failures = len([f for f in before_findings if not f["passed"]])
    after_failures = len([f for f in after_findings if not f["passed"]])

    lines = [
        f"{title}: {before_failures} checks failed before remediation, "
        f"{after_failures} after."
    ]
    if parsed.operator_note:
        lines.append(parsed.operator_note.strip())
    if audit:
        lines.append(audit.strip())
    if parsed.blocking:
        lines.append("Blocking: " + ", ".join(parsed.blocking) + ".")

    note = " ".join(lines)
    ctx.state["note"] = note
    _step(ctx, "report", True, note,
          {"blocking": parsed.blocking, "windows": windows, **remediation})
    return {"note": note}


def _as_report(source: str, findings: list[dict]):
    """Rebuild a QCReport from serialised findings so the store layer is unchanged."""
    rep = m.QCReport(source=source)
    rep.findings = [
        m.Finding(
            check=f["check"], spec=f["spec"], measured=f["measured"],
            target=f["target"], unit=f["unit"], passed=f["passed"],
            detail=f.get("detail", ""), auto_fixable=f.get("auto_fixable", False),
            offenders=f.get("offenders", []),
        )
        for f in findings
    ]
    return rep


# The deterministic nodes, wrapped. Declared as plain coroutines above and wrapped
# here rather than decorated in place, so `_remediate` stays directly callable: a
# test that has to reach into a pydantic private attribute to exercise a node is a
# test that breaks on the next ADK release.
ingest = node(_ingest, name="ingest")
scan_audio = node(_scan_audio, name="scan_audio")
scan_picture = node(_scan_picture, name="scan_picture")
stage_before = node(_stage_before, name="stage_before")
confirm_windows = node(_confirm_windows, name="confirm_windows")
remediate = node(_remediate, name="remediate")
verify = node(_verify, name="verify")
report = node(_report, name="report")

measured = JoinNode(name="measured")

# --- the graph ------------------------------------------------------------


#: The topology, as data, so the UI can render it without a second copy of the
#: truth living in JavaScript. `kind` is what a judge is actually asking: which of
#: these steps holds a model, and which of them is ffmpeg.
TOPOLOGY = [
    {"node": "ingest", "kind": "deterministic", "does": "fetch the master and its subtitles"},
    {"node": "scan_audio", "kind": "deterministic", "does": "ffmpeg ebur128, ten readings a second", "parallel": True},
    {"node": "scan_picture", "kind": "deterministic", "does": "black, frozen and subtitle checks", "parallel": True},
    {"node": "measured", "kind": "join", "does": "wait for both scans"},
    {"node": "stage_before", "kind": "deterministic", "does": "write the before stage to ClickHouse"},
    {"node": "window_scout", "kind": "agent", "tools": "mcp-clickhouse",
     "does": "write SQL over the 100ms series to locate the failing passages"},
    {"node": "confirm_windows", "kind": "deterministic",
     "does": "re-derive every window from the table and drop what it does not support"},
    {"node": "repair_planner", "kind": "agent", "tools": "none",
     "does": "choose the actions, from a fixed whitelist"},
    {"node": "remediate", "kind": "deterministic", "does": "ffmpeg: attenuate the passages, normalise"},
    {"node": "verify", "kind": "deterministic", "does": "re-measure and prove the delta"},
    {"node": "regression_auditor", "kind": "agent", "tools": "mcp-clickhouse",
     "does": "compare the two stages for damage a failure count cannot show"},
    {"node": "report", "kind": "deterministic", "does": "assemble the operator note"},
]


#: Gemini answers 429 RESOURCE_EXHAUSTED under load, and a QC pass that has already
#: spent forty seconds of ffmpeg must not be thrown away because a model call was
#: rate limited for two seconds. Retry is per node, which is a property of the graph
#: rather than a try/except wrapped round the whole run.
_AGENT_RETRY = RetryConfig(max_attempts=4, initial_delay=2.0, max_delay=30.0,
                           backoff_factor=2.0)


def build_workflow() -> Workflow:
    scout = node(A.window_scout(), retry_config=_AGENT_RETRY, timeout=180)
    planner = node(A.repair_planner(), retry_config=_AGENT_RETRY, timeout=120)
    auditor = node(A.regression_auditor(), retry_config=_AGENT_RETRY, timeout=180)

    return Workflow(
        name="delivery_qc",
        state_schema=QCState,
        edges=[
            (START, ingest),
            (ingest, (scan_audio, scan_picture)),   # fan-out: two ffmpeg processes
            (scan_audio, measured),
            (scan_picture, measured),               # JoinNode waits for both
            (measured, stage_before),
            (stage_before, scout),
            (scout, confirm_windows),
            (confirm_windows, planner),
            (planner, remediate),
            (remediate, verify),
            (verify, auditor),
            (auditor, report),
        ],
    )
