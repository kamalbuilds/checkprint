# Checkprint

**Measures the full delivery spec. Repairs loudness to target. Proves the delta by
re-measuring.**

Agentic Cinema hackathon · ClickHouse track · Gemini on Google Cloud. Partner wiring: `ARCHITECTURE.md`.

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

Each command below reproduces the number on the same line. The `-t` flag is part of
it: every figure here is a measurement of a bounded window, and without `-t` ffmpeg
measures the whole feature and returns something else. Do not add `-loglevel error`
either, because it suppresses the `ebur128` summary and you get empty output.

```bash
# -26.1 LUFS. Verified independently on a second machine, 2026-09-09.
ffmpeg -hide_banner -nostats -t 300 \
  -i "https://archive.org/download/vicki-1953/Vicki%20%281953%29.mp4" \
  -af ebur128 -f null -

# -18.0 LUFS
ffmpeg -hide_banner -nostats -t 180 \
  -i "https://archive.org/download/werewolf_in_a_girls_dormitory_ipod/Werewolf_In_A_Girls_Dormitory.ogv" \
  -af ebur128 -f null -
```

| Title | Window | Measured | EBU R128 target | Verdict |
|---|---|---|---|---|
| *Vicki* (1953) | first 300 s | **−26.1 LUFS** | −23 LUFS ±1 | FAIL, 3.1 LU under |
| *What Becomes Of The Children?* (512kb encode) | first 180 s | **−24.3 LUFS** | −23 LUFS ±1 | FAIL, 1.3 LU under |
| *Werewolf in a Girls' Dormitory* | first 180 s | **−18.0 LUFS** | −23 LUFS ±1 | FAIL, 5.0 LU over |
| in-spec control (synthesised) | full | **−23.0 LUFS** | −23 LUFS ±1 | **PASS** |

The app does not ask you to trust that table either: `/api/title/{id}` returns the
exact command for whichever title is on screen, built from the URL and window that
particular measurement was actually taken from, which is recorded in
`deliverable.sources` at ingest. If a command cannot be made runnable for a title,
none is shown, because a reproduce command that 404s is worse than no offer.

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

A twelve-node graph, built as a `google.adk.workflow.Workflow`. That is the current
orchestration primitive in ADK 2.8: `SequentialAgent`, `ParallelAgent` and
`LoopAgent` still run but all three carry
`@deprecated("... in favor of Workflow ...")` in the installed wheel, and a test in
this repo fails if any of them reappears.

Three of the twelve nodes hold a model. Nine are ffmpeg and SQL.

| Node | What runs | Who decides |
|---|---|---|
| `ingest` | fetch title + subtitle track from archive.org | deterministic |
| `scan_audio` | ffmpeg `ebur128`, ten readings a second | deterministic |
| `scan_picture` | black, frozen and subtitle battery | deterministic |
| `measured` | `JoinNode`: waits for both scans | deterministic |
| `stage_before` | write verdicts and the 100 ms series to ClickHouse | deterministic |
| `window_scout` | writes its own SQL over the 100 ms series to locate the failing passages | **Gemini + mcp-clickhouse** |
| `confirm_windows` | re-derive every window from the table, drop what it does not support | deterministic |
| `repair_planner` | choose the repairs and their order, from a fixed whitelist | **Gemini** |
| `remediate` | ffmpeg: attenuate the located passages, then normalise | deterministic |
| `verify` | re-measure; fails loudly if the repair did not land | deterministic |
| `regression_auditor` | query the two stages against each other for collateral damage | **Gemini + mcp-clickhouse** |
| `report` | assemble the operator note | deterministic |

`scan_audio` and `scan_picture` are two independent ffmpeg processes, so they are a
fan-out into a `JoinNode` rather than a loop.

**Gemini never produces a number that reaches a verdict.** It decides *where* to
look and *what* to repair; ffmpeg produces every figure. That is exactly why a judge
can reproduce every claim.

### Why three agents and not one, and not five

Each of the three changes something a person can check, and there is a test that
goes red when it stops doing so.

- **`window_scout`** writes SQL over `loudness_samples` and returns the passages of
  this master that fail. Those passages are the only seconds of audio the
  remediator may touch. Delete it and the repair becomes one gain over the whole
  programme, which is a different output file, not a different diagram.
- **`repair_planner`** holds no tools on purpose, so it cannot query and therefore
  cannot invent a passage. Delete it and nothing is planned, `remediate` no-ops, and
  the run raises rather than reporting an unchanged file as a completed repair.
