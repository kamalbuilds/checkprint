"""ClickHouse store for QC telemetry.

Connects to ClickHouse Cloud or a self-hosted cluster (the track permits either).
Credentials come from the environment so nothing is baked into the repo.
"""

from __future__ import annotations

import os
import re
import subprocess
import uuid
from pathlib import Path

import clickhouse_connect

_SAMPLE = re.compile(
    r"t:\s*([\d.]+)\s+TARGET:\s*-?\d+\s*LUFS\s+"
    r"M:\s*(-?[\d.inf]+)\s+S:\s*(-?[\d.inf]+)\s+"
    r"I:\s*(-?[\d.inf]+)\s*LUFS\s+LRA:\s*(-?[\d.inf]+)\s*LU"
    r"(?:\s+FTPK:\s*(-?[\d.inf]+))?"
)


def _f(raw: str | None) -> float:
    if raw is None:
        return -120.0
    if "inf" in raw:
        return -120.0
    try:
        return float(raw)
    except ValueError:
        return -120.0


def client():
    """ClickHouse client from env. Works for Cloud and for a local server."""
    host = os.getenv("CLICKHOUSE_HOST", "localhost")
    secure = os.getenv("CLICKHOUSE_SECURE", "false").lower() == "true"
    default_port = 8443 if secure else 8123
    return clickhouse_connect.get_client(
        host=host,
        port=int(os.getenv("CLICKHOUSE_PORT", default_port)),
        username=os.getenv("CLICKHOUSE_USER", "default"),
        password=os.getenv("CLICKHOUSE_PASSWORD", ""),
        secure=secure,
    )


def apply_schema(ch=None) -> None:
    ch = ch or client()
    sql = (Path(__file__).with_name("schema.sql")).read_text()
    for stmt in _statements(sql):
        ch.command(stmt)


def _statements(sql: str) -> list[str]:
    """Split schema.sql into executable statements.

    Two things make a naive sql.split(";") wrong here, both observed in production:

    1. A block of leading `--` comments becomes its own fragment once the previous
       ';' is consumed, and ClickHouse rejects a comment-only statement with
       `Code: 62. DB::Exception: Empty query. (SYNTAX_ERROR)`.
    2. A `--` comment containing an apostrophe ("a title nobody touched") makes any
       quote-aware splitter think a string literal is open, so the split lands in
       the middle of prose and ClickHouse reports
       `Syntax error: failed at position 1 (calling)`.

    So comments are stripped first, and only then is the SQL split on ';'. The
    comments are worth keeping in the file for whoever reads the schema; they are
    simply not worth sending to the server.
    """
    lines = []
    for line in sql.splitlines():
        stripped = line.strip()
        if stripped.startswith("--"):
            continue
        # Trailing comment on a line of SQL. Only safe to cut when the '--' is not
        # inside a string literal, so require an even number of quotes before it.
        idx = line.find("--")
        if idx != -1 and line[:idx].count("'") % 2 == 0:
            line = line[:idx]
        if line.strip():
            lines.append(line)

    return [stmt.strip() for stmt in "\n".join(lines).split(";") if stmt.strip()]


def loudness_timeseries(path: str | Path, seconds: int | None = None) -> list[tuple]:
    """Every 100ms ebur128 reading, parsed from ffmpeg's per-frame log.

    This is the volume that justifies ClickHouse: ~10 rows/sec of content.
    """
    cmd = ["ffmpeg", "-hide_banner", "-nostats", "-i", str(path)]
    if seconds:
        cmd += ["-t", str(seconds)]
    cmd += ["-af", "ebur128=peak=true", "-f", "null", "-"]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)

    rows = []
    for m in _SAMPLE.finditer(proc.stderr):
        rows.append((
            float(m.group(1)),
            _f(m.group(2)),
            _f(m.group(3)),
            _f(m.group(4)),
            _f(m.group(6)),
        ))
    return rows


def store_run(title_id: str, title: str, stage: str, report, samples: list[tuple],
              ch=None, run_id: uuid.UUID | None = None) -> uuid.UUID:
    """Persist one QC run: the verdicts and the underlying time series."""
    ch = ch or client()
    run_id = run_id or uuid.uuid4()

    # A check that was not examined gets no row. `passed` is a UInt8 with nowhere
    # to put "unexamined", and a 0 there would read as a failure while a 1 would
    # read as a clean picture on an asset with no picture. The report keeps the
    # unexamined entries and the API serves them; the verdict tables only hold
    # checks that were actually run.
    recorded = [f for f in (report.findings if report is not None else [])
                if not getattr(f, "not_measured", False)]
    if recorded:
        ch.insert(
            "deliverable.findings",
            [
                [run_id, title_id, title, stage, f.check, f.spec,
                 f.measured, f.target, f.unit,
                 1 if f.passed else 0, 1 if f.auto_fixable else 0, f.detail]
                for f in recorded
            ],
            column_names=["run_id", "title_id", "title", "stage", "check", "spec",
                          "measured", "target", "unit", "passed", "auto_fixable", "detail"],
        )

    if samples:
        ch.insert(
            "deliverable.loudness_samples",
            [[run_id, title_id, stage, t, mo, st, i, tp] for t, mo, st, i, tp in samples],
            column_names=["run_id", "title_id", "stage", "t_seconds",
                          "momentary", "short_term", "integrated", "true_peak"],
        )

    return run_id


