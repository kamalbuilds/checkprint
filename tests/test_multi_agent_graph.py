"""Each of the three agents must change an outcome, or it is decoration.

The rule these tests exist to enforce: if deleting an agent leaves the output file
and the verdict identical, that agent is a diagram, and criterion one is lost the
same way a deterministic model fallback loses it.

So there is one removal test per agent, and each one asserts on something a person
could check: the bytes of the repaired audio, whether the run refuses to report an
unchanged file, and whether the operator note still carries evidence of collateral
damage.
"""

from __future__ import annotations

import asyncio
import re as _re
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent import agents as A  # noqa: E402
from agent import api, graph  # noqa: E402
from qc import measure as m, store  # noqa: E402


# --- the graph is what it says it is --------------------------------------


def test_graph_is_a_real_adk_workflow():
    """Built on the current primitive, not the deprecated trio."""
    from google.adk.workflow import Workflow

    wf = graph.build_workflow()
    assert isinstance(wf, Workflow)
    names = [n.name for n in wf.graph.nodes]
    assert names[0] == "__START__"
    for expected in ("ingest", "scan_audio", "scan_picture", "measured",
                     "stage_before", "window_scout", "confirm_windows",
                     "repair_planner", "remediate", "verify",
                     "regression_auditor", "report"):
        assert expected in names, f"{expected} missing from {names}"


def test_deprecated_workflow_agents_are_not_used():
    """A Google engineer greps for the stale primitives. They must not be here."""
    for path in Path(__file__).resolve().parents[1].glob("agent/*.py"):
        code = "\n".join(
            line for line in path.read_text().splitlines()
            if not line.lstrip().startswith(("#", "*"))
        )
        for stale in ("SequentialAgent", "ParallelAgent", "LoopAgent"):
            assert f"{stale}(" not in code, f"{path.name} instantiates {stale}"
            assert f"import {stale}" not in code, f"{path.name} imports {stale}"


def test_three_nodes_hold_a_model_and_the_measurement_nodes_do_not():
    """Measurement, remediation and re-measurement are ffmpeg, never an agent."""
    from google.adk.agents import LlmAgent

    wf = graph.build_workflow()
    holders = set()
    for n in wf.graph.nodes:
        inner = getattr(n, "_inner_node", None) or n
        if isinstance(inner, LlmAgent) or isinstance(getattr(inner, "agent", None), LlmAgent):
            holders.add(n.name)
    assert holders == {"window_scout", "repair_planner", "regression_auditor"}, holders
    for deterministic in ("scan_audio", "scan_picture", "remediate", "verify"):
        assert deterministic not in holders


def test_topology_matches_the_graph():
    """The UI renders TOPOLOGY, so it must not drift from the edges it describes."""
    wf = graph.build_workflow()
    graph_names = {n.name for n in wf.graph.nodes} - {"__START__"}
    described = {n["node"] for n in graph.TOPOLOGY}
    assert described == graph_names, described ^ graph_names
    agents = {n["node"] for n in graph.TOPOLOGY if n["kind"] == "agent"}
    assert agents == {"window_scout", "repair_planner", "regression_auditor"}


def test_scans_fan_out_and_are_joined():
    """The two ffmpeg batteries are independent, so they run at the same time."""
    wf = graph.build_workflow()
    from_ingest = {e.to_node.name for e in wf.graph.edges if e.from_node.name == "ingest"}
    assert from_ingest == {"scan_audio", "scan_picture"}
    into_join = {e.from_node.name for e in wf.graph.edges if e.to_node.name == "measured"}
    assert into_join == {"scan_audio", "scan_picture"}
    join = next(n for n in wf.graph.nodes if n.name == "measured")
    assert join._requires_all_predecessors is True


# --- /api/agents may not claim a change this corpus cannot show ------------

# `removing_it` is served to anyone who opens /api/agents, so every sentence in it
# is a claim a judge can go and test. "The output file changes" is only true where a
# passage collides with the lift to target, and no public-domain transfer in this
# catalog does that: the scout returns no windows on all of them, so the repair is
# already one global gain and deleting the scout leaves the delivered file identical.
# The shipped string therefore has to carry its own scope, and this is the check that
# it does. It also fails the other way: if the corpus ever grows a title with a
# treated window, the disclaimer becomes the stale sentence and has to go.

