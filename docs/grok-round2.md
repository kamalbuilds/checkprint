# Grok round 2: Checkprint

Second pass of the critic loop. Round 1 criticised Checkprint, the code was then
rebuilt, and nobody went back to Grok to ask whether the rebuild survives. This is
that return trip.

## Conditions

Conducted 2026-09-09 over the `grokweb` CLI on the x.com transport.

**Model: grok-3, not grok-4.** grok-4 was exhausted at the account level before this
session began. The exact refusal, at 10:16Z:

```
Rate limited by x.com Grok. You've reached your limit of 15 Grok 4
questions per 20 hours for now. Please sign up for Premium+ to access more or
check back later. Quota is 15 questions per 20 hours.
```

grok-3-mini was also tried and returned `Error: Empty response. The stream carried
no final message chunk.` grok-3 answered.

Two notes on method:

- Sent through `grokweb ask`, not `grokweb chat`. `chat` hardwires a 120 second
  request timeout and the reply was truncated mid stream at 23,248 bytes on the
  first attempt. `ask` accepts `-t`.
- `ask` carries no conversation memory, which is deliberate. Round 1 recorded a
  routing fault where a reply came back under another conversation's header and
  answered a different project's question. A single self contained one shot cannot
  be contaminated by a neighbouring thread, so every claim is restated inside the
  question.

Grok performed its own retrieval and cited the repo, so the answer is grounded in
the deployed code rather than only in the brief.

## What was put to it

The four hardest lines from round 1, quoted back verbatim:

1. "If windowed and global produce the same I and the same TP, delete the scout and keep loudnorm."
2. The MCP agent is a guided tour of tables the app already owns.
3. The 100ms loudness series is written by Python and read by Python, so the table is ballast.
4. "If the UI still prints 'ingest / measure / classify' from your own enum, Workflow bought you nothing visible." Cosmetic ADK makes you look more like the crowd, not less.

Then the rebuild: the 12 node `google.adk.workflow.Workflow` called `delivery_qc`,
two concurrent ffmpeg scans converging on a `JoinNode`, three Gemini nodes,
`window_scout` composing its own gap and island SQL, `confirm_windows` re-deriving
every window from the table, `repair_planner` holding no tools, the SELECT only
guardrail with CTE alias parsing, 11 MCP tool calls in a production run, verify
going 8 to 4.

And, stated up front rather than hidden: `loudnorm` is called with a hardcoded
`LRA=11`; and on the public domain corpus the windowed repair provably does not
change the outcome versus one global gain, because those transfers are either quiet
with headroom or already clipped.

## Grok's answer, verbatim

