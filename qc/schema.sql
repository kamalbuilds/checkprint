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
--
-- The verdict distinguishes "we improved it but it is not shippable" from "we did
-- nothing", because those are different facts for an operator and the earlier
-- version reported them identically. A subtitle pass that takes cues below minimum
-- duration from 44 to 12, and reading-speed failures from 20.3% to 12.2%, is real
-- work; calling that "still failing" alongside a title nobody touched is both
-- unhelpful and makes the tool look worse than it is. It is still not "delivery
-- ready", and we do not claim it is.
--
-- failures_* count CHECKS, which is the wrong unit for scanning a catalog: a
-- title with 42 cues over the reading-speed limit and a title with 1 cue over
-- both report a single failed check and look identical. The cue counts below are
-- the size of the problem, parsed out of the finding text the check sheet
-- already writes ("42 of 516 cues exceed the reading-speed limit"). A title with
-- no subtitle findings sums to 0, which is the truth, not a blank.
--
-- For min-duration, `measured` is already a cue count, so the greater of the two
-- is taken and the row survives either representation. For reading speed
-- `measured` is a PERCENTAGE, so only the detail text may be parsed there;
-- taking a max would report 20.3 cues for a 20.3% failure rate.
CREATE OR REPLACE VIEW deliverable.catalog_status AS
SELECT
    title_id,
    any(title)                                        AS title,
    max(run_at)                                       AS last_run,
    countIf(passed = 0 AND stage = 'before')          AS failures_before,
    countIf(passed = 0 AND stage = 'after')           AS failures_after,
    sumIf(toUInt32OrZero(extract(detail, '^(\\d+)')),
          check = 'subtitle_reading_speed' AND stage = 'before')  AS cps_cues_before,
    sumIf(toUInt32OrZero(extract(detail, '^(\\d+)')),
          check = 'subtitle_reading_speed' AND stage = 'after')   AS cps_cues_after,
    sumIf(greatest(toUInt32OrZero(extract(detail, '^(\\d+)')),
                   toUInt32(ifNull(measured, 0))),
          check = 'subtitle_min_duration' AND stage = 'before')   AS short_cues_before,
    sumIf(greatest(toUInt32OrZero(extract(detail, '^(\\d+)')),
                   toUInt32(ifNull(measured, 0))),
          check = 'subtitle_min_duration' AND stage = 'after')    AS short_cues_after,
    multiIf(
        countIf(stage = 'after') = 0,                             'not remediated',
        countIf(passed = 0 AND stage = 'after') = 0,              'delivery ready',
        countIf(passed = 0 AND stage = 'after')
            < countIf(passed = 0 AND stage = 'before'),           'improved, needs human',
        'still failing'
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
--
-- min()/max() over 100ms samples answer the wrong question: a single 100ms spike
-- is not a delivery problem, and reporting it as "the loudest moment" sends an
-- operator to a frame where nothing is audibly wrong. The percentiles are what a
-- QC operator actually argues about, so they are computed with quantileTDigest,
-- which is the estimator built for exactly this shape of data: tens of thousands
-- of samples per title, streamed, where an exact quantile would mean holding the
-- whole distribution in memory per group.
--
-- p95 is the sustained-loud end and p05 the sustained-quiet end; the raw min/max
-- stay alongside them so the spike is still visible and nothing is hidden.
CREATE OR REPLACE VIEW deliverable.worst_windows AS
SELECT
    title_id,
    stage,
    count()                                              AS samples,
    round(toFloat64(min(short_term)), 1)                     AS quietest_short_term_lufs,
    round(toFloat64(max(short_term)), 1)                     AS loudest_short_term_lufs,
    -- toFloat64 before round() is load-bearing, not style: quantileTDigest returns
    -- Float32, and round(Float32, 1) leaks the binary representation, so p05 came
    -- back as -40.29999923706055 instead of -40.3 and rendered as noise in the UI.
    round(toFloat64(quantileTDigest(0.05)(short_term)), 1)   AS p05_short_term_lufs,
    round(toFloat64(quantileTDigest(0.50)(short_term)), 1)   AS median_short_term_lufs,
    round(toFloat64(quantileTDigest(0.95)(short_term)), 1)   AS p95_short_term_lufs,
    -- How wide the sustained range is: a large spread is a mastering problem a
    -- single normalisation pass will not fix, and is worth a human's attention.
    round(toFloat64(quantileTDigest(0.95)(short_term))
        - toFloat64(quantileTDigest(0.05)(short_term)), 1)   AS sustained_range_lu,
    argMin(t_seconds, short_term)                        AS quietest_at_seconds,
    argMax(t_seconds, short_term)                        AS loudest_at_seconds
FROM deliverable.loudness_samples
WHERE short_term > -70
GROUP BY title_id, stage;

-- Where each measurement came from. One row per ingest.
--
-- This exists so the reproduce command on screen is the URL that actually produced
-- the number, rather than a URL rebuilt later from an identifier. Rebuilding it is
-- how you publish `archive.org/download/Cherchever1/` with no filename on it,
-- which 404s, and a broken reproduce command on a product whose whole pitch is
-- "check my numbers yourself" is worse than no command at all.
--
-- `window_seconds` is stored next to the URL for the same reason: the published
-- number is a measurement of the first N seconds, and a command without `-t N`
-- measures the whole feature and returns something else.
CREATE TABLE IF NOT EXISTS deliverable.sources
(
    title_id       String,
    title          String,
    ingested_at    DateTime DEFAULT now(),
    video_file     String,
    source_url     String,
    window_seconds UInt32,
    bytes_fetched  UInt64
)
ENGINE = ReplacingMergeTree(ingested_at)
ORDER BY title_id;

-- Fail windows: the passages of a master that a window-scout agent located by
-- writing its own SQL over loudness_samples, and the only regions the windowed
-- remediation pass is allowed to touch.
--
-- This table is the reason the 100ms series is not an audit log. A finding says
-- "integrated loudness is 4.3 LU under target", which a single global gain can
-- answer. A window says "between 41.2s and 68.9s the short-term loudness sits at
-- -31.4 LUFS", which a global gain cannot answer, because a global gain moves that
-- passage and every other passage by the same amount and leaves the spread between
-- them identical. Locating those passages is a window query over tens of thousands
-- of rows per title, which is what a column store is for.
--
-- `sql` is the exact statement the agent sent to mcp-clickhouse. It is stored so
-- the claim "the model chose this query" is inspectable rather than asserted, and
-- so a reviewer can re-run it.
CREATE TABLE IF NOT EXISTS deliverable.fail_windows
(
    run_id      UUID,
    title_id    String,
    title       String,
    found_at    DateTime DEFAULT now(),
    ord         UInt16,
    start_s     Float32,
    end_s       Float32,
    metric      LowCardinality(String),   -- 'short_term_low' | 'short_term_high' | 'true_peak'
    measured    Float64,
    target      Float64,
    unit        LowCardinality(String),
    gain_db     Float32,                  -- what the remediator applied, 0 if refused
    treated     UInt8,                    -- 0 when escalated to a human instead
    reason      String,
    sql         String
)
ENGINE = MergeTree
ORDER BY (title_id, run_id, start_s);

-- Pipeline run state. Lives in ClickHouse rather than process memory because
-- Cloud Run serves requests from multiple instances: a job started on instance A
-- was invisible to the poll that landed on instance B, which returned 404.
CREATE TABLE IF NOT EXISTS deliverable.jobs
(
    job         String,
    updated_at  DateTime DEFAULT now(),
    state       LowCardinality(String),
    identifier  String,
    payload     String
)
ENGINE = ReplacingMergeTree(updated_at)
ORDER BY job;