_CLAIMS_A_DIFFERENT_FILE = _re.compile(
    r"(?:output|delivered|rendered|repaired|resulting)\s+file[^.]{0,80}?"
    r"(?:changes|change|differs|differ|is different)"
    r"|byte[ -]different",
    _re.I,
)
_NAMES_THE_COLLIDING_CASE = _re.compile(
    r"\bwould\b[^.]{0,80}?(?:clip|no headroom)", _re.I)
_DISCLAIMS_THIS_CORPUS = _re.compile(
    r"no title in (?:this|the)[^.]{0,40}?corpus", _re.I)
_NO_WINDOWS_HERE = _re.compile(r"(?:returns no windows|no windows)", _re.I)


def _audit_removal_claims(agents: list[dict], treated_windows: int) -> None:
    """Raise unless every file-change claim matches what the corpus can demonstrate."""
    for agent in agents:
        text = agent["removing_it"]
        if not _CLAIMS_A_DIFFERENT_FILE.search(text):
            continue
        assert _NAMES_THE_COLLIDING_CASE.search(text), (
            f"{agent['name']}: removing_it says the file changes without naming the "
            f"case where it does, a passage that would clip once the programme is "
            f"lifted to target. As written it reads as a claim about every title. "
            f"Text: {text!r}"
        )
        if treated_windows == 0:
            assert _DISCLAIMS_THIS_CORPUS.search(text), (
                f"{agent['name']}: no treated window is stored for any title, so on "
                f"this corpus the repair is one global gain and removing the scout "
                f"changes nothing. removing_it must say so. Text: {text!r}"
            )
            assert _NO_WINDOWS_HERE.search(text) and "identical" in text.lower(), (
                f"{agent['name']}: the disclaimer has to state the consequence, that "
                f"the scout returns no windows here and the delivered file is "
                f"identical without it. Text: {text!r}"
            )
        else:
            assert not _DISCLAIMS_THIS_CORPUS.search(text), (
                f"{agent['name']}: {treated_windows} treated window(s) are stored, so "
                f"a title in the corpus now does demonstrate the change and the "
                f"no-collision disclaimer is false. Name that title instead. "
                f"Text: {text!r}"
            )


def _treated_windows_in_the_corpus() -> int:
    """Passages the remediator actually applied a gain to, over the whole store.

    A window row with treated = 0 is the scout locating a passage and the remediator
    declining it, which leaves the render untouched, so only treated rows can back a
    claim that the output differs.

    Blind spot, stated rather than hidden: with no reachable ClickHouse this returns
    0, which selects the strict branch above. That still catches the sentence losing
    its scope, which is the failure we shipped; it cannot catch a disclaimer that a
    newly ingested title has made stale.
    """
    try:
        rows = store.client().query(
            "SELECT count() FROM deliverable.fail_windows WHERE treated"
        ).result_rows
    except Exception:
        return 0
    return int(rows[0][0]) if rows else 0


def test_no_removal_claim_promises_a_file_change_this_corpus_cannot_show():
    treated = _treated_windows_in_the_corpus()
    _audit_removal_claims(api.topology()["agents"], treated)


def test_the_removal_claim_audit_goes_red_on_both_kinds_of_overclaim():
    """The audit above is worth nothing unless it can fail. Both branches, in-suite."""
    shipped_before_the_fix = [{
        "name": "window_scout",
        "removing_it": "the repair becomes one gain over the whole programme, "
                       "so the output file changes",
    }]
    with pytest.raises(AssertionError, match="without naming the case"):
        _audit_removal_claims(shipped_before_the_fix, 0)

    scoped_but_not_disclaimed = [{
        "name": "window_scout",
        "removing_it": "on a master where a passage would clip once the programme is "
                       "lifted to target, the rendered file differs",
    }]
    with pytest.raises(AssertionError, match="must say so"):
        _audit_removal_claims(scoped_but_not_disclaimed, 0)

    # And the reverse: once a title in the store carries a treated window, the
    # no-collision disclaimer is the stale half of the sentence. A literal is used
    # here so this test names one direction; the shipped string is put through the
    # same branch by the corpus test above, with the count read from ClickHouse.
    fully_scoped = [{
        "name": "window_scout",
        "removing_it": "on a master where a passage would clip once the programme is "
                       "lifted to target, the rendered file differs. No title in this "
                       "public-domain corpus has that collision, so the scout returns "
                       "no windows here and the file is identical without it",
    }]
    _audit_removal_claims(fully_scoped, 0)
    with pytest.raises(AssertionError, match="disclaimer is false"):
        _audit_removal_claims(fully_scoped, treated_windows=1)

    # The strings that make no claim about the file are not dragged in by the regex.
    quiet = [a for a in api.topology()["agents"] if a["name"] != "window_scout"]
    assert len(quiet) == 2
    _audit_removal_claims(quiet, 0)
    _audit_removal_claims(quiet, 1)


