"""Tests for the QC core.

The governing rule: a check that cannot fail is worse than no check. So every test
here asserts BOTH directions - the check goes red on bad input and green on good
input - and the mutation test at the bottom proves the suite itself has teeth.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qc import measure as m  # noqa: E402


# --- fixtures: real files, generated deterministically ---------------------


@pytest.fixture(scope="session")
def in_spec_audio(tmp_path_factory) -> Path:
    """A file mastered to -23 LUFS. The gate must PASS this."""
    out = tmp_path_factory.mktemp("media") / "in_spec.m4a"
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-v", "error", "-y",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=30",
         "-af", "loudnorm=I=-23:TP=-2:LRA=7", "-c:a", "aac", str(out)],
        check=True, capture_output=True,
    )
    return out


@pytest.fixture(scope="session")
def too_quiet_audio(tmp_path_factory) -> Path:
    """Same tone, pushed ~8 LU under target. The gate must FAIL this."""
    out = tmp_path_factory.mktemp("media") / "quiet.m4a"
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-v", "error", "-y",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=30",
         "-af", "loudnorm=I=-31:TP=-2:LRA=7", "-c:a", "aac", str(out)],
        check=True, capture_output=True,
    )
    return out


# --- loudness: both directions --------------------------------------------


def test_in_spec_audio_passes(in_spec_audio):
    measured = m.measure_loudness(in_spec_audio)
    findings = {f.check: f for f in m.loudness_findings(measured)}
    assert abs(measured["integrated_lufs"] - (-23.0)) <= 1.0, measured
    assert findings["integrated_loudness_ebu_r128"].passed


def test_too_quiet_audio_fails(too_quiet_audio):
    measured = m.measure_loudness(too_quiet_audio)
    findings = {f.check: f for f in m.loudness_findings(measured)}
    assert measured["integrated_lufs"] < -24.0, measured
    assert not findings["integrated_loudness_ebu_r128"].passed


def test_remediation_moves_the_number(tmp_path, too_quiet_audio):
    before = m.measure_loudness(too_quiet_audio)["integrated_lufs"]
    fixed = m.remediate_loudness(too_quiet_audio, tmp_path / "fixed.m4a")
    after = m.measure_loudness(fixed)["integrated_lufs"]

    assert not m.loudness_findings({"integrated_lufs": before})[0].passed
    assert m.loudness_findings({"integrated_lufs": after})[0].passed
    assert after > before, f"remediation must raise loudness: {before} -> {after}"


def test_measurement_discriminates_between_files(in_spec_audio, too_quiet_audio):
    """Guards against a stub that returns a constant."""
    a = m.measure_loudness(in_spec_audio)["integrated_lufs"]
    b = m.measure_loudness(too_quiet_audio)["integrated_lufs"]
    assert abs(a - b) > 3.0, (a, b)


# --- subtitles: both directions -------------------------------------------

CLEAN_SRT = """1
00:00:01,000 --> 00:00:05,000
A short clean line.

2
00:00:06,000 --> 00:00:10,000
Another comfortable line.
"""

# 74 chars in 1.0s = 74 cps, far over the 17 cps limit.
DIRTY_SRT = """1
00:00:01,000 --> 00:00:02,000
This is a very long subtitle line that nobody could possibly read in time.

