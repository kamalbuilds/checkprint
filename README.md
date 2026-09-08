# DELIVERABLE

**Measures the full delivery spec. Repairs loudness to target. Proves the delta by
re-measuring.**

Agentic Cinema hackathon · ClickHouse track · Gemini on Google Cloud

---

## The incident

A distributor uploads a finished film to a streamer. Eleven days later it bounces back:
integrated loudness is out of spec, a run of subtitle cues breaks the reading-speed limit,
there is a two-second black hole at the reel change. Nobody watched the film wrong. The
numbers were simply never measured before delivery, and the redeliver cycle costs weeks.

Delivery QC is one of the few places in film where "correct" is a **number with a legal
threshold**, not an opinion, and it is still routinely done by ear.

## Who this is for

A **post-production supervisor or delivery QC operator** at an indie distributor, post
house, or film archive. They ship masters to platforms that reject on spec. Today they
either pay for Telestream Vantage / Venera Pulsar / Baton, or they eyeball it.

The thresholds this tool enforces are not invented for a demo. They are published
standards, quoted here from the primary sources so any of them can be checked:

- **EBU R128** ([tech.ebu.ch/docs/r/r128.pdf](https://tech.ebu.ch/docs/r/r128.pdf)),
  clause h: *"the Programme Loudness Level shall be normalised to a Target Level of
  −23.0 LUFS ... a tolerance of ±1.0 LU is permitted."* Those two numbers are
  `EBU_R128_TARGET_LUFS` and `EBU_R128_TOLERANCE_LU` in `qc/measure.py`.
- **ATSC A/85 / the CALM Act** (47 U.S.C. §621), the US rule that makes commercial
  loudness a legal matter rather than a preference: −24 LKFS, ±2 LU.
- **Netflix Timed Text Style Guide**: 17 characters/second adult reading speed, 5/6 s
  minimum cue duration, 42 characters per line, 2 lines maximum.

Those tools **detect and report**. This one **repairs what is deterministically
repairable, then re-measures to prove the repair landed** and escalates the rest with the
specific reason. The honest scope, stated up front rather than buried:

| Defect | What this tool does |
|---|---|
| Integrated loudness (EBU R128 / ATSC A/85) | **Measures and repairs**, verified by re-measurement |
| True peak | **Measures and repairs** |
| Subtitle reading speed / min duration | Measures, and **partially** repairs by retiming into free space |
| Subtitle line length | **Measures only.** Rewriting text is a human judgement |
| Black frames / frozen frames | **Measures only.** A reel change and damage look identical to a machine |

**Nobody has used this in production yet.** It was built during the hackathon and
validated against public files anyone can download, plus a synthesised modern master
(see below). That is the honest state, and every number here is reproducible rather
than reported.

## Verify every claim yourself

This is the point of the project. Every number below came out of ffmpeg, and you can
reproduce any of them without running our code:

```bash
curl -L -o vicki.mp4 "https://archive.org/download/vicki-1953/Vicki%20%281953%29.mp4"
ffmpeg -i vicki.mp4 -af ebur128 -f null -
```

| Title | Measured | EBU R128 target | Verdict |
|---|---|---|---|
| *Vicki* (1953), first 300 s | **−26.1 LUFS** | −23 LUFS ±1 | FAIL, 3.1 LU under |
| *What Becomes Of The Children?* (512kb encode, 180 s) | **−24.3 LUFS** | −23 LUFS ±1 | FAIL, 1.3 LU under |
| *Werewolf in a Girls' Dormitory*, first 180 s | **−18.0 LUFS** | −23 LUFS ±1 | FAIL, 5.0 LU over |
| in-spec control (synthesised) | **−23.0 LUFS** | −23 LUFS ±1 | **PASS** |

That last row matters as much as the others. A gate that flags everything is decoration.

Loudness is a property of a specific encode over a specific window, so both are always
stated. The same title's higher-bitrate encode measures −26.9 LUFS over its first 240 s.
Two encodes of one film genuinely differ; the tool reports what it measured, not what the
title "is".

### It is not only old films

The demo titles are public-domain features because those are the films anyone can
download and re-measure. The obvious objection is that a 1950s optical soundtrack is
nothing like a modern delivery master, so `tests/test_modern_master.py` builds one and
runs the same loop against it:

| Property | Value |
|---|---|
| Resolution | 1920×1080 |
| Audio | **5.1 surround, 48 kHz**, 448 kbps |
| Measured | **−27.3 LUFS** → FAIL |
| After repair | **−22.2 LUFS** → PASS |
| Channel layout after repair | still 6 channels |

That last row is a test in its own right: a repair that silently downmixed 5.1 to stereo
would ruin a master while appearing to fix the loudness. It was verified by forcing a
downmix and confirming the test goes red.

An in-spec modern master measures −23.0 and PASSES, so the gate is not simply flagging
everything modern either.

Subtitle measurements on real archive.org tracks:

| Check | Spec | *Werewolf* before | after repair |
|---|---|---|---|
| Reading speed | Netflix TTSS ≤ 17 cps | 22.4% of cues fail | **16.2%** |
| Minimum duration | ≥ 5/6 s per cue | 29 cues | **13 cues** |
| Line length | ≤ 42 chars/line | 38 cues | 38 (needs a human) |

## What the agent does

Six deterministic steps. The model sits at the edge, never in the measurement path.

| Step | What runs | Who decides |
|---|---|---|
| `ingest` | fetch title + subtitle track from archive.org | deterministic |
| `measure` | ffmpeg QC battery → ClickHouse | deterministic |
| `classify` | Gemini reads measurements + spec, plans repairs | **Gemini** |
| `remediate` | two-pass `loudnorm`, cue retiming | deterministic |
| `verify` | re-measure; fails loudly if the repair did not land | deterministic |
| `report` | Gemini writes the operator's delivery note | **Gemini** |

**Gemini never produces a number that reaches a verdict.** It interprets numbers ffmpeg
produced. That is exactly why a judge can reproduce every claim.

## Why ClickHouse

Not "a database". QC telemetry is a real time series: `ebur128` emits a reading every
100 ms, so one 90-minute feature is **~54,000 rows** and a 500-title catalog is **~27M**.
A single 300-second scan in this repo produces **3,001 rows**.

The catalog questions are scans over that: which titles fail, where the worst sustained
passage sits, whether this master regressed against the previous one. Delete ClickHouse and
the catalog view dies.

Schema: `qc/schema.sql` (`findings`, `loudness_samples`, plus `catalog_status` and
`worst_windows` views).

## Specs encoded

- **EBU R128** — integrated loudness −23 LUFS, ±1.0 LU
- **ATSC A/85 (CALM Act)** — −24 LKFS, ±2.0 LU
- **True peak** — ≤ −1.0 dBTP
- **Netflix TTSS** — ≤ 17 chars/sec, ≥ 5/6 s per cue, ≤ 42 chars/line, ≤ 2 lines
- **Structural** — no black segment ≥ 2 s, no frozen video

## Run it

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt

# ClickHouse (Cloud or self-hosted; both satisfy the track)
export CLICKHOUSE_HOST=localhost          # or <id>.clickhouse.cloud
export CLICKHOUSE_SECURE=false            # true for Cloud
export CLICKHOUSE_PASSWORD=...
clickhouse client --multiquery < qc/schema.sql

# Gemini: Vertex AI
export GOOGLE_GENAI_USE_VERTEXAI=true
export GOOGLE_CLOUD_PROJECT=your-project
# ...or the Gemini API
export GOOGLE_API_KEY=...

.venv/bin/python web/server.py   # http://localhost:8080
```

**Gemini credentials are required.** Without them `classify` raises `GeminiRequired`
and the pipeline stops. There is deliberately no offline fallback: a fallback that
produced the same plan shape would make the model decorative, since you could delete
Gemini and the product would behave identically. Deciding *which* defects are worth
repairing, in what order, and which need a human is the model's job. Executing the
repair stays deterministic in ffmpeg, and the model is never allowed to produce a
number that reaches a verdict.

## Tests

```bash
.venv/bin/python -m pytest tests/ -q     # 48 passed
```

Every check is tested in **both directions**: it must go red on bad input and green on good
input. The suite includes a mutation test that widens a threshold to absurdity and asserts
the verdict flips, so a gate that cannot fail is caught here rather than in front of a user.

## Honest limitations

- **Bounded scans.** The demo measures the first N seconds of each title (default 180-300 s)
  so a run finishes in a demo. Full-feature scans work but take minutes. The window is
  always shown in the UI; it is never implied to be a full scan.
- **Subtitle repair is partial by construction.** Cues are only extended into genuinely free
  space, so a densely packed track cannot be fully fixed by retiming. On *Werewolf*, reading
  speed went 22.4% → 16.2%, not to zero. Over-long lines need a human to rewrite the text,
  and the agent says so instead of pretending.
- **Black and frozen frames are never auto-repaired.** A two-second black segment may be a
  reel change, a fade, or damage. That is a human call.
- **ASR subtitles are noisy.** archive.org tracks are machine-transcribed, so some cues are
  near-zero duration. Those are reported as minimum-duration failures rather than absurd
  reading-speed numbers.
- **No production user yet.** This was built during a hackathon. It has not been run
  inside a post house on a paying delivery, and nobody is quoted here saying it saved
  them a redeliver, because that has not happened yet. What it has is reproducible
  numbers on files anyone can fetch.
- **Loudness remediation is two-pass.** Single-pass `loudnorm` runs in dynamic mode and
  measurably moved a −24.3 LUFS file to −25.3, i.e. further from spec. The `verify` step
  caught it. Fixed, and there is a regression test that fails when the fix is disabled.

## License

MIT. See `LICENSE`.