> Q1. Same failure in a better costume. The costume is real. The failure that kills the file is unchanged.
>
> Line 1 is alive. On every public-domain title a judge can press play on, windowed and global still produce the same I and the same TP. window_scout now writes a gap-and-island query, confirm_windows re-derives it from deliverable.loudness_samples, and fail_windows.sql is stored. None of that reaches the WAV. remediate still ends in loudnorm=I=...:TP=...:LRA=11, and on this corpus that single filter is the whole repair. /api/agents currently says that removing window_scout changes the output file. On the titles in the bay, that sentence is false.
>
> Line 4 is dead. The live page and /api/agents print the twelve-node google.adk.workflow.Workflow (delivery_qc), not ingest / measure / classify. Fan-out to two ffmpeg scans, JoinNode, three Gemini nodes, planner with tools: none, that is a real graph, not an LlmAgent wrapper. Cosmetic ADK is no longer the charge.
>
> Line 2 is wounded, not dead. The agent is no longer a list_tables tour. Gemini authored ROW_NUMBER() OVER against deliverable.loudness_samples, and confirm_windows will drop a window the table does not support. That is not sightseeing. It is still an agent querying a table the same job inserted thirty seconds earlier. There is no independent store, no other writer, no catalog the product does not own. MCP is a second door onto owned rows.
>
> Line 3 is dead as you worded it, alive for the artifact. The series is no longer written-by-Python-and-read-by-Python-only. Python writes it (store_run / clickhouse-connect). Gemini reads it over MCP. confirm_windows treats the table as source of truth. Delete the table and the locate loop has nothing to validate against. That is not ballast for the agent. It is ballast for the rendered master: those rows do not change a sample the judge can hear. That is the distinction. Python-writes-and-Python-reads is no longer the right sentence. "The table is the evidence locker for a no-op" is.
>
> Q2. On every title the live demo will play, the ClickHouse query is not in the causal path of the delivered WAV.
>
> That is the whole reason. The track is won when the query changes the artifact. Here the query is real, stored, guardrailed, and then discarded by a global loudnorm that was going to do the same I/TP anyway. Eleven MCP calls and 8 to 4 on verify do not move that fact.
>
> Q3. Make the windowed repair change a file the video can play. Do not reframe as the main move.
>
> You already have the fixture: the synthesised master with a 1 s transient, and test_removing_the_scout_changes_the_repaired_audio. The public-domain transfers cannot show the conflict. Do not hunt archive.org for a peaky-under-target feature in ten hours. Put the transient title in the live bay and film that.
>
> Exactly this:
>
> 1. Build one short master the judge will understand in twenty seconds: dialogue/bed under target (about -28 LUFS) plus two or three 0.5-1.5 s near-full-scale bursts. Required lift to -23 is ~5 dB. Bursts that sit at -2 dBTP become illegal after a global lift. Optical transfers will never give you that shape.
> 2. Take LRA=11 off the demo path. Two-pass loudnorm with linear=true and no LRA target, or a constant volume= after the window volume=...:enable='between(t,...)' filters. If you leave LRA=11 on, the compressor rewrites the whole programme and the window disappears into the same mush as global-only.
> 3. Run three renders of that same file: illegal source, global-only, windowed-then-linear. Measure all three with the same ffmpeg -af ebur128=peak=true command you already print. The video is those three numbers plus the waveforms: global-only goes over -1 dBTP on the bursts; windowed knocks only those seconds down, then the linear lift hits I without a TP over.
> 4. On screen, in order: the scout SQL (the ROW_NUMBER() statement, not a paraphrase), confirm_windows keeping those islands, the filtergraph with between(t,...), the three ebur128 summaries. Say out loud that Vicki correctly returns zero windows. Do not say the archive titles were locally repaired.
> 5. Change the /api/agents line that claims removing the scout changes the file. True for the transient title. False for the catalog the homepage loads.
>
> If that title is not on the live URL and in the video, do not spend the ten hours on another MCP guardrail. The honest fallback claim, used only if the transient file does not ship: the scout's job is to decide whether a local repair is required; on optical transfers it returns no islands and that is the correct query result; loudnorm then does the global lift; ClickHouse's product is the evidence for "do not touch local passages," not a different WAV. That claim is accurate and still loses Q2, because the sponsor's database never changes the file. Ship the transient title.

## Verdict on each claim

Checked against the repo, the deployed service, the installed `google-adk` 2.8.0 and
the local ffmpeg build.