# --- removal proof 1: the window scout changes the audio ------------------


@pytest.fixture(scope="module")
def hot_master(tmp_path_factory) -> Path:
    """A programme under target with one passage that has no headroom left.

    Synthesised rather than downloaded so the test is deterministic, but the shape
    is the common real one: a quiet transfer with a hot passage in it. Bringing the
    programme up to target pushes that passage over the ceiling, which is what
    forces a normaliser to compress everything instead of applying one gain.
    """
    d = tmp_path_factory.mktemp("hot")
    quiet, hot, master = d / "q.wav", d / "h.wav", d / "master.wav"
    # The hot passage is deliberately SHORT relative to the programme. A hot section
    # long enough to dominate the gated integrated loudness makes the test lie to
    # itself: attenuating it lowers integrated, the normalise afterwards adds the
    # same amount back globally, and the change at the passage nearly cancels. Real
    # masters fail this way too, on a transient rather than on a reel.
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-v", "error", "-y", "-f", "lavfi",
         "-i", "anoisesrc=d=15:c=pink:a=0.5:r=48000:seed=20260909",
         "-af", "volume=-14dB", "-c:a", "pcm_s16le", str(quiet)], check=True)
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-v", "error", "-y", "-f", "lavfi",
         "-i", "anoisesrc=d=1:c=pink:a=0.5:r=48000:seed=1917",
         "-af", "volume=1.5dB", "-c:a", "pcm_s16le", str(hot)], check=True)
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-v", "error", "-y", "-i", str(quiet), "-i", str(hot),
         "-i", str(quiet), "-filter_complex", "[0][1][2]concat=n=3:v=0:a=1",
         "-c:a", "pcm_s16le", str(master)], check=True)
    return master


def _measure(path: Path, seconds: int) -> tuple[float, float]:
    """Integrated loudness and true peak of a file, straight from ebur128."""
    out = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-t", str(seconds), "-i", str(path),
         "-af", "ebur128=peak=true", "-f", "null", "-"],
        capture_output=True, text=True, timeout=600,
    ).stderr
    integrated = _re.search(r"I:\s*(-?\d+\.\d+)\s*LUFS\n\s*Threshold", out)
    peak = _re.search(r"Peak:\s*(-?\d+\.\d+)\s*dBFS", out)
    assert integrated and peak, out[-500:]
    return float(integrated.group(1)), float(peak.group(1))


def _series(path: Path, seconds: int = 31):
    """The 100 ms ebur128 series with real timestamps, the same parse the store uses.

    Timestamps rather than `-ss` seeking on purpose: seeking into an AAC file lands
    on a frame boundary and drags encoder padding in with it, which is enough to
    hide a 3 dB change.
    """
    rows = store.loudness_timeseries(path, seconds=seconds)
    assert rows, f"ebur128 produced no per-frame readings for {path}"
    return rows


def _peak_in(rows, start: float, end: float) -> float:
    """Loudest momentary reading between two timestamps."""
    values = [r[1] for r in rows if start <= r[0] <= end]
    assert values, f"no samples between {start}s and {end}s"
    return max(values)


