#!/usr/bin/env python3
"""Render the README screenshots for this fork, offscreen, from sample data.

Nothing here reads real usage, projects or paths, so the images are safe to
publish. Qt's ``offscreen`` platform is used: no window appears on screen.

Run from the repo root:
    uv run --with pyside6-essentials --with certifi python scripts/gen_fork_screenshots.py

Outputs: ``screenshots/fork-osd.png`` and ``screenshots/fork-popup.png``.
"""

from __future__ import annotations

import math
import os
import sys
import time

os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["QT_SCALE_FACTOR"] = "2"  # same layout, twice the pixels: crisp on GitHub

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

from PySide6.QtCore import QRectF, QTimer
from PySide6.QtGui import QColor, QImage, QLinearGradient, QPainter
from PySide6.QtWidgets import QApplication

from claude_usage.collector import UsageStats
from claude_usage.config import load_config
from claude_usage.overlay import VIEW_MODE_BARS, UsageOverlay
from claude_usage.ticker import TickerItem
from claude_usage.widget import UsagePopup

OUTPUT_DIR = os.path.join(REPO_ROOT, "screenshots")
OSD_SCALE = 1.0


def _pump(app: QApplication, ms: int = 100) -> None:
    deadline = QTimer()
    deadline.setSingleShot(True)
    deadline.start(ms)
    while deadline.isActive():
        app.processEvents()


def sample_stats() -> UsageStats:
    """Sample numbers: 43 % of the 5h session, 32 % of the week, 2d 17h to reset."""
    now = time.time()
    stats = UsageStats()
    stats.session_utilization = 0.43
    stats.session_reset = int(now + 2 * 3600 + 14 * 60)
    stats.weekly_utilization = 0.32
    stats.weekly_reset = int(now + 2 * 86400 + 17 * 3600 + 20 * 60)
    stats.session_history = [0.02, 0.05, 0.05, 0.09, 0.14, 0.2, 0.24, 0.31, 0.37, 0.43]
    stats.weekly_history = [0.0, 0.04, 0.09, 0.13, 0.13, 0.19, 0.24, 0.27, 0.32, 0.32]
    stats.ticker_items = [
        TickerItem(ts=now - i * 30, msg_id=f"demo_{i}", cost_usd=c, tool=t,
                   output_tokens=o, model="claude-opus-4-7")
        for i, (t, c, o) in enumerate([
            ("Bash", 0.034, 156), ("Read", 0.089, 412), ("Edit", 0.124, 780),
            ("Write", 0.205, 2571), ("Bash", 0.053, 208), ("Read+2", 0.287, 1840),
        ])
    ]
    return stats


def _backdrop(w: int, h: int) -> QImage:
    """A plain aubergine gradient so the card reads as a floating desktop widget."""
    img = QImage(w, h, QImage.Format.Format_ARGB32)
    p = QPainter(img)
    grad = QLinearGradient(0, 0, w, h)
    grad.setColorAt(0.0, QColor("#3a2350"))
    grad.setColorAt(1.0, QColor("#1c1226"))
    p.fillRect(QRectF(0, 0, w, h), grad)
    p.end()
    return img


def main() -> int:
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    app = QApplication([])
    cfg = load_config(os.path.join(REPO_ROOT, "config.json.example"))
    cfg = {**cfg, "osd_opacity": 1.0, "show_ticker": True, "osd_scale": OSD_SCALE,
           "osd_view_mode": VIEW_MODE_BARS}
    stats = sample_stats()

    overlay = UsageOverlay(cfg)
    overlay.update_stats(stats)
    overlay._ticker_offset = 260.0
    overlay.show()
    _pump(app)
    card = overlay.grab().toImage()
    card.setDevicePixelRatio(1.0)  # draw at full pixel size, not logical size
    overlay.close()

    pad = 56  # pixels (28 logical at 2x)
    canvas = _backdrop(card.width() + 2 * pad, card.height() + 2 * pad)
    p = QPainter(canvas)
    p.drawImage(pad, pad, card)
    p.end()
    canvas.save(os.path.join(OUTPUT_DIR, "fork-osd.png"), "PNG")

    popup = UsagePopup({**cfg, "osd_scale": 1.0})
    popup.resize(540, 400)
    popup.update_stats(stats)
    popup.show()
    _pump(app, ms=200)
    content = popup._content
    content.adjustSize()
    _pump(app, ms=100)
    shot = content.grab().toImage()
    popup.close()
    # Keep the plan-limits part; the cost sections below are not what this fork changes.
    shot.copy(0, 0, shot.width(), min(shot.height(), 670)).save(
        os.path.join(OUTPUT_DIR, "fork-popup.png"), "PNG")
    print("Saved screenshots/fork-osd.png and screenshots/fork-popup.png")
    return 0


if __name__ == "__main__":
    sys.exit(main())
