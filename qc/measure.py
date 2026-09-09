"""Deterministic QC measurement over a media file.

Every function here shells out to ffmpeg/ffprobe and parses real output. No model
is involved at this layer, by design: the numbers a judge reproduces must come from
the same tool the judge would run.

Specs encoded:
  EBU R128     integrated loudness target -23 LUFS, tolerance +/-1.0 LU
  ATSC A/85    (CALM Act) target -24 LKFS, tolerance +/-2.0 LU
  True peak    <= -1.0 dBTP (EBU R128 / most streamer delivery specs)
  Netflix TTSS subtitle reading speed <= 17 chars/sec (adult),
               min cue duration 5/6 s, max 42 chars/line, max 2 lines
"""

from __future__ import annotations

import json
import math
import re
import subprocess
from dataclasses import dataclass, field, asdict
from pathlib import Path

# --- spec constants -------------------------------------------------------

EBU_R128_TARGET_LUFS = -23.0
EBU_R128_TOLERANCE_LU = 1.0
ATSC_A85_TARGET_LKFS = -24.0
ATSC_A85_TOLERANCE_LU = 2.0
TRUE_PEAK_CEILING_DBTP = -1.0

NETFLIX_MAX_CPS = 17.0
NETFLIX_MIN_CUE_SECONDS = 5 / 6
NETFLIX_MAX_LINE_CHARS = 42
NETFLIX_MAX_LINES = 2


class ToolMissing(RuntimeError):
    """ffmpeg or ffprobe is not installed."""


def _run(cmd: list[str], timeout: int = 900) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError as exc:  # pragma: no cover - environment dependent
        raise ToolMissing(f"{cmd[0]} not found on PATH") from exc


@dataclass
class Finding:
    """One spec violation, or one confirmation of compliance."""

    check: str
    spec: str
    measured: float | None
    target: float | None
    unit: str
    passed: bool
    detail: str = ""
    auto_fixable: bool = False
    # The specific items that failed, e.g. per-cue reading-speed offenders.
    # Empty means nothing failed this check.
    offenders: list[dict] = field(default_factory=list)

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class QCReport:
    source: str
    duration_seconds: float | None = None
    findings: list[Finding] = field(default_factory=list)

    @property
    def failures(self) -> list[Finding]:
        return [f for f in self.findings if not f.passed]

    @property
    def passed(self) -> bool:
        return not self.failures

    def as_dict(self) -> dict:
        return {
            "source": self.source,
            "duration_seconds": self.duration_seconds,
            "passed": self.passed,
            "findings": [f.as_dict() for f in self.findings],
        }


# --- probing --------------------------------------------------------------


def probe(path: str | Path) -> dict:
    """ffprobe container/stream metadata as a dict."""
    out = _run(
        [
            "ffprobe", "-v", "error",
            "-show_format", "-show_streams",
            "-of", "json", str(path),
        ],
        timeout=120,
    )
    if out.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {out.stderr.strip()[:400]}")
    return json.loads(out.stdout)


def duration_of(path: str | Path) -> float | None:
    try:
        meta = probe(path)
    except Exception:
        return None
    try:
        return float(meta["format"]["duration"])
    except (KeyError, TypeError, ValueError):
        return None


def has_audio(path: str | Path) -> bool:
    try:
        return any(s.get("codec_type") == "audio" for s in probe(path).get("streams", []))
    except Exception:
        return False


# --- loudness -------------------------------------------------------------

_LOUDNESS_SUMMARY = re.compile(
    r"Integrated loudness:\s*\n\s*I:\s*(-?[\d.]+|-inf)\s*LUFS", re.MULTILINE
)
_TRUE_PEAK = re.compile(r"True peak:\s*\n\s*Peak:\s*(-?[\d.]+|-inf)\s*dBFS", re.MULTILINE)
_LRA = re.compile(r"LRA:\s*(-?[\d.]+)\s*LU", re.MULTILINE)


