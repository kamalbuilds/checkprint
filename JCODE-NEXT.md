# WORK ORDER: Checkprint catalog row must select that title in the bay

You are in `projects/deliverable`. Wordmark is **Checkprint**.

The catalog now shows cue counts (`116 → 84` cps cues on Werewolf). Clicking a row does nothing. Accord lets a reviewer go from the index to the failing title. `selectTitle` already exists. Catalog rows already have `title_id`. `/api/titles` already has `identifier`.

## Do this

1. Keep the QC bay. Do not restyle.
2. Clicking a catalog row must call the existing select path for the matching title (`title_id` === `identifier`). Scroll the still/meter into view. If that title is not in the loaded filmstrip, say so in the existing empty-msg language. Do not invent a title. Do not start a QC run on click.
3. A check that would fail if a catalog row for title_id `werewolf_in_a_girls_dormitory_ipod` does not resolve to that title's identifier, and would fail if a row for an unknown id silently selects the first film. Use the live `/api/catalog` + `/api/titles` payloads.
4. DM Sans, near-black, no Inter, no em dash, no emoji.

## Done when

A judge who scans the catalog can click the 116-cue row and be on that title's meter, without typing.
