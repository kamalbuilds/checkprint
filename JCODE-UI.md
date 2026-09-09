# WORK ORDER: rebuild Checkprint so it is a loudness bay, not a dark dashboard

You are Claude Opus. Effort high. Visual judgment is the job. 
Do not add a chip, a copy button, or a catalog column. Rebuild the first viewport.

Wordmark **Checkprint**. You are in `projects/deliverable`. Rewrite `web/index.html` (HTML/CSS/JS in that file). Keep every existing API. Do not introduce Next.js, Tailwind, or Inter.

## Why the current page is slop

It is a 62/38 split panel with a 4px-radius meter box, Tailwind green `#22c55e` / yellow `#f59e0b`, a Netflix-style thumb rail, and an 11-column catalog table. A post supervisor does not work in a dashboard. They watch a meter and they listen.

## Design Read (already locked)

`.uicraft-read.json` exists. Tokens from `DESIGN.md` (Runway):
- Type: DM Sans (abcNormal substitute). Tight, weight 400, tracking negative. Not Inter.
- Surface: `#030303` on `#000`. Interface almost invisible.
- Accent: white. One. No green pass badges. No yellow.
- Radius: 2px. No 12px cards.
- Showpiece: none.

Paste and obey this contract before writing CSS:

```
You are implementing UI. Follow uicraft contract. Do not search registries.
Keep HTML/CSS. No Inter. No purple gradient. No 3 feature cards.
uicraft gate --cwd web must exit 0. Then uicraft look --url http://127.0.0.1:8083
```

## First viewport (this is the product)

Full-bleed still of the selected title (`https://archive.org/services/img/{identifier}`), 100vh. The still is the page.

Over it, right side, a **hardware loudness meter**, not a card:
- Vertical scale -40 LUFS to 0. Hard rule at **-23**. ±1 LU band.
- Two needles or two thin bars: illegal master (dim white) and repaired (bright white). Empty before a run, not a fake -23.
- The integrated number in ~72-88px DM Sans, tracking tight. After a run it is a pair: `-16.1 → -22.9`. Fail = the number sits off -23. Pass = it sits on the rule. Do not paint it green.
- Under the meter, two transports labelled with state: **play the illegal master** / **play the repaired master**. They drive `#a-before` / `#a-after`. Disabled until the job is done.
- One primary: **Measure and repair**. While running, the six pipeline steps as a mono log on the still (ingest measure classify remediate verify report). Not dots. Not a wizard.

Title + archive id sit bottom-left on the still, small. Default-select the first title so load is never empty.

Filmstrip under the still: each thumb is a title with its last measured LUFS burned on it (from `/api/catalog` when present). Not a generic poster rail.

## Below the fold

Check sheet: one row per defect, measured vs spec vs after. Cue offenders with timecode stay. Sustained loudness as a small SVG range from `/api/loudness-profile/{id}` plus the query_cost line. Catalog is the filmstrip, not a spreadsheet. Supervisor/MCP trace stays as a disclosure at the bottom.

## Keep

All current `/api/titles`, `/api/catalog`, `/api/run`, `/api/loudness-profile`, `/api/review`, `/api/mcp-transcript` shapes. Audio elements `#a-before` `#a-after`. No new backend unless the still URL is missing.

## Hard bans

Inter. Yellow. Green success. Purple. Gradient text. Emoji. Em dash. En dash. Pills. "Elevate". 11-column tables above the fold. `border-radius` over 2px. Feature cards.

## Done when

1. `uicraft gate --cwd web` exits 0.
2. `uicraft look --url http://127.0.0.1:8083` at 1440. Name remaining tells. Fix them.
3. A judge at 8 seconds sees a film and a meter at -23, not a dashboard.
4. Commit.

Blind spot you must state: what the still does when archive.org img 404s.