2
00:00:03,000 --> 00:00:03,100
Too brief.
"""


def test_clean_subtitles_pass():
    measured = m.measure_subtitles(CLEAN_SRT)
    assert measured["cue_count"] == 2
    assert all(f.passed for f in m.subtitle_findings(measured))


def test_dirty_subtitles_fail():
    measured = m.measure_subtitles(DIRTY_SRT)
    findings = {f.check: f for f in m.subtitle_findings(measured)}
    assert not findings["subtitle_reading_speed"].passed
    assert not findings["subtitle_min_duration"].passed


def test_subtitle_remediation_fixes_and_does_not_overlap():
    fixed, changed = m.remediate_subtitles(DIRTY_SRT)
    assert changed > 0

    cues = m.parse_srt(fixed)
    for a, b in zip(cues, cues[1:]):
        assert a["end"] <= b["start"], f"cues overlap: {a} / {b}"

    after = m.measure_subtitles(fixed)
    assert not after["under_min_duration"]


def test_two_digit_millisecond_timestamps_parse():
    """archive.org ASR .srt files emit 2-digit ms; a naive parser mangles them."""
    srt = "1\n00:02:00,70 --> 00:02:06,64\nline\n"
    cue = m.parse_srt(srt)[0]
    assert cue["start"] == pytest.approx(120.7, abs=0.01)
    assert cue["end"] == pytest.approx(126.64, abs=0.01)


# --- structural -----------------------------------------------------------


def test_black_detection_finds_real_black(tmp_path):
    black = tmp_path / "black.mp4"
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-v", "error", "-y",
         "-f", "lavfi", "-i", "color=c=black:s=320x240:d=5",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", str(black)],
        check=True, capture_output=True,
    )
    measured = m.measure_structural(black)
    assert measured["black_segments"], "a fully black clip must produce a black segment"
    assert not m.structural_findings(measured)[0].passed


def test_colour_bars_are_not_flagged_black(tmp_path):
    bars = tmp_path / "bars.mp4"
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-v", "error", "-y",
         "-f", "lavfi", "-i", "testsrc=s=320x240:d=5",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", str(bars)],
        check=True, capture_output=True,
    )
    measured = m.measure_structural(bars)
    assert m.structural_findings(measured)[0].passed


# --- mutation check -------------------------------------------------------


def test_suite_has_teeth():
    """If the tolerance is widened to absurdity, the failing case must stop failing.

    This asserts the assertions are load-bearing: a gate that passes everything is
    detected here rather than in front of a judge.
    """
    bad = {"integrated_lufs": -31.0}
    assert not m.loudness_findings(bad)[0].passed

    original = m.EBU_R128_TOLERANCE_LU
    try:
        m.EBU_R128_TOLERANCE_LU = 100.0
        assert m.loudness_findings(bad)[0].passed, (
            "widening tolerance did not change the verdict, so the check is not "
            "actually comparing against the tolerance"
        )
    finally:
        m.EBU_R128_TOLERANCE_LU = original

    assert not m.loudness_findings(bad)[0].passed


def test_remediation_lands_near_target_not_just_different(tmp_path):
    """Regression: single-pass loudnorm ran in dynamic mode and moved a -24.3 LUFS
    file to -25.3, i.e. further from spec while still 'changing' the number. The
    old assertion (after > before) passed anyway. This one requires landing.
    """
    # Wide dynamic range with quiet passages is what pushes single-pass loudnorm
    # into dynamic mode. A constant tone does not reproduce the bug.
    src = tmp_path / "slightly_off.m4a"
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-v", "error", "-y",
         "-f", "lavfi", "-i",
         "anoisesrc=color=pink:duration=40,volume='if(lt(mod(t,8),5),0.05,1.0)':eval=frame",
         "-af", "loudnorm=I=-24.3:TP=-2:LRA=11", "-c:a", "aac", str(src)],
        check=True, capture_output=True,
    )
    before = m.measure_loudness(src)["integrated_lufs"]
    fixed = m.remediate_loudness(src, tmp_path / "landed.m4a")
    after = m.measure_loudness(fixed)["integrated_lufs"]

    assert abs(after - (-23.0)) < abs(before - (-23.0)), (
        f"remediation moved away from target: {before} -> {after}"
    )
    assert abs(after - (-23.0)) <= 1.0, f"remediation did not land in spec: {after}"


def test_reading_speed_finding_names_the_failing_cues_and_shrinks_after_repair():
    """The check sheet must show which cues failed, not only a percentage."""
    srt = (
        "1\n00:00:01,000 --> 00:00:02,000\n"
        "This single cue crams far too many characters into one short second.\n\n"
        "2\n00:00:03,000 --> 00:00:09,000\nShort and calm.\n\n"
        "3\n00:00:10,000 --> 00:00:11,000\n"
        "Another dense line no human reader could finish inside this window.\n"
    )
    before = m.subtitle_findings(m.measure_subtitles(srt))[0]
    assert [o["cue"] for o in before.offenders] == [1, 3]
    assert before.offenders[0]["cps"] > m.NETFLIX_MAX_CPS
    assert before.offenders[0]["text"]
    # A judge needs the moment, not just the ordinal. Timecode is SRT-shaped and
    # matches the cue the offender names.
    first = before.offenders[0]
    assert first["timecode"] == "00:00:01,000 --> 00:00:02,000"
    assert first["start"] == 1.0 and first["end"] == 2.0
    assert all(
        re.fullmatch(r"\d{2}:\d{2}:\d{2},\d{3} --> \d{2}:\d{2}:\d{2},\d{3}",
                     o["timecode"])
        for o in before.offenders
    )

    fixed, _ = m.remediate_subtitles(srt)
    after = m.subtitle_findings(m.measure_subtitles(fixed))[0]
    # Cue 3 has room to extend and is cleared; cue 1 is boxed in and stays over.
    assert len(after.offenders) < len(before.offenders)
    assert [o["cue"] for o in after.offenders] == [1]
    assert after.offenders[0]["timecode"].startswith("00:00:01,000 -->")