| Grok's claim | Verdict | Evidence |
|---|---|---|
| `/api/agents` asserts that removing `window_scout` changes the output file | **REAL** | Live `/api/agents` returns, for `window_scout`, `"removing_it": "the repair becomes one gain over the whole programme, so the output file changes"`. On the public domain catalog that sentence is not true. Sharpest integrity finding of the round. |
| `test_removing_the_scout_changes_the_repaired_audio` exists | **REAL** | `tests/test_multi_agent_graph.py:162`. |
| A synthesised master with a short transient already exists as a fixture | **REAL** | `hot_master`, `tests/test_multi_agent_graph.py:98-126`. 15s pink noise at `volume=-14dB`, then 1s at `volume=1.5dB`, then 15s quiet, concatenated. Grok said "two or three 0.5-1.5 s bursts"; it is one 1s burst. Shape right, count slightly off. |
| That test is non vacuous | **REAL, and stronger than Grok knew** | The threshold is measured off the fixture rather than hardcoded, and there is a guard at `tests/test_multi_agent_graph.py:184-187`: `assert peak > ceiling, "fixture does not exercise the case"`. The fixture cannot silently stop exercising the case. |
| "Vicki correctly returns zero windows" | **REAL** | `README.md:261`, "It returned nothing for *Vicki*, and that is the correct answer." |
| `LRA=11` is hardcoded on the repair path | **REAL** | `qc/measure.py:549` and `:555`, both loudnorm passes. |
| `volume=...:enable='between(t,...)'` is how windows are applied | **REAL** | `qc/measure.py:492-502`, one filter per window, so no other second of audio is touched. |
| `ebur128=peak=true` is the measurement command | **REAL** | `qc/measure.py:143`. |
| "Two-pass loudnorm with `linear=true` and **no LRA target**" | **WRONG as written, right in substance** | `ffmpeg -h filter=loudnorm` on this machine: `LRA` is `(from 1 to 50) (default 7)`. There is no way to omit an LRA target; leaving it out silently selects 7, which is tighter than 11 and would compress harder. `linear` does exist and defaults to true. Grok's alternative in the same sentence, a constant `volume=` computed from the measured integrated loudness applied after the window filters, is the correct implementation and the one to use. Do not follow the first half of item 2 literally. |
| The pipeline is a real 12 node Workflow, not a wrapper | **REAL** | `agent/graph.py:470-492` builds a `google.adk.workflow._workflow.Workflow`; the built graph is `__START__` plus 12 nodes. `Workflow`, `JoinNode`, `FunctionNode`, `LlmAgent` and `McpToolset` all exist in the installed `google-adk` 2.8.0, so nothing Grok named here is invented. |
| `fail_windows.sql` is "stored" | **WRONG, and our documentation is the cause** | No file of that name exists anywhere in the tree. Grok repeated our own error: `README.md:235` and `docs/WIN-CONDITIONS.md:33` both say "recorded in `fail_windows.sql`", while `docs/SUBMISSION.md:55` correctly says the `fail_windows` **table**. The substance is real: `qc/schema.sql:176-195` defines the table and the live row holds a gap and island query using `row_number() OVER (ORDER BY t_seconds)` over `deliverable.loudness_samples`. Two further mismatches: the recorded statement is lowercase `row_number()` while README prose shows `ROW_NUMBER()`, and the live `fail_windows` row belongs to a different title than the Vicki query the README presents. |
| Q2, the ClickHouse query is not in the causal path of the delivered WAV on any demo title | **REAL, and the finding that matters** | Not rhetoric. It follows from `LRA=11` plus a corpus whose failures are all answerable by one global gain. Same conclusion the team had already reached internally, arrived at independently. |

Nothing in this round was unverifiable. Every named symbol, file, test and ffmpeg
flag resolved to something real on disk, with the single exception of
`fail_windows.sql`, which is a doc bug of ours that Grok faithfully echoed.

## Where this leaves Checkprint

Grok moved on two of its four lines and held two.

- **Dead:** line 4, cosmetic ADK. The graph is real and the deployed service prints it.
- **Dead as worded:** line 3. Gemini now reads the series over MCP, so "written by
  Python and read by Python" is retired. Grok replaced it with a sharper sentence:
  the table is "the evidence locker for a no-op".
- **Wounded:** line 2. The agent composes its own SQL so it is not a guided tour, but
  it still queries only rows the same job inserted, with no independent writer.
- **Alive, and now the whole case against the project:** line 1.

Single highest value fix it named: put a master on the live service whose shape
forces the windowed repair to matter, take `LRA=11` off the demo path so the
compressor does not erase the window, and film three renders of that one file. The
fixture already exists in the test suite and is already proven able to fail. The
correction to make before acting on it: implement step 2 as a computed constant
`volume=` gain, not as "loudnorm with no LRA target", which ffmpeg does not support.

Second, independent of the video: the `removing_it` string on `/api/agents` claims
something that is false for every title the homepage loads. One line honesty fix, and
a judge who tests it will find it.