- **`regression_auditor`** runs after the re-measurement and compares the two stages
  on the same sample grid. It exists because `verify` only counts failures, so a
  repair that fixed integrated loudness by flattening the mix passes it. Delete it
  and the ship or do-not-ship line loses its only evidence of collateral damage.

Measurement, remediation and re-measurement are deliberately **not** agents. A model
that can decide to skip the re-measure is a model that can report a repair that never
happened.

Two things the agents are not allowed to do. Every MCP tool call passes an ADK
`before_tool_callback` that refuses anything which is not a `SELECT` or `WITH` over
one of five named tables, so a language model never holds a write connection to the
QC record. And the run refuses to start unless the official `mcp-clickhouse` server
answers a real `list_tools`, because a scout without its tools does not stop having
opinions: the first time this graph ran, the MCP subprocess had died at startup and
the scout returned five passages with plausible timecodes, one of them at 1782
seconds into a sixty-second scan.

### The passage, not the programme

This is the part worth reading if you only read one.

A master that sits under target has to be lifted to reach it. A global gain moves
every sample by the same amount, so it cannot answer a question about one passage:
if the loudest passage has less headroom than the lift, that passage ends up over the
true-peak ceiling and the normaliser has to start limiting.

So the scout's SQL looks for exactly those passages, judged not against the −1.0 dBTP
ceiling but against `ceiling − required lift`, which is computed in `scan_audio` and
handed down. The remediator attenuates only inside them, and then the programme is
normalised as usual.

What that buys, stated as precisely as it can be verified: **the located passage, and
only the located passage, is attenuated, by exactly the planned amount.** Measured on
the 100 ms series of a synthesised master, before the normalise, the passage moves by
the planned −3.0 dB and the rest of the programme moves by 0.0 dB within 0.1 LU. That
is the assertion in `test_removing_the_scout_changes_the_repaired_audio`, and the
finished repair is a byte-different file from the same repair run with no passages
handed to it.

What is deliberately **not** claimed: that this makes `loudnorm` normalise linearly
and preserve the loudness range. That would be the nicer headline and it is not
reliably true. `loudnorm` is asked for `LRA=11`, so on any programme with a wider
range it runs in dynamic mode and redistributes level across the whole file, which
partly offsets the windowed attenuation in the final render. Measured on the same
fixture: 0.1 LU of difference at the passage against 0.2 LU elsewhere after the
normalise. The mechanism is real and exact before that stage; the end-to-end
loudness-range story is not, so it is not sold as one.

Three rules bound it, all in `qc/measure.py`:

- It **only ever attenuates.** It never raises a quiet passage. A 100 ms loudness
  series cannot tell a whispered line after an explosion from a mastering mistake,
  so a passage below target is reported and left alone.
- Corrections are capped at **6 dB**. Past that the window is escalated to a person,
  because that is a mastering decision and not a delivery fix.
- Passages shorter than **400 ms**, one momentary integration window, are not
  touched. A single tick is not a passage.

And the model does not do the arithmetic. It decides *where*; the dB are computed in
Python. That split was not theoretical either: on the first run the scout searched
against the raw ceiling, returned a passage peaking at −14.6 dBTP, and the remediator
refused it as already compliant. It was right to refuse.

Here is the query Gemini wrote for *Vicki* (1953), unedited, sent through
`mcp-clickhouse` and recorded in the `sql` column of the `deliverable.fail_windows`
table. Nobody wrote this SQL by hand, and it is not a primary-key lookup: it is a
gap-and-island grouping that finds contiguous runs of offending 100 ms samples and
discards the short ones.

```sql
SELECT MIN(t_seconds) AS start_s, MAX(t_seconds) + 0.1 AS end_s,
       MAX(true_peak) AS worst_true_peak, COUNT(*) AS row_count
FROM (
  SELECT t_seconds, true_peak,
         t_seconds - (ROW_NUMBER() OVER (ORDER BY t_seconds) * 0.1) AS time_group
  FROM deliverable.loudness_samples
  WHERE run_id = '3f510a4f-90a1-4a68-bed0-9bad26d0cd8f'
    AND title_id = 'vicki-1953' AND stage = 'before'
    AND true_peak > -2.9
) AS flagged_samples
GROUP BY time_group
HAVING row_count >= 4
ORDER BY worst_true_peak DESC
```

