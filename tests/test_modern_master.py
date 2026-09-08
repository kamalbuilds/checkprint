"""The tool must work on a MODERN delivery master, not only on archive films.

The adversarial judge review raised this as a credibility gap: the demo runs on
1930s-60s public-domain films while claiming to serve people who ship modern
masters. The measurement code is format-agnostic, but "should be" is not evidence.

These tests build a file in the shape of a real modern delivery master
(1920x1080, 5.1 surround, 48 kHz, high bitrate) and assert the full
measure -> repair -> re-verify loop works on it.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qc import measure as m  # noqa: E402


def _build_master(path: Path, target_lufs: float, seconds: int = 20) -> Path:
    """A 1080p / 5.1 / 48 kHz master, mastered to a chosen loudness."""
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-v", "error", "-y",
         "-f", "lavfi", "-i", f"anoisesrc=color=pink:duration={seconds}:sample_rate=48000",
         "-f", "lavfi", "-i", f"testsrc2=s=1920x1080:r=25:d={seconds}",
         "-filter_complex",
         "[0:a]pan=5.1|c0=c0|c1=c0|c2=c0|c3=0.3*c0|c4=0.5*c0|c5=0.5*c0,"
         f"aresample=48000,loudnorm=I={target_lufs}:TP=-3:LRA=11,aresample=48000[a]",
         "-map", "[a]", "-map", "1:v", "-ar", "48000",
         "-c:a", "aac", "-b:a", "448k",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-b:v", "8M",
         str(path)],
        check=True, capture_output=True, timeout=600,
    )
    return path


@pytest.fixture(scope="module")
def out_of_spec_master(tmp_path_factory) -> Path:
    return _build_master(tmp_path_factory.mktemp("modern") / "quiet_master.mp4", -27.0)


@pytest.fixture(scope="module")
def in_spec_master(tmp_path_factory) -> Path:
    return _build_master(tmp_path_factory.mktemp("modern") / "good_master.mp4", -23.0)


def test_modern_master_has_broadcast_shape(out_of_spec_master):
    """Guard the fixture itself: if it is not 5.1/48k/1080p, it proves nothing."""
    meta = m.probe(out_of_spec_master)
    audio = next(s for s in meta["streams"] if s["codec_type"] == "audio")
    video = next(s for s in meta["streams"] if s["codec_type"] == "video")

    assert audio["channels"] == 6, "expected 5.1 surround"
    assert int(audio["sample_rate"]) == 48000, "expected 48 kHz broadcast rate"
    assert (video["width"], video["height"]) == (1920, 1080)


def test_out_of_spec_modern_master_fails(out_of_spec_master):
    measured = m.measure_loudness(out_of_spec_master)
    finding = m.loudness_findings(measured)[0]
    assert not finding.passed, measured
    assert measured["integrated_lufs"] < -24.0


def test_in_spec_modern_master_passes(in_spec_master):
    """The gate must not simply flag every modern file it is shown."""
    measured = m.measure_loudness(in_spec_master)
    assert m.loudness_findings(measured)[0].passed, measured


def test_repair_lands_a_modern_master_in_spec(tmp_path, out_of_spec_master):
    before = m.measure_loudness(out_of_spec_master)["integrated_lufs"]
    fixed = m.remediate_loudness(out_of_spec_master, tmp_path / "repaired.m4a")
    after = m.measure_loudness(fixed)["integrated_lufs"]

    assert not m.loudness_findings({"integrated_lufs": before})[0].passed
    assert m.loudness_findings({"integrated_lufs": after})[0].passed, (
        f"repair did not land a modern master in spec: {before} -> {after}"
    )


def test_surround_channels_survive_repair(tmp_path, out_of_spec_master):
    """A repair that silently downmixes 5.1 to stereo would destroy the master."""
    fixed = m.remediate_loudness(out_of_spec_master, tmp_path / "repaired_ch.m4a")
    audio = next(s for s in m.probe(fixed)["streams"] if s["codec_type"] == "audio")
    assert audio["channels"] == 6, (
        f"repair collapsed 5.1 to {audio['channels']} channels; a delivery master "
        "must keep its channel layout"
    )