def measure_loudness(path: str | Path, seconds: int | None = None) -> dict:
    """Run the ffmpeg ebur128 scanner and return integrated loudness, LRA, true peak.

    `seconds` bounds the analysis window; a full feature takes minutes, and the UI
    states the window explicitly rather than implying a full scan.
    """
    cmd = ["ffmpeg", "-hide_banner", "-nostats", "-i", str(path)]
    if seconds:
        cmd += ["-t", str(seconds)]
    cmd += ["-af", "ebur128=peak=true", "-f", "null", "-"]
    out = _run(cmd)
    text = out.stderr

    def _grab(rx, default=None):
        m = rx.search(text)
        if not m:
            return default
        raw = m.group(1)
        return float("-inf") if raw == "-inf" else float(raw)

    integrated = _grab(_LOUDNESS_SUMMARY)
    if integrated is None:
        raise RuntimeError(f"ebur128 produced no summary: {text.strip()[-400:]}")

    return {
        "integrated_lufs": integrated,
        "true_peak_dbfs": _grab(_TRUE_PEAK),
        "lra_lu": _grab(_LRA),
        "window_seconds": seconds,
    }


def loudness_findings(measured: dict) -> list[Finding]:
    findings: list[Finding] = []
    i = measured["integrated_lufs"]

    delta_ebu = abs(i - EBU_R128_TARGET_LUFS)
    findings.append(
        Finding(
            check="integrated_loudness_ebu_r128",
            spec="EBU R128",
            measured=i,
            target=EBU_R128_TARGET_LUFS,
            unit="LUFS",
            passed=delta_ebu <= EBU_R128_TOLERANCE_LU,
            detail=f"{delta_ebu:.1f} LU from target (tolerance {EBU_R128_TOLERANCE_LU} LU)",
            auto_fixable=True,
        )
    )

    delta_atsc = abs(i - ATSC_A85_TARGET_LKFS)
    findings.append(
        Finding(
            check="integrated_loudness_atsc_a85",
            spec="ATSC A/85 (CALM Act)",
            measured=i,
            target=ATSC_A85_TARGET_LKFS,
            unit="LKFS",
            passed=delta_atsc <= ATSC_A85_TOLERANCE_LU,
            detail=f"{delta_atsc:.1f} LU from target (tolerance {ATSC_A85_TOLERANCE_LU} LU)",
            auto_fixable=True,
        )
    )

    tp = measured.get("true_peak_dbfs")
    if tp is not None:
        findings.append(
            Finding(
                check="true_peak",
                spec="EBU R128 true peak ceiling",
                measured=tp,
                target=TRUE_PEAK_CEILING_DBTP,
                unit="dBTP",
                passed=tp <= TRUE_PEAK_CEILING_DBTP,
                detail=f"peak {tp} dBTP against ceiling {TRUE_PEAK_CEILING_DBTP} dBTP",
                auto_fixable=True,
            )
        )
    return findings


# --- structural video defects --------------------------------------------

_BLACK = re.compile(r"black_start:([\d.]+)\s+black_end:([\d.]+)\s+black_duration:([\d.]+)")
_FREEZE = re.compile(r"freeze_start:\s*([\d.]+)")
_SILENCE = re.compile(r"silence_start:\s*(-?[\d.]+)")


def measure_structural(path: str | Path, seconds: int | None = None,
                       min_black: float = 1.0) -> dict:
    """Detect black frames, frozen frames and silence."""
    cmd = ["ffmpeg", "-hide_banner", "-nostats", "-i", str(path)]
    if seconds:
        cmd += ["-t", str(seconds)]
    cmd += [
        "-vf", f"blackdetect=d={min_black}:pix_th=0.10,freezedetect=n=-60dB:d=2",
        "-af", "silencedetect=n=-50dB:d=2",
        "-f", "null", "-",
    ]
    text = _run(cmd).stderr

    blacks = [
        {"start": float(a), "end": float(b), "duration": float(c)}
        for a, b, c in _BLACK.findall(text)
    ]
    return {
        "black_segments": blacks,
        "freeze_events": [float(x) for x in _FREEZE.findall(text)],
        "silence_events": [float(x) for x in _SILENCE.findall(text)],
        "window_seconds": seconds,
    }