The `-2.9` is the threshold `scan_audio` computed and handed down: *Vicki* measured
−24.9 LUFS over that window, so it needs +1.9 dB, and −1.0 − 1.9 = −2.9.

### Where this path does not fire, and why

It returned nothing for *Vicki*, and that is the correct answer. Measured over the
first 90 s of each title with `ebur128=peak=true`:

| Title | Integrated | True peak | Lift needed | Passage threshold | Windows |
|---|---|---|---|---|---|
| *Vicki* (1953) | −24.9 LUFS | −14.5 dBTP | +1.9 dB | −2.9 dBTP | none |
| *Citizen Kane* | −18.3 LUFS | −0.2 dBTP | −4.7 dB | +3.7 dBTP | none |
| *Cherche* | −14.9 LUFS | +2.3 dBTP | −8.1 dB | +7.1 dBTP | none |
| *Go Down Death* | −31.5 LUFS | −10.6 dBTP | +8.5 dB | −9.5 dBTP | none |
| *Karate Kids USA* | −28.9 LUFS | −11.9 dBTP | +5.9 dB | −6.9 dBTP | none |
| synthesised master with a 1 s transient | −31.3 LUFS | −6.3 dBTP | +8.3 dB | −9.3 dBTP | **1, treated −3.0 dB** |

The pattern is a property of the corpus, not a bug. A 1950s optical transfer is
either quiet with 12 dB of headroom, so the lift fits, or already clipped and too
loud, so the global gain is an attenuation and it lowers the peaks along with
everything else. Both cases are handled by one constant gain, and the scout correctly
declines to touch anything.

The passage-level conflict needs a programme that is *under* target and *peaky*, which
is a high-crest-factor modern mix rather than an optical soundtrack. That case is
exercised by `test_removing_the_scout_changes_the_repaired_audio`, which builds such a
master and runs the repair twice, once with the passage and once without it.

Saying so plainly is the point. The honest claim is that the mechanism is real,
exactly tested, and does nothing on files that do not need it, which is what a QC tool
should do. A tool that found something to fix on every one of those five titles would
be a tool whose findings mean nothing.

## Why ClickHouse

Not "a database". QC telemetry is a real time series: `ebur128` emits a reading every
100 ms, so one 90-minute feature is **~54,000 rows** and a 500-title catalog is **~27M**.
A single 300-second scan in this repo produces **3,001 rows**.

The catalog questions are scans over that: which titles fail, where the worst sustained
passage sits, whether this master regressed against the previous one. Delete ClickHouse and
the catalog view dies.

And the questions are not lookups. The window scout writes its own grouped scans over
the 100 ms series to find contiguous offending passages; the regression auditor
self-joins the before and after stages on the same `t_seconds` grid to check that
nothing outside the treated passages moved. Those statements are recorded verbatim in
the `sql` column of the `deliverable.fail_windows` table and returned by the API, so
"the model chose this query" is inspectable rather than asserted.

Schema: `qc/schema.sql`. Tables `findings`, `loudness_samples`, `fail_windows`,
`sources`, `jobs`; views `catalog_status` and `worst_windows`.

`fail_windows` is the table that stops the 100 ms series being an audit log. A
finding says "integrated loudness is 4.3 LU under target", which one global gain
answers. A window says "between 41.2 s and 68.9 s this master has no headroom left",
which a global gain cannot answer, because it moves that passage and every other
passage by the same amount.

## Specs encoded

- **EBU R128**: integrated loudness −23 LUFS, ±1.0 LU
- **ATSC A/85 (CALM Act)**: −24 LKFS, ±2.0 LU
- **True peak**: ≤ −1.0 dBTP
- **Netflix TTSS**: ≤ 17 chars/sec, ≥ 5/6 s per cue, ≤ 42 chars/line, ≤ 2 lines
- **Structural**: no black segment ≥ 2 s, no frozen video

## Run it

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt

# ClickHouse (Cloud or self-hosted; both satisfy the track)
export CLICKHOUSE_HOST=localhost          # or <id>.clickhouse.cloud
export CLICKHOUSE_SECURE=false            # true for Cloud
export CLICKHOUSE_PASSWORD=...
clickhouse client --multiquery < qc/schema.sql

