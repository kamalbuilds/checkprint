# Devpost submission: Checkprint

Track: ClickHouse. Agentic Cinema, Google Cloud + Devpost.

---

## Project name

**Checkprint**

## Elevator pitch

Delivery QC that measures a master against EBU R128, writes its own ClickHouse SQL to find the failing passage, repairs it with ffmpeg, then re-measures to prove the fix landed.

*(177 characters, under the 200 limit.)*

---

## Inspiration

A distributor uploads a finished film to a streamer. Eleven days later it bounces: integrated loudness sits outside spec, a run of subtitle cues breaks the reading speed limit, there's a two second black hole at the reel change. Nobody watched the film wrong. The numbers were never measured before the file went out, and the redeliver cycle burns weeks.

Delivery QC is one of the few places in film where "correct" is a number with a published threshold rather than an opinion, and it's still routinely done by ear.

The thing that turned that annoyance into a project was checking whether the defects are actually out there. *Vicki* (1953) is on archive.org, free to anyone. One ffmpeg command reads it at **-26.1 LUFS** against the EBU R128 target of -23. That is a real rejection on a real file, and a stranger can confirm it in about a minute without touching our code. Everything else in Checkprint is built to hold that standard: if a number appears on screen, you can go get it yourself.

## What it does

Checkprint takes a master, measures it against the delivery spec, repairs what can be repaired deterministically, and re-measures to prove the repair landed. Six steps run per job: ingest, measure, locate, remediate, verify, report.

Measurement covers integrated loudness (EBU R128 at -23 LUFS ±1, ATSC A/85 at -24 LKFS ±2), true peak against -1.0 dBTP, subtitle reading speed and cue duration against the Netflix Timed Text Style Guide, plus black and frozen frame detection. Loudness gets sampled every 100 ms, so a scan is a time series rather than a single verdict, and that series lands in ClickHouse.

Then the interesting part. Instead of applying one gain across the whole programme, a Gemini node writes its own SQL over the 100 ms series to locate the contiguous passages that actually offend, and only those seconds get touched. The repair runs in ffmpeg. The `verify` step re-measures and fails loudly if the delta didn't land, and a second Gemini node queries the before and after stages against each other looking for collateral damage the deterministic check can't see.

The catalog view is the product surface a supervisor would actually live in: 32 measured titles, each with its failure count before and after, served through the `mcp-clickhouse` MCP server. Current verdicts across those 32: 19 improved but needing a human, 10 still failing, 2 not remediated, 1 delivery ready.

### What it does not do

Stated up front rather than buried, because a QC tool that overclaims is worse than no QC tool.

Loudness repair works end to end and gets re-measured. Subtitle retiming is partial: cues only extend into genuinely free space, so a densely packed track can't be fully fixed by moving timecodes, and rewriting an over-long line is a human judgement. Black and frozen frames are detected and never repaired, because a reel change, a fade and physical damage look identical to a machine.

Measurement runs over a bounded window per title rather than the full feature, so a demo run finishes in front of you. The UI says which window every number came from, on every number.

Nobody has used this in production. The corpus is public domain films from archive.org. There's no post house quoted here saying it saved them a redeliver, because that hasn't happened.

## How we built it

**The graph is a real ADK workflow, not a wrapper around a for loop.** Twelve nodes built as a `google.adk.workflow.Workflow`, using `node`, `START`, `JoinNode` and `RetryConfig` from ADK 2.8.0. Three of the twelve hold a model: `window_scout`, `repair_planner` and `regression_auditor`. The other nine are ffmpeg or SQL joins, deliberately, because a model that can decide to skip the re-measure is a model that can report a repair that never happened. `scan_audio` and `scan_picture` are two independent ffmpeg processes fanning out into a `JoinNode`, so the parallelism is structural rather than decorative.

**ClickHouse is reached through the official `mcp-clickhouse` server.** The binary at `/usr/local/bin/mcp-clickhouse` exposes `list_databases`, `list_tables` and `run_query`, and the agents call it over stdio like any other MCP client. `/api/catalog` on the live deployment returns `"via": "mcp-clickhouse"` alongside its 32 titles, so the integration is inspectable from outside the process. A production run on the live URL, job `0f511b19`, recorded 3 MCP tool calls from `window_scout` and 8 from `regression_auditor`. `repair_planner` made zero, which is by design: it holds no tools so it can't query, which means it can't invent a passage that isn't in the table. It only decides.

Every MCP call passes an ADK `before_tool_callback` that refuses anything which isn't a `SELECT` or `WITH` over one of five named tables. A language model never holds a write connection to the QC record.

**ClickHouse is doing work that a row store would not enjoy.** `ebur128` emits ten readings a second, so one 90 minute feature is roughly 54,000 rows and a 500 title catalog is around 27 million. One title in the demo corpus carries 2,402 samples over a 60 second scan. A percentile query over that series came back in 15 ms having read 8,411 rows, and we read that back out of `system.query_log` rather than timing it with a stopwatch. The scout's queries aren't primary key lookups either: the SQL Gemini wrote for *Vicki* is a gap and island grouping with `ROW_NUMBER() OVER` that finds contiguous runs of offending 100 ms samples and discards the short ones. It's recorded verbatim in the `fail_windows` table and served back through the API, so "the model chose this query" is something a judge can read rather than something we assert.

**Gemini is load bearing, and we can prove it by removing it.** `classify` raises `GeminiRequired` with no offline fallback, verified by test. Pull the credentials and the run stops rather than quietly degrading into the same output. That was a deliberate rewrite: an earlier version had a deterministic fallback producing the same plan shape, which made the model decorative, since you could delete Gemini and the product would behave identically.

