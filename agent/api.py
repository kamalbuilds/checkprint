"""Payload builders for the web layer.

These exist so `web/server.py` stays thin handlers and every shape the UI renders
has one definition, in the same place as the code that produces the data. Each
function returns a plain JSON-serialisable dict and raises nothing the caller has
to interpret: an unreachable ClickHouse comes back as an explicit flag, not an
exception the handler has to guess about.
"""

from __future__ import annotations

import urllib.parse
from functools import lru_cache

from agent import graph
from qc import archive, measure as m, store

#: The exact command a stranger runs to reproduce a headline number.
#:
#: Two details are load-bearing. `-t {seconds}` must be there, because the published
#: figure is a measurement of the first N seconds and a command without it measures
#: the whole feature and returns something else. And `-loglevel error` must NOT be
#: there, because it suppresses the ebur128 summary and the reviewer gets empty
#: output.
REPRODUCE = (
    'ffmpeg -hide_banner -nostats -t {seconds} -i "{url}" -af ebur128 -f null -'
)

#: A URL with no filename on it, e.g. archive.org/download/<id>/, returns a
#: directory listing rather than a media file, so the command 404s. On a product
#: whose strongest claim is "check my numbers yourself", shipping a broken check is
#: worse than shipping none, so a command that does not name a file is withheld.
_MEDIA_EXT = (".mp4", ".m4v", ".ogv", ".mpeg", ".mpg", ".avi", ".mkv", ".mov", ".webm")


def reproduce_command(title_id: str, seconds: int | None = None, ch=None) -> dict | None:
    """The runnable command for one title, or None when it cannot be made runnable.

    The source URL comes from `deliverable.sources`, which records the file each
    measurement was actually taken from. For a title measured before that table
    existed there is no stored provenance, so the filename is resolved from
    archive.org once and cached; and if even that fails, nothing is returned rather
    than a command that 404s.
    """
    src = store.source_for(title_id, ch=ch)
    url = (src or {}).get("source_url") or ""
    window = (src or {}).get("window_seconds") or seconds

    if not _names_a_file(url):
        url = _url_from_archive(title_id) or ""

    if not _names_a_file(url) or not window:
        return None
    return {
        "command": REPRODUCE.format(seconds=int(window), url=url),
        "url": url,
        "window_seconds": int(window),
        "note": "the window flag is part of the command on purpose: without it "
                "ffmpeg measures the whole feature and returns a different number "
                "from the one published against a bounded window",
    }


def _names_a_file(url: str) -> bool:
    """Does this URL end in a media filename rather than a directory?"""
    if not url:
        return False
    tail = urllib.parse.urlparse(url).path.rsplit("/", 1)[-1]
    return bool(tail) and urllib.parse.unquote(tail).lower().endswith(_MEDIA_EXT)


@lru_cache(maxsize=256)
def _url_from_archive(title_id: str) -> str | None:
    """Resolve the filename from archive.org. Cached: it is a network round-trip.

    Percent-encoded by `archive.download_url`, which matters more than it looks:
    real filenames in this corpus include `Fighting Caravans (Gary Cooper) -.ogv`,
    and an unencoded space or parenthesis breaks the command the moment somebody
    pastes it into a shell.
    """
    try:
        picked = archive.pick_files(title_id)
    except Exception:
        return None
    if not picked.get("video"):
        return None
    return archive.download_url(title_id, picked["video"])


def topology() -> dict:
    """The graph, as the UI should draw it: which nodes hold a model, which run ffmpeg."""
    return {
        "framework": "google-adk",
        "primitive": "google.adk.workflow.Workflow",
        "workflow": "delivery_qc",
        "model": graph.A.MODEL,
        "mcp_server": "mcp-clickhouse",
        "nodes": graph.TOPOLOGY,
        "agents": [
            {
                "name": "window_scout",
                "tools": "mcp-clickhouse via ADK McpToolset",
                "decides": "which passages of this master the repair may touch",
                "removing_it": "on a master where a passage would clip once the "
                               "programme is lifted to target, removing it leaves one "
                               "gain over the whole programme and the rendered file "
                               "differs, which is what "
                               "test_removing_the_scout_changes_the_repaired_audio "
                               "renders twice and compares. No title in this "
                               "public-domain corpus has that collision, so here the "
                               "scout returns no windows, the repair is that single "
                               "gain already, and the delivered file is identical "
                               "without it",
            },
            {
                "name": "repair_planner",
                "tools": "none, deliberately: it cannot query, so it cannot invent a passage",
                "decides": "which repairs run and in what order, from a fixed whitelist",
                "removing_it": "nothing is planned, remediate no-ops and the run raises "
                               "rather than reporting an unchanged file as repaired",
            },
            {
                "name": "regression_auditor",
                "tools": "mcp-clickhouse via ADK McpToolset",
                "decides": "whether the repair damaged anything the failure count cannot see",
                "removing_it": "the ship or do-not-ship line loses its only evidence "
                               "of collateral damage",
            },
        ],
    }


