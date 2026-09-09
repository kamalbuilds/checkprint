"""A catalog row must land the reviewer on that exact title.

The catalog is the index a judge scans first. If a row for
`werewolf_in_a_girls_dormitory_ipod` puts a different film in the bay, the
number they clicked no longer describes what they are looking at, which is
worse than the row doing nothing at all.

Two failures are pinned here:

1. Every measured title in `/api/catalog` must exist in `/api/titles`, keyed on
   `title_id` == `identifier`. Otherwise the row has nowhere to go.
2. `selectByTitleId`, taken from the shipped `web/index.html` rather than a
   copy, must select the matching title and must refuse an unknown id instead
   of falling through to the first film.

Skipped when the server or node is unavailable.

    uv run --python 3.12 python -m pytest tests/test_catalog_row_selects_title.py -v
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
INDEX = ROOT / "web" / "index.html"
BASE = os.environ.get("CHECKPRINT_URL", "http://localhost:8080")
WEREWOLF = "werewolf_in_a_girls_dormitory_ipod"


def _api(path: str, timeout: int = 120):
    try:
        with urllib.request.urlopen(f"{BASE}{path}", timeout=timeout) as r:
            return json.load(r)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        pytest.skip(f"Checkprint server unavailable at {BASE}: {str(exc)[:80]}")


@pytest.fixture(scope="module")
def catalog():
    payload = _api("/api/catalog")
    if payload.get("warming"):
        pytest.skip("ClickHouse still warming")
    rows = payload.get("catalog") or []
    if not rows:
        pytest.skip("No measured titles in the catalog yet")
    return rows


@pytest.fixture(scope="module")
def identifiers():
    return [t["identifier"] for t in _api("/api/titles?rows=8")["titles"]]


def test_every_catalog_row_has_a_title_to_select(catalog, identifiers):
    """A row whose title_id is missing from /api/titles is a dead click."""
    missing = [c["title_id"] for c in catalog if c["title_id"] not in identifiers]
    assert not missing, f"catalog rows with no filmstrip entry: {missing}"


def test_werewolf_row_is_selectable(catalog, identifiers):
    """The 116-cue row named in the work order specifically."""
    ids = [c["title_id"] for c in catalog]
    if WEREWOLF not in ids:
        pytest.skip(f"{WEREWOLF} has not been measured in this environment")
    assert WEREWOLF in identifiers


# ── the resolver itself, run as shipped ──────────────────────────────────────

def _resolver_source() -> str:
    """Lift selectByTitleId out of web/index.html so the test cannot drift."""
    src = INDEX.read_text()
    match = re.search(
        r"function selectByTitleId\(titleId\)\{.*?\n\}", src, re.S
    )
    assert match, "selectByTitleId not found in web/index.html"
    return match.group(0)


def _resolve(title_id: str, identifiers: list[str]):
    """Run the shipped resolver under node with the DOM calls stubbed out."""
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")

    harness = f"""
    const titles = {json.dumps([{"identifier": i, "title": i} for i in identifiers])};
    const thumbFor = {{}};
    let picked = null;
    function selectTitle(t) {{ picked = t.identifier; }}
    const document = {{
      getElementById: () => ({{ scrollIntoView: () => {{}} }})
    }};
    {_resolver_source()}
    const res = selectByTitleId({json.dumps(title_id)});
    console.log(JSON.stringify({{ ok: res.ok, picked: res.picked }}));
    """
    out = subprocess.run(
        [node, "-e", harness], capture_output=True, text=True, timeout=30
    )
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def test_resolver_picks_the_row_that_was_clicked(identifiers):
    """Clicking Werewolf selects Werewolf, not whatever sorted first."""
    if WEREWOLF not in identifiers:
        pytest.skip(f"{WEREWOLF} not in the loaded filmstrip")
    # Deliberately move Werewolf off the front. A resolver that ignores the id
    # and takes titles[0] would pass if the target happened to sort first.
    decoyed = [i for i in identifiers if i != WEREWOLF]
    decoyed.insert(min(2, len(decoyed)), WEREWOLF)
    assert decoyed[0] != WEREWOLF
    result = _resolve(WEREWOLF, decoyed)
    assert result["ok"] is True
    assert result["picked"] == WEREWOLF


def test_resolver_refuses_an_unknown_id(identifiers):
    """An id with no title must select nothing at all, not the first film."""
    result = _resolve("no_such_title_id_at_all", identifiers)
    assert result["ok"] is False
    assert result["picked"] is None
