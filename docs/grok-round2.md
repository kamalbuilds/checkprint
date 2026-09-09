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

Grok's replies are reproduced with their wording unchanged, with one
mechanical exception: em dashes have been replaced with commas or full stops to
satisfy this repo's writing rules, and the fixed-width line wrapping the CLI
applies has been joined. No words were added, removed or reordered.

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

## Follow-up exchange: is a synthesised master a rigged demo?

The Q3 answer above asks for a constructed file on the live service. Nobody had
tested whether that reads as a wired fixture to a ClickHouse panel, so it was put
back to Grok. Attempted on grok-4 first, which refused again with the same 15-per-20-hours
message, so this is also grok-3. Same em dash and line-joining treatment as above.

Asked: is the transient-master plan really the highest-value change with under ten
hours left, and is there a risk in demoing a synthesised master rather than a real
archival transfer, and if so how should it be framed on camera?

> 1. Almost, same class of fix, one tighter version.
>
> The diagnosis is right: on the public-domain transfers the scout SQL is real and the guardrail is real, and neither is in the causal path of the WAV. Your own win note already says the differentiator is "only the located passage moves; a global gain cannot do that." That is not audible on Vicki / Werewolf. So the remaining hours have to make windows change the file a judge can play.
>
> Do not spend those hours hunting a "real" archival file with a quiet bed and a near-full-scale sting. 1950s optical tracks mostly will not give you that collision, and a miss burns the window.
>
> Do this instead of "synth + film three renders" as the whole plan:
>
> - Pin a constructed headroom-collision title on the live service (quiet bed + short near-0 dBTP burst), generated from the script you already have, not a one-off DAW bounce.
> - Take dynamic loudnorm / hardcoded LRA=11 off that path. After the windowed volume=...:enable='between(t,...)' apply a linear lift (or loudnorm with linear=true). If you leave LRA=11 on, the AGC will smear the island and the three-way will collapse again.
> - Put the three-way on the live bay, not only in the video: source / global-only / windowed-then-linear, with I, TP, and the scout SQL + island timestamps on screen. Judges will open the Cloud Run URL, not trust a cut.
> - Keep Vicki in the catalog and say out loud that on that transfer windows are a no-op. That is correct behaviour, not a bug.
>
> That is the highest-value change. A prettier meter, more MCP calls, or catalog quantiles will not save you if the two repaired files hash the same.
>
> 2. Yes, there is a risk. Frame the fixture as a spec probe, not as the corpus.
>
> A ClickHouse panel will treat a cartoon WAV as rigged if it is the only title you play and you imply the database found a "real" defect. They already know fixtures can be shaped so ROW_NUMBER() OVER looks load-bearing.
>
> Say this on camera, in this order:
>
> 1. "Thirty-two real transfers are in loudness_samples. That is the catalog. Anyone can re-measure Vicki with the ffmpeg line the API returns."
> 2. "On those transfers a global gain and a windowed gain land on the same I and TP. The tracks are either quiet with headroom or already against the ceiling. The scout still writes the SQL; the deterministic node drops windows the table does not support; the file does not change. That is the honest result."
> 3. "Broadcast QC has always used constructed sequences for a single clause of the spec, same reason EBU ships Tech 3341/3342 signals instead of a random feature."
> 4. "This file is generated by the repo script: low-level bed plus a short sting whose true peak is inside the lift we need for -23. A global +N dB takes the sting over -1 dBTP. Windowed attenuate-then-linear does not. Same 12-node graph, same SELECT-only MCP, same confirm node. Only the signal changes."
> 5. Play the three files. Show the after-stage series in ClickHouse: island moved by the planned dB, the bed did not, within 0.1 LU.
>
> Do not call it a film master. Call it a headroom-collision case. Do not hide that it is synthesised, the README already admits the mechanism is "exercised by test rather than by a demo title." If you hide it, the panel will find that sentence. If you lead with it, the 32-title corpus stays the measurement story and the fixture stays the policy story. That split is what keeps it from reading as a wired demo.

### Verdict on each claim in the follow-up

| Grok's claim | Verdict | Evidence |
|---|---|---|
| "Thirty-two real transfers are in loudness_samples" | **REAL** | Live `/api/catalog` returns 32 rows, and reports `"via": "mcp-clickhouse"`. Grok got the count right without being given it. |
| The win note already claims "only the located passage moves; a global gain cannot do that" | **REAL** | `docs/WIN-CONDITIONS.md:17` states the passage "moves by exactly the planned gain while the rest of the programme moves 0.0 dB within 0.1 LU, which is the outcome only the agent-located windows can produce and which a global gain cannot." Accurate paraphrase, and it is the claim that the corpus does not currently support. |
| The README admits the mechanism is "exercised by test rather than by a demo title" | **REAL, and this is the sentence to worry about** | `README.md:281` and `docs/WIN-CONDITIONS.md:49`. Grok's warning that a panel will find it is well founded, because it is in the submission material. Its advice to lead with it rather than let it be discovered is sound. |
| "Put the three-way on the live bay, not only in the video ... judges will open the Cloud Run URL, not trust a cut" | **REAL as reasoning** | Consistent with everything else in this round: the service is public and every figure in it is checkable. This raises the cost of the Q3 fix from a video edit to a deployment, which is worth knowing before committing the hours. |
| "loudnorm with linear=true" as the alternative to LRA=11 | **STILL WRONG in the same way** | It repeated this. `ffmpeg -h filter=loudnorm`: `LRA` is `(from 1 to 50) (default 7)` and cannot be omitted, so dropping `LRA=11` silently selects 7 and compresses harder. Its other phrasing here, "apply a linear lift", is the correct implementation: a constant `volume=` gain computed from the measured integrated loudness, applied after the window filters. |
| EBU ships Tech 3341/3342 test signals rather than programme material | **REAL in substance** | EBU Tech 3341 specifies the EBU Mode meter and its compliance test signals; Tech 3342 defines loudness range. Using constructed signals to test one clause of a spec is standard practice, so the on-camera framing is defensible. |
| The Click-a-Thon India 2026 page among its sources | **NOT RELIED ON** | It surfaced a real-looking event URL. Nothing in this document depends on it, and no claim about judges, criteria or deadlines from any round of this critique has been confirmed against an official page. Treat all of those as unverified. |

The net of the follow-up: it agreed with its own earlier answer, raised the cost by
insisting the three-way render must be on the live service rather than only in the
video, and supplied the framing that keeps a constructed file from reading as rigged,
which is to present the 32 real transfers as the measurement story and the constructed
file as a single-clause spec probe, explicitly labelled as synthesised.
