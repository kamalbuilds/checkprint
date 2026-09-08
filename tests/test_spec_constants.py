"""The thresholds must match the published standards the README cites.

Prose is unchecked code: the README quotes EBU R128 clause h and the Netflix Timed
Text Style Guide, and nothing stops someone tuning a constant until a demo passes,
leaving the citation silently false. These tests pin the constants to the cited
values so that drift breaks the build instead of the claim.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qc import measure as m  # noqa: E402

README = Path(__file__).resolve().parents[1] / "README.md"


def test_ebu_r128_matches_the_published_standard():
    # EBU R128 clause h: Target Level -23.0 LUFS, tolerance +/-1.0 LU.
    assert m.EBU_R128_TARGET_LUFS == -23.0
    assert m.EBU_R128_TOLERANCE_LU == 1.0


def test_atsc_a85_matches_the_calm_act_target():
    assert m.ATSC_A85_TARGET_LKFS == -24.0
    assert m.ATSC_A85_TOLERANCE_LU == 2.0


def test_netflix_timed_text_constants():
    assert m.NETFLIX_MAX_CPS == 17.0
    assert m.NETFLIX_MAX_LINE_CHARS == 42
    assert m.NETFLIX_MAX_LINES == 2
    assert abs(m.NETFLIX_MIN_CUE_SECONDS - 5 / 6) < 1e-9


def test_readme_quotes_the_same_numbers_as_the_code():
    """Catch the README and the code disagreeing, in either direction."""
    text = README.read_text(encoding="utf-8")

    # The README states the target as "-23.0 LUFS" (with a unicode minus).
    assert re.search(r"[-\u2212]23\.0 LUFS", text), "README no longer states the R128 target"
    assert "±1.0 LU" in text or "+/-1.0 LU" in text, "README no longer states the tolerance"
    assert f"{int(m.NETFLIX_MAX_CPS)} characters/second" in text
    assert f"{m.NETFLIX_MAX_LINE_CHARS} characters per line" in text
