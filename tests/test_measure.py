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


@pytest.fixture(scope="module")
def black_with_sound(tmp_path_factory) -> Path:
    """Ten seconds of genuinely black picture, with a tone over it."""
    out = tmp_path_factory.mktemp("black") / "black_with_sound.mp4"
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-v", "error", "-y",
         "-f", "lavfi", "-i", "color=c=black:s=320x240:r=25:d=10",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=10",
         "-map", "0:v", "-map", "1:a",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(out)],
        check=True, capture_output=True, timeout=300,
    )
    return out


def test_a_file_with_no_picture_never_reports_a_passing_picture_check(
    tmp_path, black_with_sound
):
    """The same content, video stripped, must not turn a picture failure into a pass.

    blackdetect and freezedetect on an asset with no video stream emit nothing and
    exit 0. Reported naively that is "0 black segments", which is written down as a
    pass: a 0 that means nobody looked, printed identically to a 0 that means
    nothing was found. This is the case the verify stage hits, because a loudness
    repair only touches audio.
    """
    stripped = tmp_path / "audio_only.m4a"
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-v", "error", "-y", "-i", str(black_with_sound),
         "-vn", "-c:a", "aac", str(stripped)],
        check=True, capture_output=True, timeout=300,
    )
    assert m.has_video(black_with_sound) is True
    assert m.has_video(stripped) is False

    with_picture = {f.check: f for f in m.run_qc(black_with_sound).findings}
    assert with_picture["black_frames"].passed is False, (
        "the fixture must genuinely fail the picture check, or this proves nothing"
    )

    report = m.run_qc(stripped)
    findings = {f.check: f for f in report.findings}
    for check in m.PICTURE_CHECKS:
        assert check in findings, f"{check} vanished instead of being marked unexamined"
        assert findings[check].not_measured is True, check
        assert findings[check].passed is False, check
        assert findings[check].measured is None, check

    assert [f.check for f in report.not_measured] == list(m.PICTURE_CHECKS)
    assert report.passed is False, "a report with an unexamined check is not a pass"
    # And an unexamined check is not a failure either, so it cannot flatter the
    # before/after delta by appearing on one side of it.
    assert not any(f.not_measured for f in report.failures)


def test_the_repaired_file_keeps_its_picture_so_verify_can_re_measure_it(
    tmp_path, black_with_sound
):
    """The repaired file carries the picture, so the after pass measures it.

    ffmpeg's default stream selection happens to pick up a video stream, which
    means this works by accident unless the mapping is explicit. It is asked for
    explicitly, and copied rather than re-encoded, so the after report covers the
    same checks as the before report at the cost of a stream copy.

    The delivered file fails black_frames. So must the repaired one: a loudness
    repair does not remove black frames, and a re-measurement that says otherwise
    is measuring something else.
    """
    repaired = m.remediate_loudness_detailed(
        black_with_sound, tmp_path / "repaired.mp4", carry_video=True
    ).path
    assert m.has_video(repaired) is True

    report = m.run_qc(repaired)
    findings = {f.check: f for f in report.findings}
    assert not report.not_measured, [f.check for f in report.not_measured]
    assert findings["black_frames"].not_measured is False
    assert findings["black_frames"].passed is False, (
        "a loudness repair cannot fix black frames, so the re-measurement must "
        "still report them"
    )
    # The picture is copied, not re-encoded. A re-encode at ffmpeg's default
    # quality is both slow on a feature and a second-generation picture, which is
    # not what anybody asked a loudness repair to produce.
    src_v = next(s for s in m.probe(black_with_sound)["streams"]
                 if s["codec_type"] == "video")
    out_v = next(s for s in m.probe(repaired)["streams"]
                 if s["codec_type"] == "video")
    assert out_v["codec_name"] == src_v["codec_name"]
    assert out_v["nb_frames"] == src_v["nb_frames"]


def test_quieting_ffmpeg_would_blind_the_detector_parser(black_with_sound):
    """ffmpeg's detectors report at INFO, and `measure_structural` parses stderr.

    blackdetect, freezedetect and silencedetect all write their findings to stderr
    at log level INFO. Add `-v error` to that command and every finding vanishes
    while the exit code stays 0, so a file with a real defect measures clean and
    the picture check can no longer fail. That is a check that cannot fail, which
    is worse than no check, so the mutation is reproduced here on a file with a
    known defect rather than left to a comment.
    """
    cmd = ["ffmpeg", "-hide_banner", "-nostats", "-i", str(black_with_sound),
           "-vf", "blackdetect=d=1:pix_th=0.10,freezedetect=n=-60dB:d=2",
           "-af", "silencedetect=n=-50dB:d=2", "-f", "null", "-"]

    quieted = subprocess.run(cmd[:2] + ["-v", "error"] + cmd[2:],
                             capture_output=True, text=True, timeout=300)
    assert quieted.returncode == 0, quieted.stderr[-400:]
    assert "black_start" not in quieted.stderr, (
        "this ffmpeg build no longer suppresses detector output at -v error, so "
        "this test no longer reproduces the bug it exists to pin"
    )

    shipped = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    assert "black_start" in shipped.stderr

    # And the shipped code path agrees with the shipped command: the defect
    # reaches a finding, not just ffmpeg's log.
    measured = m.measure_structural(black_with_sound)
    assert measured["black_segments"]
    assert not {f.check: f for f in m.structural_findings(measured)}["black_frames"].passed


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