def test_removing_the_scout_changes_the_repaired_audio(hot_master, tmp_path):
    """The passages the scout writes SQL for are the only thing that varies here.

    Two claims, both checked exactly rather than approximately.

    First, the located passage and ONLY the located passage is attenuated, by the
    gain that was planned. That is measured on the window pass alone, against the
    untouched source, on the same 100 ms grid ffmpeg produces, so there is no
    seeking and no encoder padding in the way.

    Second, the file that comes out of the full repair differs from the file that
    comes out of the same repair with no passages handed to it. That is what
    deleting the scout does, and if the two matched the scout would be decoration.

    The end-to-end level difference is deliberately NOT asserted to be the size of
    the gain: the normalise afterwards picks its own global gain from its own
    measurement of each render, and partly offsets it. What cannot be offset is
    that a different set of samples was modified.
    """
    # The threshold and the passage value are MEASURED off the fixture with the same
    # arithmetic scan_audio uses, not hardcoded. Hardcoding them made this test flaky:
    # a fixed -3.0 dBTP sometimes described a passage that was not there.
    integrated, peak = _measure(hot_master, 31)
    lift = round(m.EBU_R128_TARGET_LUFS - integrated, 2)
    ceiling = round(m.TRUE_PEAK_CEILING_DBTP - lift, 2)
    assert peak > ceiling, (
        f"fixture does not exercise the case: peak {peak} dBTP already clears the "
        f"{ceiling} dBTP threshold, so there is nothing for the scout to find"
    )
    window = [{"start_s": 15.0, "end_s": 16.0, "metric": "true_peak",
               "measured": peak, "unit": "dBTP"}]

    planned = m.plan_window_gains(window, ceiling_dbtp=ceiling)
    assert planned[0]["treated"] is True
    gain = planned[0]["gain_db"]
    assert gain < 0, "the tool only ever attenuates"

    # --- claim one: exactly the located passage moves, by exactly the planned gain
    treated_only = tmp_path / "windowed.wav"
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-v", "error", "-y", "-t", "31",
         "-i", str(hot_master), "-af", m.window_filter(planned),
         "-c:a", "pcm_s16le", str(treated_only)], check=True, timeout=600)

    before = _series(hot_master)
    after = _series(treated_only)
    inside_delta = _peak_in(after, 15.05, 15.95) - _peak_in(before, 15.05, 15.95)
    outside_delta = _peak_in(after, 0.5, 14.0) - _peak_in(before, 0.5, 14.0)

    assert inside_delta == pytest.approx(gain, abs=0.3), (inside_delta, gain)
    assert outside_delta == pytest.approx(0.0, abs=0.1), outside_delta

    # --- claim two: the finished repair is a different file
    with_scout = m.remediate_loudness_detailed(
        hot_master, tmp_path / "with.m4a", seconds=31, windows=window,
        window_ceiling_dbtp=ceiling)
    without_scout = m.remediate_loudness_detailed(
        hot_master, tmp_path / "without.m4a", seconds=31, windows=None,
        window_ceiling_dbtp=ceiling)

    assert [w["treated"] for w in with_scout.windows] == [True]
    assert without_scout.windows == []
    assert with_scout.path.read_bytes() != without_scout.path.read_bytes(), \
        "the located passage made no difference to the output file"

    # What is NOT asserted, and why. After the normalise the level difference at the
    # passage measures 0.1 LU while the rest of the programme moves 0.2 LU, because
    # `loudnorm` is running in dynamic mode here and redistributes level across the
    # whole file. Asserting "the difference is concentrated in the passage" would be
    # asserting something that is not true of this signal chain, and a test that
    # states a convenient falsehood is worse than no test. The exact claim above,
    # measured before the normalise, is the one that holds.


def test_the_scout_only_ever_pulls_down(hot_master, tmp_path):
    """A quiet passage is a decision somebody made. The tool does not lift it."""
    quiet_window = [{"start_s": 2.0, "end_s": 6.0, "metric": "short_term_low",
                     "measured": -38.0, "unit": "LUFS"}]
    planned = m.plan_window_gains(quiet_window, ceiling_dbtp=-9.0)
    assert planned[0]["treated"] is False
    assert "never treated" in planned[0]["reason"]
    assert m.window_filter(planned) == ""


def test_the_scout_cannot_ask_for_a_remaster():
    """Past the cap, a window is escalated instead of quietly flattened."""
    beyond = m.plan_window_gains([{"start_s": 1.0, "end_s": 4.0, "metric": "true_peak",
                                   "measured": 9.0, "unit": "dBTP"}])
    assert beyond[0]["treated"] is False
    assert "mastering decision" in beyond[0]["reason"]

    inside = m.plan_window_gains([{"start_s": 1.0, "end_s": 4.0, "metric": "true_peak",
                                   "measured": 2.0, "unit": "dBTP"}])
    assert inside[0]["treated"] is True
    assert inside[0]["gain_db"] == pytest.approx(-3.0)