Serving is Cloud Run. Gemini runs on Vertex AI, ClickHouse holds the telemetry, ffmpeg does every measurement and every repair. No number that reaches a verdict comes from a model.

## Challenges we ran into

**Two packages, one protocol, incompatible pins.** `google-adk` pins `mcp>=1.24,<2`. `mcp-clickhouse` needs `fastmcp>=4`, which wants `mcp>=2`. Installing both into one environment leaves an executable sitting on disk that dies at import, and the MCP client surfaces that as "Connection closed", which reads like a network problem and sends you debugging the wrong layer for an hour. The fix came from remembering what MCP actually is: a subprocess protocol. The server doesn't need to share our interpreter, it needs to be runnable. So `mcp-clickhouse` lives in its own virtualenv at `/opt/mcp-clickhouse-venv`, we point `MCP_CLICKHOUSE_BIN` at it, and the version conflict stops being a conflict.

**A scout with no tools doesn't stop having opinions.** The first time the graph ran end to end, the MCP subprocess had already died at startup. The scout returned five passages with entirely plausible timecodes, one of them 1782 seconds into a 60 second scan. Nothing errored. The output looked like work. Now the run refuses to start unless the server answers a real `list_tools`, and there's a test that goes red when that check is removed.

**A check that can't fail.** While making sure each guardrail was genuinely exercised, we found the write-keyword fence was answering first for every case in the write test, so disabling the `SELECT` only rule left the whole suite green. It looked like two checks; it was one check and a decoration. That rule now has cases only it can catch.

**Single pass `loudnorm` made a file worse.** It runs in dynamic mode and measurably moved a -24.3 LUFS file to -25.3, further from spec than where it started. The `verify` step caught it, which is the entire argument for having a `verify` step. Two pass now, with a regression test that fails when the fix is disabled.

**Sometimes the clever path correctly does nothing.** On the sample production run, `locate` found 0 passages, so remediation fell back to normalising the whole programme. The agent ran, queried, and had nothing localized to point at on that title. Passage level repair needs a master that's under target *and* peaky, which is a modern high crest factor mix rather than a 1950s optical transfer. Reporting that honestly was more uncomfortable than fixing a bug, and it's the right answer: a tool that finds something to repair on every file is a tool whose findings mean nothing.

## Accomplishments we're proud of

The headline number survives contact with a stranger. Someone with no access to this repo, on a different machine, ran this and got **-26.1 LUFS**, matching the published table exactly:

```bash
ffmpeg -hide_banner -nostats -t 300 \
  -i "https://archive.org/download/vicki-1953/Vicki%20%281953%29.mp4" \
  -af ebur128 -f null -
```

That's the whole posture of the project in one command. Most demos ask you to trust a screenshot of a dashboard built over data you can't reach.

Beyond that: Gemini wrote non trivial analytical SQL that nobody hand authored, and it's on the record in a table you can query. The agents can read the QC ledger and structurally cannot write to it. `verify` caught a repair that made a file worse, in the build, which is the case a QC tool exists for.

## What we learned

MCP being a subprocess protocol isn't trivia, it's the escape hatch from Python dependency resolution. Two packages that can't share an environment can still share a workflow.

Giving a model fewer tools made it more trustworthy, not less. `repair_planner` holding no tools is why it can't fabricate a passage. The failure mode we hit wasn't a model refusing to answer, it was a model answering confidently with its data source dead, and no exception anywhere in the stack.

Splitting the decision from the arithmetic is where the agent earns its place. The model picks *where* to look and *what* to repair; Python computes the dB and ffmpeg produces every figure. That split is exactly why every claim on the page is reproducible.

And the boring one: a passing test proves nothing until you've broken what it guards and watched it go red.

## What's next

Full feature scans instead of bounded windows, which is an infrastructure problem rather than a correctness one, since the pipeline already handles them and just takes minutes per title.

A real master from a real post house. Everything here holds on public domain films and a synthesised modern master, and that's a different thing from a delivery that someone is being paid for. That's the gap worth closing first.

Subtitle line rewriting stays with a human, on purpose. Extending cue timing into free space is arithmetic; deciding which words to cut from a line is not, and we'd rather flag it with the specific reason than pretend.

## Built with

Python, Google Agent Development Kit (ADK 2.8.0), google.adk.workflow.Workflow, Gemini, Vertex AI, Google Cloud Run, ClickHouse, mcp-clickhouse (official MCP server), Model Context Protocol, ffmpeg, ebur128, EBU R128, ATSC A/85, Netflix Timed Text Style Guide, pytest, HTML, CSS, JavaScript

## Try it yourself

**Live app:** https://deliverable-387894104564.us-central1.run.app

`/api/health` returns the connected ClickHouse version. `/api/catalog` returns the 32 measured titles and says `"via": "mcp-clickhouse"` so you can see which path the data took.

**Repo:** https://github.com/kamalbuilds/deliverable (public, MIT licence)

**Demo video:** https://youtube.com/@kamal `TODO REPLACE` <- placeholder, do not submit this URL

**Reproduce the headline number without our code.** One command, one public file, no account:

```bash
ffmpeg -hide_banner -nostats -t 300 \
  -i "https://archive.org/download/vicki-1953/Vicki%20%281953%29.mp4" \
  -af ebur128 -f null -
```

Look for `I: -26.1 LUFS` in the summary. EBU R128 wants -23.0 ±1, so that master fails by 3.1 LU.

Two flags matter. Keep `-t 300`, because every figure we publish is a measurement of a bounded window and without it ffmpeg reads the whole feature and returns something else. Don't add `-loglevel error`, because it suppresses the `ebur128` summary and you get empty output.
