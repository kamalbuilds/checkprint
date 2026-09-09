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


# --- worst_windows: percentiles over the 100ms sample stream --------------


# --- cue counts: the catalog must show the SIZE of the problem -------------

# One run id shared by these titles: catalog_status reads the latest run only, so
# a title's before and after rows must belong to the same run to appear together.
_RUN = uuid.uuid4()


def _insert_subs(ch, title_id, over_cps, short_cues, stage="before", cue_count=516):
    """One realistic subtitle finding pair, worded exactly as measure.py writes them."""
    ch.insert(
        f"{DB}.findings",
        [
            [_RUN, title_id, title_id, stage,
             "subtitle_reading_speed", "spec",
             round(100 * over_cps / cue_count, 1), 0.0, "% of cues",
             1 if over_cps == 0 else 0, 1,
             f"{over_cps} of {cue_count} cues exceed the reading-speed limit"],
            [_RUN, title_id, title_id, stage,
             "subtitle_min_duration", "spec",
             float(short_cues), 0.0, "cues",
             1 if short_cues == 0 else 0, 1,
             f"{short_cues} cues below minimum duration"],
        ],
        column_names=["run_id", "title_id", "title", "stage", "check", "spec",
                      "measured", "target", "unit", "passed", "auto_fixable", "detail"],
    )


def _cues(ch, title_id) -> dict:
    cols = "cps_cues_before,cps_cues_after,short_cues_before,short_cues_after"
    rows = ch.query(
        f"SELECT {cols} FROM {DB}.catalog_status WHERE title_id = %(t)s",
        parameters={"t": title_id},
    ).result_rows
    assert rows, f"no catalog row for {title_id}"
    return dict(zip(cols.split(","), rows[0]))


def test_catalog_reports_cue_counts_not_check_counts(ch):
    """The bug this column exists for.

    Three cues over the reading-speed limit and two cues under minimum duration
    are ONE failed check each. If the catalog reports 1 and 1, a title with 42
    illegal cues is indistinguishable from a title with 1, which is exactly what
    the old failures_before column did.
    """
    _insert_subs(ch, "cues_small", over_cps=3, short_cues=2)
    got = _cues(ch, "cues_small")
    assert got["cps_cues_before"] == 3, f"reported check count, not cue count: {got}"
    assert got["short_cues_before"] == 2, f"reported check count, not cue count: {got}"


def test_a_big_offender_outranks_a_small_one(ch):
    """42 illegal cues must be visibly larger than 1, on the catalog, before drilldown."""
    _insert_subs(ch, "cues_big", over_cps=42, short_cues=17)
    _insert_subs(ch, "cues_one", over_cps=1, short_cues=0)
    big, one = _cues(ch, "cues_big"), _cues(ch, "cues_one")
    assert big["cps_cues_before"] == 42 and one["cps_cues_before"] == 1
    assert big["cps_cues_before"] > one["cps_cues_before"]


def test_remediation_shrinks_the_cue_counts(ch):
    """before and after must be separate numbers, or the repair is invisible."""
    _insert_subs(ch, "cues_fixed", over_cps=20, short_cues=44, stage="before")
    _insert_subs(ch, "cues_fixed", over_cps=4, short_cues=12, stage="after")
    got = _cues(ch, "cues_fixed")
    assert (got["cps_cues_before"], got["cps_cues_after"]) == (20, 4), got
    assert (got["short_cues_before"], got["short_cues_after"]) == (44, 12), got


def test_a_title_with_no_subtitle_findings_reports_zero_not_blank(ch):
    _insert(ch, "no_subs", before_fail=1, after_fail=0)
    got = _cues(ch, "no_subs")
    assert all(v == 0 for v in got.values()), got


@pytest.fixture(scope="module")
def ch_samples(ch):
    """A loudness_samples table plus the worst_windows view, in the test database."""
    ch.command(f"""
        CREATE TABLE IF NOT EXISTS {DB}.loudness_samples (
            run_id UUID, title_id String, stage LowCardinality(String),
            t_seconds Float32, momentary Float32, short_term Float32,
            integrated Float32, true_peak Float32
        ) ENGINE = MergeTree ORDER BY (title_id, stage, t_seconds)
    """)
    view_sql = (Path(__file__).parent.parent / "qc" / "schema.sql").read_text()
    start = view_sql.index("CREATE OR REPLACE VIEW deliverable.worst_windows")
    end = view_sql.index(";", view_sql.index("GROUP BY title_id, stage", start))
    ch.command(view_sql[start:end].replace("deliverable.", f"{DB}."))

    run = uuid.uuid4()
    # 998 samples at -23, plus two one-sample spikes. A spike is not a sustained
    # problem, which is the whole reason percentiles are used here.
    rows = [[run, "pct", "before", float(i) / 10, -23.0, -23.0, -23.0, -5.0] for i in range(998)]
    rows.append([run, "pct", "before", 99.8, -3.0, -3.0, -23.0, -5.0])
    rows.append([run, "pct", "before", 99.9, -60.0, -60.0, -23.0, -5.0])
    ch.insert(f"{DB}.loudness_samples", rows,
              column_names=["run_id", "title_id", "stage", "t_seconds", "momentary",
                            "short_term", "integrated", "true_peak"])

    # A second title whose values are NOT exactly representable in Float32.
    # This is what actually exercises the toFloat64 cast: -23.0 and -60.0 round-trip
    # exactly, so a title built only from those can never reveal the noise bug.
    # -40.3 stored as Float32 and read back through round(x, 1) without widening
    # yields -40.29999923706055, which is what shipped to the UI before the fix.
    noisy = [[run, "noise", "before", float(i) / 10, -40.3, -40.3, -40.3, -5.1]
             for i in range(600)]
    noisy += [[run, "noise", "before", 60.0 + float(i) / 10, -12.7, -12.7, -40.3, -5.1]
              for i in range(400)]
    ch.insert(f"{DB}.loudness_samples", noisy,
              column_names=["run_id", "title_id", "stage", "t_seconds", "momentary",
                            "short_term", "integrated", "true_peak"])
    return ch


