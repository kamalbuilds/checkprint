# WIN-CONDITIONS, Checkprint, Agentic Cinema, ClickHouse track

Project-local copy of the gate. Every line is answered from something that was run,
not from intent.

Source for the field numbers is `../README.md` and the live deployment at
https://deliverable-387894104564.us-central1.run.app.

```
Scoreboard: 260+ competing repos verified on GitHub 2026-09-08. Parallel 28, Grafana 24, ClickHouse ~20, Replit 6, IBM ~3. The crowd sits on three ideas: script clearance (10+), render-farm SRE (10+), retention analytics (7+). Zero entries do measured broadcast delivery QC. Named ClickHouse-track rivals: Accord, Cliffhanger, Cutting Room Copilot, cut-point, signaldrop.
Bar to beat: a demo the judge can independently reproduce without our code. No competitor found offers one; they output opinions over data the judge cannot access. Concretely, beating Accord means a reviewer sees the exact failing passage and the exact SQL that located it, not a summary.
Asset we will own: a QC measurement corpus over public-domain features, one ebur128 reading per 100 ms, 32 titles measured and live in ClickHouse today, plus the fail-window table that records which passage of which master an agent's own SQL located and what was done to it.
Off-platform buyer: a post-production delivery QC operator or post supervisor at an indie distributor, post house or film archive, who ships masters to platforms that reject on spec and today pays for Telestream Vantage, Venera Pulsar or Interra Baton, or eyeballs it. That buyer exists without this hackathon.
Single entry: Checkprint, one entry, ClickHouse track. Vault and Sixteen Seventeen are other agents' projects and are not submitted alongside this on the same track.
Verb the brief names: "show off a deterministic, multi-step agent" that "solves enterprise friction", built natively on ADK.
Our product performs that verb: YES. measure, locate, plan, repair, re-verify, audit. The remediate node writes a corrected file, verify re-measures and the run fails loudly if the delta did not land, and the regression auditor queries the before stage against the after stage in ClickHouse for damage the deterministic check cannot see.
Metric plan: integrated LUFS delta per title against EBU R128 (-23 +/- 1). True peak against -1.0 dBTP. The passage the agent's SQL located moves by exactly the planned gain while the rest of the programme moves 0.0 dB within 0.1 LU, which is the outcome only the agent-located windows can produce and which a global gain cannot. Caption cue failure percent against Netflix 17 cps. Count of titles passing and failing in the catalog view.
Live by: live now at https://deliverable-387894104564.us-central1.run.app, /api/health returns ok with ClickHouse 26.2.1.641 and /api/catalog returns 32 measured titles via mcp-clickhouse. Deadline Sep 09 2026.
Deviation from research: YES, twice. The adversarial critique said the winning move is agent-located fail windows driving the repair, and that was taken. Its proposed measurable, narrowing the sustained loudness range, was rejected: raising a mixer's quiet passages is a re-mix, not a repair, so the tool only ever attenuates and never lifts. The replacement measurable, loudnorm going from dynamic to linear with LRA preserved, was then also dropped after testing showed it is not reliably reachable, and the claim became the one that is exactly true: the located passage moves by the planned gain and nothing else moves.
```

## Falsification evidence already in hand

| Claim | Evidence | How it was produced |
|---|---|---|
| Real films carry real defects | `Vicki (1953)` I = -26.1 LUFS against -23 | ran ffmpeg ebur128 |
| The fix works | after two-pass `loudnorm`: -24.0 LUFS, in tolerance | ran ffmpeg |
| It measures, not recites | second title = -26.9 LUFS, a different value | ran ffmpeg |
| The gate can PASS | in-spec control file = -23.0, PASS | ran ffmpeg |
| Caption defects are real | 110 of 447 cues (24.6%) over 17 cps | ran on a real .srt |
| The located passage moves by exactly the planned gain | -3.0 dB inside 15.0s-16.0s, 0.0 dB elsewhere within 0.1 LU | ran ffmpeg over the 100ms series, 2026-09-09 |
| The scout's windows change the output file | byte-different render against the same repair with no windows | ran the remediator twice, 2026-09-09 |
| The scout writes non-trivial SQL itself | gap-and-island grouping with `ROW_NUMBER() OVER`, recorded in `fail_windows.sql` | live graph run on vicki-1953, 2026-09-09 |
| The graph runs end to end on real titles | haider-2014: 6 failing checks to 4, 231 samples written, 10 MCP tool calls | live graph run, 2026-09-09 |
| Judge can reproduce any of it | `ffmpeg -hide_banner -nostats -t 300 -i "<url>" -af ebur128 -f null -` gives -26.1 LUFS | one command, public file, independently confirmed on a second machine |

## Honest risks

1. **ClickHouse plus `mcp-clickhouse` is the single hard dependency.** It is wired and live,
   but if the deployment loses it the track requirement fails and there is no fallback that
   still qualifies. The pipeline now refuses to start rather than degrade, which is the right
   behaviour and also means an MCP outage is a total outage.
2. **Full-film processing is slow.** Mitigated by a bounded window per title, stated in the
   UI rather than implied away.
3. **Nobody has run this on a paying delivery.** The corpus is public-domain features plus a
   synthesised modern master. That is stated in the README rather than dressed up.
4. **The windowed repair does not fire on the public-domain corpus.** Measured across five
   titles: those transfers are either quiet with plenty of headroom or already clipped and
   too loud, and both cases are answered by one global gain. The mechanism is exercised by
   test rather than by a demo title, and the README says so with the numbers.