def structural_findings(measured: dict, max_black_seconds: float = 2.0) -> list[Finding]:
    long_blacks = [b for b in measured["black_segments"] if b["duration"] >= max_black_seconds]
    findings = [
        Finding(
            check="black_frames",
            spec="delivery spec: no black segment >= %.0fs" % max_black_seconds,
            measured=float(len(long_blacks)),
            target=0.0,
            unit="segments",
            passed=not long_blacks,
            detail="; ".join(
                f"{b['start']:.1f}s-{b['end']:.1f}s ({b['duration']:.1f}s)" for b in long_blacks[:5]
            ) or "no black segment over threshold",
            auto_fixable=False,
        ),
        Finding(
            check="frozen_frames",
            spec="delivery spec: no frozen video",
            measured=float(len(measured["freeze_events"])),
            target=0.0,
            unit="events",
            passed=not measured["freeze_events"],
            detail=f"{len(measured['freeze_events'])} freeze events",
            auto_fixable=False,
        ),
    ]
    return findings


# --- subtitles ------------------------------------------------------------

_TS = re.compile(r"(\d+):(\d+):(\d+)[,.](\d+)\s*-->\s*(\d+):(\d+):(\d+)[,.](\d+)")


def _seconds(h: str, m: str, s: str, ms: str) -> float:
    # SRT milliseconds are conventionally 3 digits but real-world files (including
    # archive.org ASR output) emit 2. Normalise on digit count rather than assuming.
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / (10 ** len(ms))


def parse_srt(text: str) -> list[dict]:
    cues = []
    for block in re.split(r"\n\s*\n", text.strip()):
        lines = [ln for ln in block.strip().split("\n") if ln.strip()]
        if len(lines) < 2:
            continue
        ts_line = next((ln for ln in lines if _TS.search(ln)), None)
        if not ts_line:
            continue
        m = _TS.search(ts_line)
        start = _seconds(*m.groups()[:4])
        end = _seconds(*m.groups()[4:])
        body = [ln for ln in lines[lines.index(ts_line) + 1:]]
        if not body:
            continue
        cues.append({
            "start": start,
            "end": end,
            "duration": max(end - start, 0.0),
            "lines": body,
            "text": " ".join(body),
        })
    return cues


def measure_subtitles(text: str) -> dict:
    cues = parse_srt(text)
    over_cps, short, long_lines, too_many_lines = [], [], [], []

    for idx, c in enumerate(cues, start=1):
        chars = len(c["text"].strip())
        # Where the defect sits on the timeline, so a reviewer can scrub to it
        # instead of counting cues. Same parse, rendered back as SRT timecode.
        when = {
            "start": round(c["start"], 3),
            "end": round(c["end"], 3),
            "timecode": f"{_fmt_ts(c['start'])} --> {_fmt_ts(c['end'])}",
        }
        if c["duration"] > 0:
            cps = chars / c["duration"]
            # A near-zero duration produces an absurd cps; that is itself the defect,
            # reported as a minimum-duration failure rather than a reading-speed one.
            if cps > NETFLIX_MAX_CPS and c["duration"] >= NETFLIX_MIN_CUE_SECONDS:
                over_cps.append({"cue": idx, "cps": round(cps, 1),
                                 "text": c["text"][:80], **when})
        if c["duration"] < NETFLIX_MIN_CUE_SECONDS:
            short.append({"cue": idx, "duration": round(c["duration"], 3), **when})
        if any(len(ln) > NETFLIX_MAX_LINE_CHARS for ln in c["lines"]):
            long_lines.append({"cue": idx,
                               "longest": max(len(ln) for ln in c["lines"]), **when})
        if len(c["lines"]) > NETFLIX_MAX_LINES:
            too_many_lines.append({"cue": idx, "lines": len(c["lines"]), **when})

    return {
        "cue_count": len(cues),
        "over_reading_speed": over_cps,
        "under_min_duration": short,
        "over_line_length": long_lines,
        "over_line_count": too_many_lines,
    }