def _window(ch, title_id="pct"):
    cols = ("quietest_short_term_lufs,p05_short_term_lufs,median_short_term_lufs,"
            "p95_short_term_lufs,loudest_short_term_lufs,sustained_range_lu,samples")
    row = ch.query(
        f"SELECT {cols} FROM {DB}.worst_windows WHERE title_id = %(t)s AND stage = 'before'",
        parameters={"t": title_id},
    ).result_rows[0]
    return dict(zip(cols.split(","), row))


def test_percentiles_ignore_a_single_spike_but_min_max_do_not(ch_samples):
    """The point of the percentiles: one 100ms spike is not a delivery problem.

    If p95 tracked max, an operator would be sent to a frame where nothing is
    audibly wrong, which is what min()/max() alone did before.
    """
    w = _window(ch_samples)
    assert w["loudest_short_term_lufs"] == -3.0, "max must still show the spike"
    assert w["quietest_short_term_lufs"] == -60.0, "min must still show the dropout"
    assert w["p95_short_term_lufs"] == -23.0, f"p95 followed the spike: {w}"
    assert w["p05_short_term_lufs"] == -23.0, f"p05 followed the dropout: {w}"


def test_sustained_range_is_zero_for_a_flat_title(ch_samples):
    """A consistently-mastered title has no sustained spread, spikes notwithstanding."""
    assert _window(ch_samples)["sustained_range_lu"] == 0.0


def test_percentiles_are_not_float32_noise(ch_samples):
    """round() on ClickHouse's Float32 quantile leaks its binary representation.

    Observed before the toFloat64 cast: p05 came back as -40.29999923706055
    instead of -40.3 and rendered as noise in the UI.

    This asserts on the 'noise' title deliberately. An earlier version of this
    test used only -23.0 and -60.0, which are exactly representable in Float32
    and therefore survive round() unwidened, so the test passed with the cast
    removed and proved nothing. -40.3 and -12.7 are not exact, and do reveal it.
    """
    w = _window(ch_samples, "noise")
    for key in ("p05_short_term_lufs", "median_short_term_lufs",
                "p95_short_term_lufs", "sustained_range_lu"):
        value = w[key]
        assert round(value, 1) == value, f"{key} = {value!r} is Float32 noise, not a 1dp number"


# --- schema.sql must stay executable ---------------------------------------


def test_schema_splitter_emits_no_comment_only_statements():
    """A comment block before a statement must not be sent as its own query.

    ClickHouse answers a comment-only query with
    `Code: 62. DB::Exception: Empty query. (SYNTAX_ERROR)`, which broke schema
    application on startup the moment a view grew a multi-line comment. Naive
    sql.split(";") produced two such fragments.
    """
    from qc.store import _statements

    sql = (Path(__file__).parent.parent / "qc" / "schema.sql").read_text()
    for stmt in _statements(sql):
        assert any(
            line.strip() and not line.strip().startswith("--")
            for line in stmt.splitlines()
        ), f"comment-only statement would be sent to ClickHouse:\n{stmt[:200]}"


def test_an_apostrophe_in_a_comment_does_not_desync_the_split():
    """A `--` comment containing an apostrophe must not break statement splitting.

    Observed for real: a comment reading "a title nobody touched" made the split
    land mid-prose and ClickHouse reported
    `Syntax error: failed at position 1 (calling)`. Every statement must still
    begin with a SQL keyword.
    """
    from qc.store import _statements

    sql = """
-- A comment with an apostrophe: a title nobody touched is both odd and fine.
CREATE TABLE a (x Int8) ENGINE = Memory;
-- Another one, isn't it.
CREATE TABLE b (y Int8) ENGINE = Memory;
"""
    stmts = _statements(sql)
    assert len(stmts) == 2, f"apostrophe desynced the split: {stmts}"
    for stmt in stmts:
        assert stmt.upper().startswith("CREATE"), f"statement starts mid-prose: {stmt[:80]!r}"


def test_every_schema_object_survives_the_splitter():
    """The splitter must not drop real DDL while filtering comments."""
    from qc.store import _statements

    sql = (Path(__file__).parent.parent / "qc" / "schema.sql").read_text()
    joined = "\n".join(_statements(sql))
    for obj in ("deliverable.findings", "deliverable.loudness_samples",
                "deliverable.catalog_status", "deliverable.worst_windows",
                "deliverable.jobs"):
        assert obj in joined, f"{obj} was dropped by the statement splitter"


def test_schema_applies_cleanly_against_a_real_clickhouse(ch):
    """End to end: the real schema must apply without a syntax error.

    This is the check that would have caught the outage directly, rather than
    inferring it from the splitter's output.
    """
    from qc.store import _statements

    db = f"deliverable_apply_{uuid.uuid4().hex[:8]}"
    sql = (Path(__file__).parent.parent / "qc" / "schema.sql").read_text()
    sql = sql.replace("deliverable.", f"{db}.").replace(
        "CREATE DATABASE IF NOT EXISTS deliverable", f"CREATE DATABASE IF NOT EXISTS {db}"
    )
    try:
        for stmt in _statements(sql):
            ch.command(stmt)
    finally:
        ch.command(f"DROP DATABASE IF EXISTS {db}")