def test_a_tick_is_not_a_passage():
    """One 100ms sample over the ceiling is not an edit anybody asked for."""
    tick = m.plan_window_gains([{"start_s": 5.0, "end_s": 5.1, "metric": "true_peak",
                                 "measured": 2.0, "unit": "dBTP"}])
    assert tick[0]["treated"] is False
    assert "400ms" in tick[0]["reason"]


# --- removal proof 2: the planner decides whether anything happens --------


class FakeCtx:
    """The slice of an ADK Context the deterministic nodes actually touch."""

    def __init__(self, **state):
        self.state = dict(state)
        self.state.setdefault("steps", [])


AUTO_FIXABLE_FAILURE = [
    {"check": "integrated_loudness_ebu_r128", "passed": False, "auto_fixable": True},
]


def test_removing_the_planner_makes_the_run_refuse(tmp_path):
    """An empty plan plus a fixable failure must raise, not report success.

    This is the dangerous no-op: before and after come out identical, verify says
    "no change", and it reads as a film that could not be improved rather than a
    repair that never ran.
    """
    ctx = FakeCtx()
    with pytest.raises(RuntimeError, match="produced no change"):
        asyncio.run(graph._remediate(
            ctx, workdir=str(tmp_path), video_path="unused", subtitle_text="",
            seconds=10, plan={"repairs": []}, windows=[],
            before_findings=AUTO_FIXABLE_FAILURE,
        ))


def test_a_clean_master_is_allowed_to_do_nothing(tmp_path):
    """The refusal above must not fire when there was nothing to fix."""
    ctx = FakeCtx()
    out = asyncio.run(graph._remediate(
        ctx, workdir=str(tmp_path), video_path="unused", subtitle_text="",
        seconds=10, plan={"repairs": []}, windows=[],
        before_findings=[{"check": "integrated_loudness_ebu_r128",
                          "passed": True, "auto_fixable": True}],
    ))
    assert out == {"actions": []}
    assert ctx.state["deltas"] == []


# --- removal proof 3: the auditor is the only collateral-damage evidence ---


def test_removing_the_auditor_strips_the_damage_evidence():
    """The note must lose the auditor's finding when the auditor is gone."""
    plan = {"repairs": [], "blocking": ["black_frames"],
            "operator_note": "Loudness normalised."}
    before = [{"check": "a", "passed": False, "auto_fixable": True}]
    after = [{"check": "a", "passed": True, "auto_fixable": True}]

    with_auditor = FakeCtx()
    asyncio.run(graph._report(
        with_auditor, title="T", plan=plan,
        audit="Short-term spread collapsed from 14.2 LU to 6.1 LU; do not ship.",
        windows=[], remediation={}, before_findings=before, after_findings=after))

    without_auditor = FakeCtx()
    asyncio.run(graph._report(
        without_auditor, title="T", plan=plan, audit="",
        windows=[], remediation={}, before_findings=before, after_findings=after))

    assert "do not ship" in with_auditor.state["note"]
    assert "do not ship" not in without_auditor.state["note"]
    assert len(without_auditor.state["note"]) < len(with_auditor.state["note"])


# --- the guardrail on the MCP tools --------------------------------------


class FakeTool:
    name = "run_query"


@pytest.mark.parametrize("sql", [
    "INSERT INTO deliverable.findings VALUES (1)",
    "DROP TABLE deliverable.findings",
    "TRUNCATE TABLE deliverable.loudness_samples",
    "ALTER TABLE deliverable.findings DELETE WHERE 1",
    "SYSTEM SHUTDOWN",
])
def test_guardrail_refuses_writes(sql):
    """A language model does not get a write connection to the QC record."""
    result = A.select_only(FakeTool(), {"query": sql}, None)
    assert result is not None and "refused" in result["error"], sql