def title_payload(title_id: str, ch=None) -> dict:
    """Everything the QC bay needs for a title that was already measured.

    One call, straight out of ClickHouse, so the first screen a judge sees is a
    real master with real numbers on it rather than an empty meter and a button.

    The `remediated` flag carries a distinction that must not be lost in
    rendering: a title with no after-stage rows has NOT been repaired to zero
    failures, it has no after-stage data at all. Showing "6 to 0" for that title
    would advertise the most impressive repair in the catalog for a run that never
    happened, on a product whose whole claim is that it re-measures to prove the
    repair. So `after` is an empty list and `remediated` is false, and the UI must
    render absence rather than a zero.
    """
    ch = ch or store.client()
    findings = store.title_findings(title_id, ch=ch)
    series = store.loudness_series(title_id, ch=ch)
    windows = store.fail_windows(title_id, ch=ch)

    before = findings["before"]
    after = findings["after"]
    integrated = next(
        (f["measured"] for f in before if f["check"] == "integrated_loudness_ebu_r128"),
        None,
    )
    integrated_after = next(
        (f["measured"] for f in after if f["check"] == "integrated_loudness_ebu_r128"),
        None,
    )

    seconds = int(series.get("seconds") or 0) or None
    return {
        "title_id": title_id,
        "title": next((f.get("title") for f in before if f.get("title")), title_id),
        "measured": bool(before),
        "remediated": bool(after),
        "run_at": before[0]["run_at"] if before else None,
        "before": before,
        "after": after,
        "failures_before": sum(1 for f in before if not f["passed"]),
        # None, not 0. A title nobody remediated has no after-stage failure count,
        # and 0 is the number that reads as a perfect repair.
        "failures_after": (sum(1 for f in after if not f["passed"])) if after else None,
        "integrated_lufs": integrated,
        "integrated_lufs_after": integrated_after,
        "target_lufs": m.EBU_R128_TARGET_LUFS,
        "tolerance_lu": m.EBU_R128_TOLERANCE_LU,
        "true_peak_ceiling_dbtp": m.TRUE_PEAK_CEILING_DBTP,
        "series": series,
        "windows": windows,
        "profile": _profile(title_id, ch),
        "query_cost": store.query_cost("FROM deliverable.loudness_samples", ch=ch),
        "reproduce": reproduce_command(title_id, seconds=seconds, ch=ch),
    }


def _profile(title_id: str, ch) -> list[dict]:
    try:
        rows = ch.query(
            """SELECT stage, samples, quietest_short_term_lufs, p05_short_term_lufs,
                      median_short_term_lufs, p95_short_term_lufs,
                      loudest_short_term_lufs, sustained_range_lu
               FROM deliverable.worst_windows
               WHERE title_id = %(t)s ORDER BY stage DESC""",
            parameters={"t": title_id},
        ).result_rows
    except Exception:
        return []
    cols = ["stage", "samples", "min", "p05", "median", "p95", "max", "sustained_range_lu"]
    return [dict(zip(cols, r)) for r in rows]


def agent_trace(run: dict) -> dict:
    """Regroup a finished run's flat ADK event trace by agent, for rendering.

    Per agent: what it was asked, every tool call with its arguments, and what it
    concluded. The queries are the point. An agent's reasoning is only worth
    reading if the SQL behind it is on the same screen.
    """
    asked = {
        "window_scout": "Which passages of this master fail, and what SQL proves it?",
        "repair_planner": "Which repairs run, in what order, and what needs a human?",
        "regression_auditor": "Did the repair damage anything the failure count cannot see?",
    }
    grouped: dict[str, dict] = {}
    for entry in run.get("trace") or []:
        name = entry.get("agent") or "workflow"
        slot = grouped.setdefault(name, {
            "agent": name,
            "asked": asked.get(name, ""),
            "holds_model": name in asked,
            "tool_calls": [],
            "concluded": "",
        })
        if entry.get("refused"):
            slot.setdefault("refused", []).append(
                {"tool": entry.get("tool"), "reason": entry["refused"]})
        elif entry.get("tool"):
            slot["tool_calls"].append({
                "tool": entry["tool"],
                "query": (entry.get("args") or {}).get("query"),
                "args": entry.get("args") or {},
            })
        elif entry.get("text"):
            slot["concluded"] = entry["text"]

    order = ["window_scout", "repair_planner", "regression_auditor"]
    agents = [grouped[n] for n in order if n in grouped]
    agents += [v for k, v in grouped.items() if k not in order]
    return {
        "framework": "google-adk",
        "primitive": "google.adk.workflow.Workflow",
        "model": graph.A.MODEL,
        "agents": agents,
        "mcp_calls": sum(len(a["tool_calls"]) for a in agents),
        # Surfaced rather than swallowed. If the guardrail blocked a query, the run
        # found fewer passages than it otherwise would have, and that is a different
        # fact from a master with nothing wrong with it.
        "refused": sum(len(a.get("refused") or []) for a in agents),
    }


def located_windows(run: dict) -> list[dict]:
    """The passages this run located, with the SQL that located each of them."""
    for step in run.get("steps") or []:
        if step.get("step") == "locate":
            return (step.get("data") or {}).get("windows") or []
    return []