def subtitle_findings(measured: dict) -> list[Finding]:
    n = measured["cue_count"] or 1
    over = len(measured["over_reading_speed"])
    return [
        Finding(
            check="subtitle_reading_speed",
            spec=f"Netflix TTSS <= {NETFLIX_MAX_CPS:.0f} chars/sec",
            measured=round(100 * over / n, 1),
            target=0.0,
            unit="% of cues",
            passed=over == 0,
            detail=f"{over} of {measured['cue_count']} cues exceed the reading-speed limit",
            auto_fixable=True,
            offenders=measured["over_reading_speed"],
        ),
        Finding(
            check="subtitle_min_duration",
            spec=f"Netflix TTSS >= {NETFLIX_MIN_CUE_SECONDS:.3f}s per cue",
            measured=float(len(measured["under_min_duration"])),
            target=0.0,
            unit="cues",
            passed=not measured["under_min_duration"],
            detail=f"{len(measured['under_min_duration'])} cues below minimum duration",
            auto_fixable=True,
            offenders=measured["under_min_duration"],
        ),
        Finding(
            check="subtitle_line_length",
            spec=f"Netflix TTSS <= {NETFLIX_MAX_LINE_CHARS} chars/line",
            measured=float(len(measured["over_line_length"])),
            target=0.0,
            unit="cues",
            passed=not measured["over_line_length"],
            detail=f"{len(measured['over_line_length'])} cues with an over-long line",
            auto_fixable=False,
            offenders=measured["over_line_length"],
        ),
    ]


# --- remediation ----------------------------------------------------------


#: The most attenuation the windowed pass will apply inside one window. Past this
#: the correction stops being a delivery fix and becomes a mastering decision, so
#: the window is escalated to a human instead of treated.
MAX_WINDOW_ATTENUATION_DB = 6.0

#: A window shorter than one momentary (400 ms) integration window is a tick, not
#: a passage. Attenuating it would be an edit nobody asked for.
MIN_WINDOW_SECONDS = 0.4


@dataclass
class RemediationResult:
    """What the loudness repair actually did, as reported by ffmpeg itself."""

    path: Path
    #: 'linear' (one constant gain, loudness range preserved exactly) or 'dynamic'
    #: (loudnorm had to compress the programme to satisfy the true-peak ceiling,
    #: which changes the mix). Straight out of loudnorm's own JSON, not our word.
    normalization_type: str | None = None
    input_lra: float | None = None
    output_lra: float | None = None
    #: Windows the pass considered, each carrying the gain applied and why.
    windows: list[dict] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "normalization_type": self.normalization_type,
            "input_lra": self.input_lra,
            "output_lra": self.output_lra,
            "windows": self.windows,
        }


def plan_window_gains(windows: list[dict],
                      ceiling_dbtp: float = TRUE_PEAK_CEILING_DBTP) -> list[dict]:
    """Decide what, if anything, may be done inside each located window.

    This is the honesty gate on the whole windowed idea, so the rules sit here in
    one readable place rather than spread through an ffmpeg command line.

    The tool only ever ATTENUATES. It never raises a quiet passage. That is not a
    technical limitation, it is the line the feature refuses to cross: a 100 ms
    loudness series cannot tell a whispered line after an explosion from a
    mastering mistake, and a machine that lifts the quiet parts of somebody's mix
    by 6 dB has not repaired a delivery defect, it has re-mixed the film. A
    passage sitting below target is therefore reported and left alone.

    What it does act on is a true-peak over. That one is unambiguous: the sample
    peak sits above the ceiling the delivery spec names, and no reading of the
    creative intent makes an over legal. Pulling only those passages down lets the
    global normalise afterwards run as a single constant gain instead of as a
    compressor, which is the difference the mixer actually cares about.

    Returns the same windows with `gain_db`, `treated` and `reason` filled in.
    """
    out: list[dict] = []
    for w in windows:
        start = float(w.get("start_s", 0.0))
        end = float(w.get("end_s", 0.0))
        measured = w.get("measured")
        decided = dict(w)
        decided["gain_db"] = 0.0
        decided["treated"] = False

        if measured is None:
            decided["reason"] = "no measured value on this window, nothing to act on"
        elif w.get("metric") != "true_peak":
            decided["reason"] = (
                f"{w.get('metric')} windows are reported, never treated: only a "
                "true-peak over is unambiguously a defect rather than a choice"
            )
        elif end - start < MIN_WINDOW_SECONDS:
            decided["reason"] = (
                f"{end - start:.2f}s is shorter than one 400ms momentary window, "
                "a single tick is not a passage"
            )
        elif float(measured) <= ceiling_dbtp:
            decided["reason"] = f"already at or under the {ceiling_dbtp} dBTP ceiling"
        else:
            gain = ceiling_dbtp - float(measured)   # negative by construction here
            if gain < -MAX_WINDOW_ATTENUATION_DB:
                decided["reason"] = (
                    f"needs {abs(gain):.1f} dB of attenuation, past the "
                    f"{MAX_WINDOW_ATTENUATION_DB:.0f} dB cap; that is a mastering "
                    "decision, not a delivery fix"
                )
            else:
                decided["gain_db"] = round(gain, 2)
                decided["treated"] = True
                decided["reason"] = (
                    f"true peak {measured} dBTP over the {ceiling_dbtp} dBTP "
                    f"ceiling, attenuated {abs(gain):.1f} dB inside this passage only"
                )
        out.append(decided)
    return out