def catalog(ch=None) -> list[dict]:
    ch = ch or client()
    res = ch.query(
        "SELECT title_id, title, last_run, failures_before, failures_after, cps_cues_before, cps_cues_after, short_cues_before, short_cues_after, verdict "
        "FROM deliverable.catalog_status ORDER BY failures_before DESC"
    )
    return [dict(zip(res.column_names, row)) for row in res.result_rows]


def store_source(title_id: str, title: str, video_file: str, source_url: str,
                 window_seconds: int, bytes_fetched: int = 0, ch=None) -> None:
    """Record the exact file and window a measurement was taken from."""
    ch = ch or client()
    ch.insert(
        "deliverable.sources",
        [[title_id, title, video_file, source_url, int(window_seconds), int(bytes_fetched)]],
        column_names=["title_id", "title", "video_file", "source_url",
                      "window_seconds", "bytes_fetched"],
    )


def source_for(title_id: str, ch=None) -> dict | None:
    """The file and window this title was last measured from, or None."""
    ch = ch or client()
    try:
        res = ch.query(
            "SELECT video_file, source_url, window_seconds, ingested_at "
            "FROM deliverable.sources WHERE title_id = %(t)s "
            "ORDER BY ingested_at DESC LIMIT 1",
            parameters={"t": title_id},
        ).result_rows
    except Exception:
        return None
    if not res:
        return None
    return {"video_file": res[0][0], "source_url": res[0][1],
            "window_seconds": int(res[0][2]), "ingested_at": str(res[0][3])}


def store_windows(run_id: uuid.UUID, title_id: str, title: str,
                  windows: list[dict], ch=None) -> int:
    """Persist the passages an agent located, with the SQL that located them."""
    if not windows:
        return 0
    ch = ch or client()
    ch.insert(
        "deliverable.fail_windows",
        [
            [run_id, title_id, title, i,
             float(w.get("start_s", 0.0)), float(w.get("end_s", 0.0)),
             str(w.get("metric", "")), float(w.get("measured") or 0.0),
             float(w.get("target") or 0.0), str(w.get("unit", "")),
             float(w.get("gain_db") or 0.0), 1 if w.get("treated") else 0,
             str(w.get("reason", "")), str(w.get("sql", ""))]
            for i, w in enumerate(windows)
        ],
        column_names=["run_id", "title_id", "title", "ord", "start_s", "end_s",
                      "metric", "measured", "target", "unit", "gain_db", "treated",
                      "reason", "sql"],
    )
    return len(windows)


def fail_windows(title_id: str, ch=None) -> list[dict]:
    """The passages located on this title's most recent run."""
    ch = ch or client()
    res = ch.query(
        """SELECT ord, start_s, end_s, metric, measured, target, unit,
                  gain_db, treated, reason, sql
           FROM deliverable.fail_windows
           WHERE title_id = %(t)s
             AND run_id = (SELECT argMax(run_id, found_at)
                           FROM deliverable.fail_windows WHERE title_id = %(t)s)
           ORDER BY start_s""",
        parameters={"t": title_id},
    )
    return [dict(zip(res.column_names, row)) for row in res.result_rows]


def support_for_windows(title_id: str, windows: list[dict], stage: str = "before",
                        ch=None) -> list[dict]:
    """Re-derive each window straight from the samples, and drop the unsupported.

    The scout is a language model with a SQL tool. It is told to return only what
    its queries returned, and it is a bad idea to take that on trust: a window that
    no data supports would send the remediator to attenuate a passage of somebody's
    master for no reason. So every window comes back here and is measured again
    against `loudness_samples`. `measured` is replaced with the value the table
    actually holds, and a window the table does not support is dropped with a
    reason rather than silently kept.
    """
    ch = ch or client()
    checked: list[dict] = []
    for w in windows:
        try:
            start = float(w.get("start_s"))
            end = float(w.get("end_s"))
        except (TypeError, ValueError):
            continue
        if end <= start:
            continue
        res = ch.query(
            """SELECT count() AS rows, round(toFloat64(max(true_peak)), 2) AS peak,
                      round(toFloat64(max(short_term)), 2) AS loudest,
                      round(toFloat64(min(short_term)), 2) AS quietest
               FROM deliverable.loudness_samples
               WHERE title_id = %(t)s AND stage = %(s)s
                 AND t_seconds >= %(a)s AND t_seconds <= %(b)s""",
            parameters={"t": title_id, "s": stage, "a": start, "b": end},
        ).result_rows
        rows, peak, loudest, quietest = (res[0] if res else (0, None, None, None))
        out = dict(w)
        out["rows"] = int(rows or 0)
        if not rows:
            out["supported"] = False
            out["reason"] = "no samples in this range; the window is not in the data"
        else:
            out["supported"] = True
            out["measured"] = peak if w.get("metric") == "true_peak" else loudest
            out["loudest_short_term"] = loudest
            out["quietest_short_term"] = quietest
        checked.append(out)
    return checked