# The official mcp-clickhouse server, in its OWN virtualenv. This is not fussiness:
# google-adk pins mcp>=1.24,<2 and mcp-clickhouse needs fastmcp>=4 which wants
# mcp>=2, so one shared environment leaves an executable on disk that dies at
# import, and the MCP client reports "Connection closed" as if the network were at
# fault. MCP is a subprocess protocol, so the server only has to be runnable.
python3 -m venv /opt/mcp-clickhouse-venv
/opt/mcp-clickhouse-venv/bin/pip install "mcp-clickhouse>=0.6"
export MCP_CLICKHOUSE_BIN=/opt/mcp-clickhouse-venv/bin/mcp-clickhouse

# Gemini: Vertex AI
export GOOGLE_GENAI_USE_VERTEXAI=true
export GOOGLE_CLOUD_PROJECT=your-project
# ...or the Gemini API
export GOOGLE_API_KEY=...

.venv/bin/python web/server.py   # http://localhost:8080
```

**Gemini credentials and a reachable `mcp-clickhouse` are both required.** Without
credentials `run_pipeline` raises `GeminiRequired`; without a server that answers
`list_tools` it raises `McpRequired`. Neither is a degraded mode and there is
deliberately no offline fallback: a fallback that produced the same plan shape would
make the model decorative, since you could delete Gemini and the product would behave
identically. Deciding *which* passages are worth touching, in what order, and which
need a human is the model's job. Executing the
repair stays deterministic in ffmpeg, and the model is never allowed to produce a
number that reaches a verdict.

## Tests

```bash
.venv/bin/python -m pytest tests/ -q     # 107 passed
```

Every check is tested in **both directions**: it must go red on bad input and green on good
input. The suite includes a mutation test that widens a threshold to absurdity and asserts
the verdict flips, so a gate that cannot fail is caught here rather than in front of a user.

Sixteen of those tests guard a specific guarantee, and each one was confirmed able to
fail by breaking the guarantee in the production code, watching the test go red, and
restoring it:

| Guarantee broken | Test that went red |
|---|---|
| located passages stop reaching ffmpeg | `test_removing_the_scout_changes_the_repaired_audio` |
| the 6 dB attenuation cap is removed | `test_the_scout_cannot_ask_for_a_remaster` |
| quiet passages become fair game | `test_the_scout_only_ever_pulls_down` |
| the silent no-op stops being loud | `test_removing_the_planner_makes_the_run_refuse` |
| the model may widen what is auto-fixable | `test_model_cannot_mark_manual_defect_as_auto_fixable` |
| the auditor's finding is dropped from the note | `test_removing_the_auditor_strips_the_damage_evidence` |
| writes reach ClickHouse | `test_guardrail_refuses_writes` |
| the SELECT-only prefix rule is disabled | `test_guardrail_refuses_statements_that_are_not_reads_of_a_known_table` |
| an unremediated title reports 0 failures after | `test_a_title_nobody_remediated_reports_no_after_count` |
| the reproduce command loses its window flag | `test_reproduce_command_carries_the_window_and_keeps_the_summary` |
| a reproduce URL with no filename is published | `test_no_command_is_offered_when_it_cannot_be_made_runnable` |
| the run starts without proving MCP answers | `test_pipeline_refuses_without_mcp` |
| the run starts without model credentials | `test_pipeline_refuses_without_model` |
| a broken `mcp-clickhouse` copy is accepted | `test_broken_server_copy_is_not_preferred` |
| CTE aliases are checked against the physical table allowlist | `test_guardrail_allows_a_cte_over_an_allowlisted_table` |
| the table fence is dropped, so a CTE becomes a bypass | `test_a_cte_body_cannot_smuggle_in_a_forbidden_table` |

Two of those rows exist because of gaps this exercise found. The write-keyword fence
was answering for every statement in the write test, so disabling the `SELECT`-only
rule left the suite green: a check that cannot fail is worse than no check, and that
rule now has cases only it can catch. And the table allowlist was matching CTE aliases
against physical table names, so `WITH x AS (...) SELECT * FROM x` was refused, which
would have blocked exactly the gap-and-island queries the scout is asked to write.
Refusals are now also reported in the run trace, because a blocked query and a master
with nothing wrong with it were both rendering as "0 passages located".

**Where the suite is blind.** Four tests skip without a reachable ClickHouse, and the
loudness fixtures are synthesised rather than downloaded, so they prove the code
handles the shape rather than that a particular real master behaves as expected. The
end-to-end graph run is exercised by hand against a live ClickHouse and Vertex, not in
CI, because it costs model calls.

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
