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

    if report is not None and report.findings:
        ch.insert(
            "deliverable.findings",
            [
                [run_id, title_id, title, stage, f.check, f.spec,
                 f.measured, f.target, f.unit,
                 1 if f.passed else 0, 1 if f.auto_fixable else 0, f.detail]
                for f in report.findings
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
        "SELECT title_id, title, last_run, failures_before, failures_after, verdict "
        "FROM deliverable.catalog_status ORDER BY failures_before DESC"
    )
    return [dict(zip(res.column_names, row)) for row in res.result_rows]


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
