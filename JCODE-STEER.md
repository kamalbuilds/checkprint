# WORK ORDER: Checkprint, steal Accord's "exact failing moment"

You are in `projects/deliverable`. The product wordmark is **Checkprint**. Do not write DELIVERABLE in the UI.

Competitor: Accord (https://devpost.com/software/accord-j973qz, ClickHouse + mcp-clickhouse + Gemini ADK). They win the subtitle beat by letting a reviewer **see the exact cue that failed**, then re-measure after a bounded repair. We already measure per-cue defects in `qc/measure.py` (`over_reading_speed` is a list of `{cue, cps, text}`). The check sheet currently collapses that to one row.

## Do this

1. Keep the existing first viewport (film still + LUFS meter). Do not restyle the page.
2. Under the subtitle / reading-speed row of the check sheet, render the actual failing cues (index, cps, first 80 chars of text) from the verify/measure payload. If that list is not yet in the JSON the UI sees, thread it through from existing measure output. No new backend product. No invented fields.
3. After repair, the same list must shrink. If a cue is still over, it stays. Empty list = that check passed.
4. Fonts stay DM Sans + JetBrains Mono. Canvas near-black. No Inter. No yellow. No em dash. No emoji.

## Done when

A judge can pick a title, run Measure and repair, and see named failing cues, not only "24.6% over 17 cps".