@pytest.mark.parametrize("sql", [
    "SHOW TABLES FROM deliverable",
    "DESCRIBE deliverable.findings",
    "EXISTS TABLE deliverable.findings",
])
def test_guardrail_refuses_statements_that_are_not_reads_of_a_known_table(sql):
    """Only the SELECT/WITH prefix rule catches these, so it is tested on its own.

    Without this case the write-keyword fence answers for every statement in the
    test above, and disabling the prefix rule entirely leaves the suite green: a
    check that cannot fail. None of these carry a write keyword.
    """
    result = A.select_only(FakeTool(), {"query": sql}, None)
    assert result is not None and "SELECT and WITH" in result["error"], sql


def test_guardrail_refuses_tables_it_does_not_know():
    result = A.select_only(FakeTool(), {"query": "SELECT * FROM default.secrets"}, None)
    assert result is not None and "not a readable table" in result["error"]


def test_guardrail_refuses_the_system_database():
    """Caught by the keyword fence rather than the table fence, but caught."""
    result = A.select_only(FakeTool(), {"query": "SELECT * FROM system.users"}, None)
    assert result is not None and "refused" in result["error"]


@pytest.mark.parametrize("sql", [
    "SELECT max(true_peak) FROM deliverable.loudness_samples WHERE title_id = 'x'",
    "WITH g AS (SELECT 1) SELECT * FROM deliverable.findings",
    "SELECT a.t_seconds FROM deliverable.loudness_samples AS a "
    "JOIN deliverable.loudness_samples AS b ON a.t_seconds = b.t_seconds",
])
def test_guardrail_lets_real_queries_through(sql):
    """It has to be able to say yes, or it is a wall rather than a guardrail."""
    assert A.select_only(FakeTool(), {"query": sql}, None) is None, sql


# A common table expression is not a table. Refusing CTE aliases would refuse
# exactly the queries the scout is asked to write, since gap-and-island grouping,
# windowing and percentile-then-filter over a 100ms series all want a WITH clause.
# The three cases below are the fix and the two ways it must not become a bypass.


def test_guardrail_allows_a_cte_over_an_allowlisted_table():
    sql = ("WITH flagged AS ("
           "  SELECT t_seconds, true_peak FROM deliverable.loudness_samples"
           "  WHERE title_id = 'x' AND true_peak > -2.9"
           ") SELECT min(t_seconds), max(true_peak) FROM flagged")
    assert A.select_only(FakeTool(), {"query": sql}, None) is None


def test_a_cte_body_cannot_smuggle_in_a_forbidden_table():
    """The alias is permitted; what the alias reads is still checked."""
    sql = "WITH x AS (SELECT * FROM default.secrets) SELECT * FROM x"
    result = A.select_only(FakeTool(), {"query": sql}, None)
    assert result is not None and "default.secrets" in result["error"]


def test_defining_a_cte_does_not_unlock_other_tables():
    """A statement cannot buy access to a physical table by declaring a CTE first."""
    sql = "WITH x AS (SELECT 1) SELECT * FROM default.secrets"
    result = A.select_only(FakeTool(), {"query": sql}, None)
    assert result is not None and "default.secrets" in result["error"]


def test_the_scouts_real_query_shape_is_permitted():
    """The gap-and-island query Gemini actually wrote, from a live run."""
    sql = """SELECT MIN(t_seconds) AS start_s, MAX(t_seconds) + 0.1 AS end_s,
                    MAX(true_peak) AS worst_true_peak, COUNT(*) AS row_count
             FROM (
               SELECT t_seconds, true_peak,
                      t_seconds - (ROW_NUMBER() OVER (ORDER BY t_seconds) * 0.1) AS time_group
               FROM deliverable.loudness_samples
               WHERE title_id = 'vicki-1953' AND stage = 'before' AND true_peak > -2.9
             ) AS flagged_samples
             GROUP BY time_group HAVING row_count >= 4 ORDER BY worst_true_peak DESC"""
    assert A.select_only(FakeTool(), {"query": sql}, None) is None


def test_the_auditors_real_query_shape_is_permitted():
    """The before/after self-join the regression auditor actually wrote."""
    sql = """SELECT a.t_seconds, a.true_peak - b.true_peak AS diff_true_peak
             FROM deliverable.loudness_samples AS a
             JOIN deliverable.loudness_samples AS b ON a.t_seconds = b.t_seconds
             WHERE a.stage = 'after' AND b.stage = 'before'"""
    assert A.select_only(FakeTool(), {"query": sql}, None) is None


