"""Gemini must be load-bearing, not decorative.

The Build Week postmortem: "If OPENAI_API_KEY unset makes the demo identical, the
model is decorative and criterion one is lost." These tests assert the opposite
property for this project: strip the credentials and the pipeline REFUSES to plan
repairs, rather than quietly substituting a hand-written plan.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent import pipeline as p  # noqa: E402
from qc import measure as m  # noqa: E402


def _failing_report() -> m.QCReport:
    r = m.QCReport(source="synthetic")
    r.findings.append(
        m.Finding(
            check="integrated_loudness_ebu_r128", spec="EBU R128",
            measured=-31.0, target=-23.0, unit="LUFS",
            passed=False, detail="8 LU under", auto_fixable=True,
        )
    )
    return r


def test_classify_refuses_without_model(monkeypatch):
    """No credentials must be a hard stop, never a silent downgrade."""
    monkeypatch.setattr(p, "_gemini", lambda: None)
    with pytest.raises(p.GeminiRequired):
        p.classify(_failing_report())


def test_clean_report_needs_no_model(monkeypatch):
    """A passing asset short-circuits before the model: nothing to plan."""
    monkeypatch.setattr(p, "_gemini", lambda: None)
    clean = m.QCReport(source="synthetic")
    clean.findings.append(
        m.Finding(check="integrated_loudness_ebu_r128", spec="EBU R128",
                  measured=-23.0, target=-23.0, unit="LUFS", passed=True)
    )
    plan = p.classify(clean)
    assert plan["repairs"] == []


def test_model_cannot_invent_unsupported_actions(monkeypatch):
    """The executor only performs actions it implements; the rest is dropped."""

    class FakeResp:
        text = (
            '{"repairs": ['
            '{"check": "integrated_loudness_ebu_r128", "action": "loudness_normalise", "rationale": "x"},'
            '{"check": "integrated_loudness_ebu_r128", "action": "rewrite_dialogue", "rationale": "hallucinated"}'
            '], "blocking": [], "operator_note": "n"}'
        )

    class FakeModels:
        def generate_content(self, **kw):
            return FakeResp()

    class FakeClient:
        models = FakeModels()

    monkeypatch.setattr(p, "_gemini", lambda: FakeClient())
    plan = p.classify(_failing_report())
    actions = {r["action"] for r in plan["repairs"]}
    assert actions == {"loudness_normalise"}, actions


def test_model_cannot_mark_manual_defect_as_auto_fixable(monkeypatch):
    """Black frames are never auto-repaired, whatever the model claims."""

    class FakeResp:
        text = (
            '{"repairs": [{"check": "black_frames", "action": "loudness_normalise",'
            ' "rationale": "wrong"}], "blocking": [], "operator_note": "n"}'
        )

    class FakeModels:
        def generate_content(self, **kw):
            return FakeResp()

    class FakeClient:
        models = FakeModels()

    monkeypatch.setattr(p, "_gemini", lambda: FakeClient())
    report = _failing_report()
    report.findings.append(
        m.Finding(check="black_frames", spec="delivery", measured=3.0, target=0.0,
                  unit="segments", passed=False, detail="", auto_fixable=False)
    )
    plan = p.classify(report)
    assert all(r["check"] != "black_frames" for r in plan["repairs"])
