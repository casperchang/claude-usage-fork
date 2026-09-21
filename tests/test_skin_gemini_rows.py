"""Gemini (Google AI Pro) second-provider rows in the self-drawn skins.

Covers the adapter's reset-timestamp → minutes/hours conversion and the
opt-in / byte-identical-by-default contract: with ``gemini_available`` off, a
skin paints byte-for-byte identically whether or not the Gemini value fields
are populated. Mirrors ``test_skin_codex_rows`` for the second provider.
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from dataclasses import replace
from types import SimpleNamespace

import pytest

from claude_usage.skins import SKIN_MODULES
from claude_usage.skins._adapter import SkinData, from_usage_stats

# A single QApplication for the whole module (QPainter needs one).
from PySide6.QtWidgets import QApplication

_app = QApplication.instance() or QApplication([])


class TestGeminiAdapter:
    def test_reset_timestamps_convert_to_minutes_and_hours(self):
        now = 1_000_000
        stats = SimpleNamespace(
            gemini_available=True,
            gemini_session_utilization=0.31,
            gemini_session_reset=now + 2 * 3600 + 30 * 60,   # 2h30m -> 150 min
            gemini_weekly_utilization=0.62,
            gemini_weekly_reset=now + 26 * 3600 + 15 * 60,   # 26h 15m
        )
        d = from_usage_stats(stats, now=now)
        assert d.gemini_available is True
        assert d.gemini_session_pct == 0.31
        assert d.gemini_session_reset_min == 150
        assert d.gemini_weekly_reset_hrs == 26
        assert d.gemini_weekly_reset_min == 15

    def test_absent_provider_leaves_gemini_fields_zeroed(self):
        d = from_usage_stats(SimpleNamespace(), now=1_000_000)
        assert d.gemini_available is False
        assert d.gemini_session_pct == 0.0
        assert d.gemini_session_reset_min == 0
        assert d.gemini_weekly_pct == 0.0

    def test_utilization_is_clamped(self):
        stats = SimpleNamespace(
            gemini_available=True,
            gemini_session_utilization=1.9,
            gemini_weekly_utilization=-0.2,
            gemini_session_reset=0, gemini_weekly_reset=0,
        )
        d = from_usage_stats(stats, now=1_000_000)
        assert d.gemini_session_pct == 1.0
        assert d.gemini_weekly_pct == 0.0


def _render_bytes(mod, data: SkinData, height: int) -> bytes:
    from PySide6.QtCore import QRectF
    from PySide6.QtGui import QImage, QPainter

    w = int(mod.METRICS["osd_width"])
    img = QImage(w, int(height), QImage.Format_ARGB32)
    img.fill(0)
    p = QPainter(img)
    mod.paint_osd(p, QRectF(0, 0, w, int(height)), data, 1.0)
    p.end()
    return bytes(img.constBits())


@pytest.mark.parametrize("name", sorted(SKIN_MODULES))
def test_gemini_off_render_is_byte_identical(name):
    """gemini_available False must paint byte-for-byte identically whether the
    Gemini value fields are zeroed or populated — the no-default-impact contract."""
    mod = SKIN_MODULES[name]
    base_h = mod.METRICS["osd_height"]
    off_zeroed = SkinData(session_pct=0.4, weekly_pct=0.7, weekly_reset_hrs=3)
    off_populated = replace(
        off_zeroed,
        gemini_available=False,              # still OFF
        gemini_session_pct=0.61, gemini_session_reset_min=179,
        gemini_weekly_pct=0.48, gemini_weekly_reset_hrs=119, gemini_weekly_reset_min=59,
    )
    assert _render_bytes(mod, off_zeroed, base_h) == _render_bytes(mod, off_populated, base_h)


@pytest.mark.parametrize("name", sorted(SKIN_MODULES))
def test_gemini_on_renders_without_error(name):
    """With Gemini active the skin paints into its grown panel height without
    raising (the two extra rows fit in gemini_rows_height)."""
    mod = SKIN_MODULES[name]
    m = mod.METRICS
    gemini_h = m["osd_height"] + m.get("gemini_rows_height", 62)
    data = SkinData(
        session_pct=0.24, weekly_pct=0.70, weekly_reset_hrs=71, weekly_reset_min=59,
        gemini_available=True,
        gemini_session_pct=0.61, gemini_session_reset_min=179,
        gemini_weekly_pct=0.48, gemini_weekly_reset_hrs=119, gemini_weekly_reset_min=59,
    )
    # Should not raise; produces a non-empty buffer.
    assert _render_bytes(mod, data, gemini_h)


@pytest.mark.parametrize("name", sorted(SKIN_MODULES))
def test_both_providers_together_render_without_error(name):
    """Codex and Gemini compose additively — both pairs fit in the summed height."""
    mod = SKIN_MODULES[name]
    m = mod.METRICS
    both_h = (m["osd_height"]
              + m.get("codex_rows_height", 62)
              + m.get("gemini_rows_height", 62))
    data = SkinData(
        session_pct=0.24, weekly_pct=0.70, weekly_reset_hrs=71,
        codex_available=True,
        codex_session_pct=0.5, codex_session_reset_min=90,
        codex_weekly_pct=0.4, codex_weekly_reset_hrs=40,
        gemini_available=True,
        gemini_session_pct=0.61, gemini_session_reset_min=179,
        gemini_weekly_pct=0.48, gemini_weekly_reset_hrs=119, gemini_weekly_reset_min=59,
    )
    assert _render_bytes(mod, data, both_h)
