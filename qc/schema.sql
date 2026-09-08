-- DELIVERABLE schema.
--
-- The point of ClickHouse here is not "a database". It is that QC telemetry is a
-- high-volume time series: ffmpeg's ebur128 emits a reading every 100ms, so one
-- 90-minute feature is ~54,000 rows and a 500-title catalog is ~27M rows. The
-- catalog questions ("which titles fail", "where is the worst 60s window",
-- "did this master regress against its previous version") are scans over that.

CREATE DATABASE IF NOT EXISTS deliverable;

-- One row per spec check per QC run. Small table, drives the verdicts.
CREATE TABLE IF NOT EXISTS deliverable.findings
(
    run_id        UUID,
    title_id      String,
    title         String,
    run_at        DateTime DEFAULT now(),
    stage         LowCardinality(String),   -- 'before' | 'after'
    check         LowCardinality(String),
    spec          LowCardinality(String),
    measured      Nullable(Float64),
    target        Nullable(Float64),
    unit          LowCardinality(String),
    passed        UInt8,
    auto_fixable  UInt8,
    detail        String
)
ENGINE = MergeTree
ORDER BY (title_id, check, stage, run_at);

-- The high-volume table: one row per 100ms loudness sample.
CREATE TABLE IF NOT EXISTS deliverable.loudness_samples
(
    run_id     UUID,
    title_id   String,
    stage      LowCardinality(String),
    t_seconds  Float32,
    momentary  Float32,     -- M, 400ms window
    short_term Float32,     -- S, 3s window
    integrated Float32,     -- I, cumulative
    true_peak  Float32
)
ENGINE = MergeTree
ORDER BY (title_id, stage, t_seconds);

-- Catalog verdict per title, from the LATEST run only.
-- Aggregating across every historical run double-counts: re-running a title made
-- failures_before jump from 4 to 8 with no change to the film.
CREATE OR REPLACE VIEW deliverable.catalog_status AS
SELECT
    title_id,
    any(title)                                        AS title,
    max(run_at)                                       AS last_run,
    countIf(passed = 0 AND stage = 'before')          AS failures_before,
    countIf(passed = 0 AND stage = 'after')           AS failures_after,
    if(countIf(stage = 'after') = 0,
       'not remediated',
       if(countIf(passed = 0 AND stage = 'after') = 0, 'delivery ready', 'still failing')
    )                                                 AS verdict
FROM deliverable.findings
WHERE (title_id, run_id) IN (
    SELECT title_id, argMax(run_id, run_at)
    FROM deliverable.findings
    GROUP BY title_id
)
GROUP BY title_id;

-- The worst sustained loudness window per title, which is what an operator
-- actually needs in order to go and listen to the problem.
CREATE VIEW IF NOT EXISTS deliverable.worst_windows AS
SELECT
    title_id,
    stage,
    round(min(short_term), 1) AS quietest_short_term_lufs,
    round(max(short_term), 1) AS loudest_short_term_lufs,
    argMin(t_seconds, short_term) AS quietest_at_seconds
FROM deliverable.loudness_samples
WHERE short_term > -70
GROUP BY title_id, stage;