def test_guardrail_allows_the_discovery_tools():
    """list_tables carries no SQL and is a read."""
    class Listing:
        name = "list_tables"

    assert A.select_only(Listing(), {"database": "deliverable"}, None) is None


# --- the not-remediated display trap -------------------------------------


def test_a_title_nobody_remediated_reports_no_after_count(monkeypatch):
    """failures_after must be null, never 0, when there is no after stage.

    A title with verdict "not remediated" has no after-stage rows at all. Rendering
    that as "6 to 0" would advertise the most impressive repair in the catalog for a
    run that never happened, on a product whose entire claim is that it re-measures
    to prove the repair.
    """
    before = [
        {"stage": "before", "check": "integrated_loudness_ebu_r128", "spec": "EBU R128",
         "measured": -29.0, "target": -23.0, "unit": "LUFS", "passed": False,
         "auto_fixable": True, "detail": "", "run_at": "2026-09-08 09:42:44"},
        {"stage": "before", "check": "true_peak", "spec": "EBU R128", "measured": 0.2,
         "target": -1.0, "unit": "dBTP", "passed": False, "auto_fixable": True,
         "detail": "", "run_at": "2026-09-08 09:42:44"},
    ]
    monkeypatch.setattr(api.store, "client", lambda: object())
    monkeypatch.setattr(api.store, "title_findings",
                        lambda t, ch=None: {"before": before, "after": []})
    monkeypatch.setattr(api.store, "loudness_series",
                        lambda t, ch=None, **k: {"stages": {}, "seconds": 0, "samples": 0})
    monkeypatch.setattr(api.store, "fail_windows", lambda t, ch=None: [])
    monkeypatch.setattr(api.store, "query_cost", lambda f, ch=None: None)
    monkeypatch.setattr(api.store, "source_for", lambda t, ch=None: None)
    monkeypatch.setattr(api, "_url_from_archive", lambda t: None)
    monkeypatch.setattr(api, "_profile", lambda t, ch: [])

    payload = api.title_payload("haider-2014_202202")
    assert payload["remediated"] is False
    assert payload["failures_before"] == 2
    assert payload["failures_after"] is None, \
        "absence of an after stage must not render as zero failures"
    assert payload["after"] == []
    assert payload["integrated_lufs_after"] is None


def test_a_title_that_really_passed_reports_zero(monkeypatch):
    """And the opposite: a genuine zero has to be reachable, or the flag is useless."""
    before = [{"stage": "before", "check": "true_peak", "spec": "s", "measured": 0.2,
               "target": -1.0, "unit": "dBTP", "passed": False, "auto_fixable": True,
               "detail": "", "run_at": "2026-09-08 09:00:00"}]
    after = [{"stage": "after", "check": "true_peak", "spec": "s", "measured": -1.4,
              "target": -1.0, "unit": "dBTP", "passed": True, "auto_fixable": True,
              "detail": "", "run_at": "2026-09-08 09:00:00"}]
    monkeypatch.setattr(api.store, "client", lambda: object())
    monkeypatch.setattr(api.store, "title_findings",
                        lambda t, ch=None: {"before": before, "after": after})
    monkeypatch.setattr(api.store, "loudness_series",
                        lambda t, ch=None, **k: {"stages": {}, "seconds": 0, "samples": 0})
    monkeypatch.setattr(api.store, "fail_windows", lambda t, ch=None: [])
    monkeypatch.setattr(api.store, "query_cost", lambda f, ch=None: None)
    monkeypatch.setattr(api.store, "source_for", lambda t, ch=None: None)
    monkeypatch.setattr(api, "_url_from_archive", lambda t: None)
    monkeypatch.setattr(api, "_profile", lambda t, ch: [])

    payload = api.title_payload("quevadis")
    assert payload["remediated"] is True
    assert payload["failures_after"] == 0


# --- the reproduce command must actually run -----------------------------


@pytest.mark.parametrize("url,ok", [
    ("https://archive.org/download/Cherchever1/", False),
    ("https://archive.org/download/Cherchever1", False),
    ("", False),
    ("https://archive.org/download/x/Cherchever1_full.ogv", True),
    ("https://archive.org/download/x/Fighting%20Caravans%20%28Gary%20Cooper%29%20-.ogv", True),
])
def test_reproduce_url_must_name_a_media_file(url, ok):
    """A URL ending at the identifier returns a directory listing and ffmpeg 404s."""
    assert api._names_a_file(url) is ok, url