def window_filter(windows: list[dict]) -> str:
    """ffmpeg volume filters that act inside one passage each, and nowhere else."""
    parts = []
    for w in windows:
        if not w.get("treated"):
            continue
        parts.append(
            f"volume=volume={w['gain_db']}dB:"
            f"enable='between(t,{float(w['start_s']):.3f},{float(w['end_s']):.3f})'"
        )
    return ",".join(parts)


def remediate_loudness_detailed(
    src: str | Path,
    dst: str | Path,
    target_lufs: float = EBU_R128_TARGET_LUFS,
    true_peak: float = TRUE_PEAK_CEILING_DBTP,
    seconds: int | None = None,
    windows: list[dict] | None = None,
    window_ceiling_dbtp: float | None = None,
) -> RemediationResult:
    """Write a loudness-corrected copy. Deterministic: ffmpeg, no model.

    Two-pass. Single-pass loudnorm runs in dynamic mode and does NOT land on the
    target: measured here, a one-pass run moved a -24.3 LUFS file to -25.3, i.e.
    further from spec. Pass 1 measures, pass 2 applies linear correction using
    those measurements.

    `windows` is what the ClickHouse layer is for. When a passage peaks above the
    true-peak ceiling, loudnorm cannot reach the integrated target with a constant
    gain, so it drops to DYNAMIC mode and compresses the whole programme: on a
    synthesised master here that took the loudness range from 24.5 LU to 13.6 LU,
    which is somebody's mix flattened to fix somebody else's number. Attenuating
    only the offending passages first removes that constraint, so pass 2 runs
    LINEAR and the loudness range comes out unchanged. `normalization_type` in the
    result is ffmpeg's own word for which of the two happened.
    """
    src, dst = Path(src), Path(dst)
    # The threshold a WINDOW is judged against is not always the delivery ceiling.
    # A programme sitting under target has to be lifted to reach it, and a passage
    # only has to clear (ceiling - lift) today to be legal afterwards. The caller
    # computes that number; `true_peak` stays the ceiling handed to loudnorm.
    planned = plan_window_gains(
        windows or [],
        ceiling_dbtp=true_peak if window_ceiling_dbtp is None else window_ceiling_dbtp,
    )
    pre = window_filter(planned)

    # Pass 1 measures the file AS PASS 2 WILL SEE IT, so the windowed attenuation
    # is already in the chain. Measuring the untreated file and then correcting the
    # treated one hands loudnorm measurements that no longer describe its input,
    # and it quietly falls back to dynamic mode, which is the whole thing we are
    # trying to avoid.
    cmd = ["ffmpeg", "-hide_banner", "-nostats", "-i", str(src)]
    if seconds:
        cmd += ["-t", str(seconds)]
    measure_af = f"loudnorm=I={target_lufs}:TP={true_peak}:LRA=11:print_format=json"
    cmd += ["-af", f"{pre},{measure_af}" if pre else measure_af, "-f", "null", "-"]
    first = _run(cmd)
    stats = _parse_loudnorm_json(first.stderr)

    # Pass 2: apply with measured values so the filter can correct linearly.
    af = f"loudnorm=I={target_lufs}:TP={true_peak}:LRA=11:print_format=json"
    if stats:
        af += (
            f":measured_I={stats['input_i']}"
            f":measured_TP={stats['input_tp']}"
            f":measured_LRA={stats['input_lra']}"
            f":measured_thresh={stats['input_thresh']}"
            f":offset={stats['target_offset']}"
            ":linear=true"
        )

    cmd = ["ffmpeg", "-hide_banner", "-nostats", "-y", "-i", str(src)]
    if seconds:
        cmd += ["-t", str(seconds)]
    cmd += ["-af", f"{pre},{af}" if pre else af, "-c:a", "aac", "-b:a", "192k", str(dst)]
    out = _run(cmd)
    if out.returncode != 0:
        raise RuntimeError(f"loudness remediation failed: {out.stderr.strip()[:400]}")

    applied = _parse_loudnorm_json(out.stderr, full=True) or {}
    return RemediationResult(
        path=dst,
        normalization_type=applied.get("normalization_type"),
        input_lra=_maybe_float(applied.get("input_lra")),
        output_lra=_maybe_float(applied.get("output_lra")),
        windows=planned,
    )


