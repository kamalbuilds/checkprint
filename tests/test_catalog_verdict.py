"""The catalog verdict must discriminate, not flatter.

`catalog_status` is the screen a judge and an operator both read first, so its
verdict column has to mean something. These tests pin the four branches against
synthetic findings rows, using a throwaway database so they never touch real data.

Skipped automatically when no ClickHouse is reachable.
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from qc import store  # noqa: E402

DB = f"deliverable_test_{uuid.uuid4().hex[:8]}"


def _client():
    try:
        ch = store.client()
        ch.query("SELECT 1")
        return ch
    except Exception as exc:  # no ClickHouse in this environment
        pytest.skip(f"ClickHouse unavailable: {str(exc)[:80]}")


@pytest.fixture(scope="module")
def ch():
    client = _client()
    client.command(f"CREATE DATABASE IF NOT EXISTS {DB}")
    client.command(f"""
        CREATE TABLE {DB}.findings (
            run_id UUID, title_id String, title String,
            run_at DateTime DEFAULT now(), stage LowCardinality(String),
            check LowCardinality(String), spec LowCardinality(String),
            measured Nullable(Float64), target Nullable(Float64),
            unit LowCardinality(String), passed UInt8, auto_fixable UInt8, detail String
        ) ENGINE = MergeTree ORDER BY (title_id, check, stage, run_at)
    """)
    # Mirror the production view, retargeted at the test database.
    view_sql = (Path(__file__).parent.parent / "qc" / "schema.sql").read_text()
    start = view_sql.index("CREATE OR REPLACE VIEW deliverable.catalog_status")
    end = view_sql.index(";", start)
    client.command(view_sql[start:end].replace("deliverable.", f"{DB}."))
    yield client
    client.command(f"DROP DATABASE IF EXISTS {DB}")


def _insert(ch, title_id: str, before_fail: int, after_fail: int, after_rows: bool = True):
    run = uuid.uuid4()
    rows = []
    for i in range(3):
        rows.append([run, title_id, title_id, "before", f"check_{i}", "spec", 1.0, 1.0,
                     "u", 0 if i < before_fail else 1, 1, ""])
    if after_rows:
        for i in range(3):
            rows.append([run, title_id, title_id, "after", f"check_{i}", "spec", 1.0, 1.0,
                         "u", 0 if i < after_fail else 1, 1, ""])
    ch.insert(
        f"{DB}.findings", rows,
        column_names=["run_id", "title_id", "title", "stage", "check", "spec",
                      "measured", "target", "unit", "passed", "auto_fixable", "detail"],
    )


def _verdict(ch, title_id: str) -> str:
    rows = ch.query(
        f"SELECT verdict FROM {DB}.catalog_status WHERE title_id = %(t)s",
        parameters={"t": title_id},
    ).result_rows
    assert rows, f"no catalog row for {title_id}"
    return rows[0][0]


def test_all_checks_pass_after_repair_is_delivery_ready(ch):
    _insert(ch, "clean", before_fail=2, after_fail=0)
    assert _verdict(ch, "clean") == "delivery ready"


def test_fewer_failures_after_repair_is_improved_not_still_failing(ch):
    """The case that motivated this: real improvement must not read as no-op.

    A subtitle pass that took one title from 44 short cues to 12 was being
    reported identically to a title nobody touched.
    """
    _insert(ch, "better", before_fail=3, after_fail=1)
    assert _verdict(ch, "better") == "improved, needs human"


def test_no_improvement_still_reads_as_still_failing(ch):
    """The predicate must be able to say no. Otherwise it flatters everything."""
    _insert(ch, "stuck", before_fail=2, after_fail=2)
    assert _verdict(ch, "stuck") == "still failing"


def test_more_failures_after_repair_is_still_failing(ch):
    """A repair that made things worse must never read as an improvement."""
    _insert(ch, "worse", before_fail=1, after_fail=3)
    assert _verdict(ch, "worse") == "still failing"


def test_never_remediated_is_reported_as_such(ch):
    _insert(ch, "untouched", before_fail=2, after_fail=0, after_rows=False)
    assert _verdict(ch, "untouched") == "not remediated"


def test_the_verdict_column_is_not_constant(ch):
    """A column with one value carries no information, however good it looks."""
    verdicts = {r[0] for r in ch.query(f"SELECT DISTINCT verdict FROM {DB}.catalog_status").result_rows}
    assert len(verdicts) >= 3, f"verdict barely discriminates: {verdicts}"