def title_findings(title_id: str, ch=None) -> dict:
    """Both stages of this title's most recent run, ready to render."""
    ch = ch or client()
    res = ch.query(
        """SELECT stage, check, spec, measured, target, unit, passed,
                  auto_fixable, detail, run_at
           FROM deliverable.findings
           WHERE title_id = %(t)s
             AND run_id = (SELECT argMax(run_id, run_at)
                           FROM deliverable.findings WHERE title_id = %(t)s)
           ORDER BY stage, check""",
        parameters={"t": title_id},
    )
    rows = [dict(zip(res.column_names, r)) for r in res.result_rows]
    for r in rows:
        r["run_at"] = str(r["run_at"])
        r["passed"] = bool(r["passed"])
        r["auto_fixable"] = bool(r["auto_fixable"])
    return {
        "before": [r for r in rows if r["stage"] == "before"],
        "after": [r for r in rows if r["stage"] == "after"],
    }


def loudness_series(title_id: str, buckets: int = 420, ch=None) -> dict:
    """The 100ms series, aggregated down to something a screen can draw.

    Not a LIMIT and not a sample: every row in the window contributes. Each bucket
    reports the loudest momentary reading in it, the median short-term, and the
    highest true peak, because a delivery defect is a maximum, and thinning by
    taking every Nth row would step straight over the one 400ms burst that fails
    the ceiling. This is a groupBy over the whole per-title partition, which is
    what a column store is for and what a row store would make you wait for.
    """
    ch = ch or client()
    span = ch.query(
        "SELECT min(t_seconds), max(t_seconds), count() "
        "FROM deliverable.loudness_samples WHERE title_id = %(t)s",
        parameters={"t": title_id},
    ).result_rows
    if not span or not span[0][2]:
        return {"title_id": title_id, "stages": {}, "seconds": 0.0, "samples": 0}

    t0, t1, total = float(span[0][0]), float(span[0][1]), int(span[0][2])
    width = max((t1 - t0) / max(buckets, 1), 0.05)

    res = ch.query(
        """SELECT stage,
                  toUInt32(floor((t_seconds - %(t0)s) / %(w)s))       AS b,
                  round(toFloat64(min(t_seconds)), 2)                 AS t,
                  round(toFloat64(max(momentary)), 1)                 AS m,
                  round(toFloat64(quantileTDigest(0.5)(short_term)), 1) AS s,
                  round(toFloat64(max(true_peak)), 1)                 AS tp
           FROM deliverable.loudness_samples
           WHERE title_id = %(id)s AND short_term > -70
           GROUP BY stage, b
           ORDER BY stage, b""",
        parameters={"id": title_id, "t0": t0, "w": width},
    )
    stages: dict[str, list[dict]] = {}
    for stage, _b, t, m, s, tp in res.result_rows:
        stages.setdefault(stage, []).append({"t": t, "m": m, "s": s, "tp": tp})

    return {
        "title_id": title_id,
        "stages": stages,
        "seconds": round(t1 - t0, 1),
        "samples": total,
        "bucket_seconds": round(width, 3),
        "computed_with": "groupBy over deliverable.loudness_samples, "
                         "max(momentary) and quantileTDigest(0.5)(short_term) per bucket",
    }


def query_cost(fragment: str, ch=None) -> dict | None:
    """What a query actually cost, from ClickHouse's own query_log.

    Reported rather than asserted. The filter must exclude DDL and zero-row reads:
    matching a table name alone also matches the CREATE OR REPLACE VIEW issued at
    startup, which reads nothing, so the panel once proudly reported "0 rows read"
    for a query that had scanned 9,316.
    """
    ch = ch or client()
    try:
        rows = ch.query(
            """SELECT query_duration_ms, read_rows, formatReadableSize(read_bytes)
               FROM system.query_log
               WHERE type = 'QueryFinish'
                 AND query LIKE %(f)s
                 AND query NOT LIKE 'CREATE%%'
                 AND read_rows > 0
                 AND event_time > now() - INTERVAL 10 MINUTE
               ORDER BY event_time DESC LIMIT 1""",
            parameters={"f": f"%{fragment}%"},
        ).result_rows
    except Exception:
        return None
    if not rows:
        return None
    return {"duration_ms": rows[0][0], "rows_read": rows[0][1],
            "bytes_read": rows[0][2], "source": "system.query_log"}


def worst_window(title_id: str, stage: str = "before", ch=None) -> dict | None:
    """The quietest sustained passage, i.e. where an operator should go and listen."""
    ch = ch or client()
    res = ch.query(
        "SELECT quietest_short_term_lufs, loudest_short_term_lufs, quietest_at_seconds "
        "FROM deliverable.worst_windows WHERE title_id = %(t)s AND stage = %(s)s",
        parameters={"t": title_id, "s": stage},
    )
    if not res.result_rows:
        return None
    return dict(zip(res.column_names, res.result_rows[0]))