def remediate_loudness(src: str | Path, dst: str | Path,
                       target_lufs: float = EBU_R128_TARGET_LUFS,
                       true_peak: float = TRUE_PEAK_CEILING_DBTP,
                       seconds: int | None = None,
                       windows: list[dict] | None = None) -> Path:
    """Path-returning form, kept because the pipeline and tests call it that way."""
    return remediate_loudness_detailed(
        src, dst, target_lufs=target_lufs, true_peak=true_peak,
        seconds=seconds, windows=windows,
    ).path


def _maybe_float(raw) -> float | None:
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _parse_loudnorm_json(text: str, full: bool = False) -> dict | None:
    """Pull loudnorm's measurement block out of ffmpeg's stderr."""
    start = text.rfind("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        return None
    try:
        raw = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return None

    needed = ("input_i", "input_tp", "input_lra", "input_thresh", "target_offset")
    if not all(k in raw for k in needed):
        return None
    # loudnorm reports -inf on silence; those values cannot drive a linear pass.
    if any("inf" in str(raw[k]) for k in needed):
        return None
    return raw if full else {k: raw[k] for k in needed}


def remediate_subtitles(text: str, max_cps: float = NETFLIX_MAX_CPS,
                        min_duration: float = NETFLIX_MIN_CUE_SECONDS) -> tuple[str, int]:
    """Extend cue out-times so reading speed and minimum duration are met.

    A cue is only extended into space that is actually free, so cues never overlap.
    Returns the corrected SRT and the number of cues changed.
    """
    cues = parse_srt(text)
    changed = 0

    for i, c in enumerate(cues):
        chars = len(c["text"].strip())
        needed = max(chars / max_cps if max_cps else 0.0, min_duration)
        # SRT timestamps only carry milliseconds, so a target of 5/6s (0.8333...)
        # would be written as 0.833 and land back under the minimum. Round the
        # requirement up to the next whole millisecond before comparing.
        needed = math.ceil(needed * 1000) / 1000
        if c["duration"] >= needed:
            continue
        # Do not run into the next cue; leave a 1-frame-ish gap at 24fps.
        ceiling = cues[i + 1]["start"] - 0.042 if i + 1 < len(cues) else c["start"] + needed
        new_end = min(c["start"] + needed, ceiling)
        if new_end > c["end"]:
            c["end"] = new_end
            c["duration"] = new_end - c["start"]
            changed += 1

    return _render_srt(cues), changed


def _fmt_ts(t: float) -> str:
    if t < 0:
        t = 0.0
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = int(t % 60)
    ms = int(round((t - int(t)) * 1000))
    if ms == 1000:  # rounding carry
        s, ms = s + 1, 0
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _render_srt(cues: list[dict]) -> str:
    parts = []
    for i, c in enumerate(cues, start=1):
        parts.append(f"{i}\n{_fmt_ts(c['start'])} --> {_fmt_ts(c['end'])}\n" + "\n".join(c["lines"]))
    return "\n\n".join(parts) + "\n"


# --- orchestration --------------------------------------------------------


def run_qc(path: str | Path, subtitle_text: str | None = None,
           seconds: int | None = None) -> QCReport:
    """Full deterministic QC pass over one asset."""
    report = QCReport(source=str(path), duration_seconds=duration_of(path))

    if has_audio(path):
        loud = measure_loudness(path, seconds=seconds)
        report.findings.extend(loudness_findings(loud))

    structural = measure_structural(path, seconds=seconds)
    report.findings.extend(structural_findings(structural))

    if subtitle_text:
        subs = measure_subtitles(subtitle_text)
        report.findings.extend(subtitle_findings(subs))

    return report