def test_reproduce_command_carries_the_window_and_keeps_the_summary(monkeypatch):
    """Two flags decide whether a reviewer can check us at all.

    Without `-t N` the command measures the whole feature and returns a different
    number from the published one. With `-loglevel error` the ebur128 summary is
    suppressed and the reviewer gets nothing.
    """
    monkeypatch.setattr(api.store, "source_for", lambda t, ch=None: {
        "video_file": "Vicki (1953).mp4",
        "source_url": "https://archive.org/download/vicki-1953/Vicki%20%281953%29.mp4",
        "window_seconds": 300, "ingested_at": "now",
    })
    cmd = api.reproduce_command("vicki-1953")["command"]
    assert "-t 300" in cmd
    assert "-loglevel error" not in cmd
    assert cmd.endswith("-af ebur128 -f null -")
    assert "%20%281953%29" in cmd, "spaces and parentheses must stay percent-encoded"


def test_no_command_is_offered_when_it_cannot_be_made_runnable(monkeypatch):
    """Withholding beats publishing a command that 404s."""
    monkeypatch.setattr(api.store, "source_for", lambda t, ch=None: {
        "video_file": "", "source_url": "https://archive.org/download/Cherchever1/",
        "window_seconds": 60, "ingested_at": "now",
    })
    monkeypatch.setattr(api, "_url_from_archive", lambda t: None)
    assert api.reproduce_command("Cherchever1") is None


# --- the trace the UI renders --------------------------------------------


def test_a_blocked_query_is_reported_rather_than_read_as_a_clean_master():
    """A refusal must not be indistinguishable from finding nothing.

    The guardrail returns a dict so the model can retry, which means a blocked
    query leaves no trace of its own. Without surfacing it, "0 passages located"
    covers both "this master is fine" and "the tool layer would not let me ask",
    and those are opposite facts.
    """
    run = {"trace": [
        {"agent": "window_scout", "tool": "run_query",
         "refused": "refused: default.secrets is not a readable table"},
        {"agent": "window_scout", "text": '{"windows": []}'},
    ]}
    trace = api.agent_trace(run)
    assert trace["refused"] == 1
    assert trace["mcp_calls"] == 0
    assert trace["agents"][0]["refused"][0]["reason"].startswith("refused:")


def test_refusals_are_read_from_both_response_shapes():
    """ADK wraps a tool result differently by tool, so both shapes are handled."""
    from agent import pipeline as p

    assert p._refusal({"error": "refused: nope"}) == "refused: nope"
    assert p._refusal({"result": {"error": "refused: nope"}}) == "refused: nope"
    assert p._refusal({"error": "some other failure"}) is None
    assert p._refusal({"rows": []}) is None
    assert p._refusal("not a dict") is None


def test_agent_trace_groups_calls_under_the_agent_that_made_them():
    run = {
        "trace": [
            {"agent": "window_scout", "tool": "list_tables", "args": {"database": "deliverable"}},
            {"agent": "window_scout", "tool": "run_query",
             "args": {"query": "SELECT max(true_peak) FROM deliverable.loudness_samples"}},
            {"agent": "window_scout", "text": '{"windows": []}'},
            {"agent": "repair_planner", "text": '{"repairs": []}'},
            {"agent": "regression_auditor", "tool": "run_query",
             "args": {"query": "SELECT stage FROM deliverable.loudness_samples"}},
            {"agent": "regression_auditor", "text": "Safe to ship."},
        ]
    }
    trace = api.agent_trace(run)
    assert [a["agent"] for a in trace["agents"]] == \
        ["window_scout", "repair_planner", "regression_auditor"]
    assert trace["refused"] == 0
    assert trace["mcp_calls"] == 3
    scout = trace["agents"][0]
    assert scout["holds_model"] is True
    assert scout["tool_calls"][1]["query"].startswith("SELECT max(true_peak)")
    assert trace["agents"][1]["tool_calls"] == [], "the planner holds no tools"
    assert trace["agents"][2]["concluded"] == "Safe to ship."
