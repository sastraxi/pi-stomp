"""Full-screen EQ panel for fil4 / x42-eq.

V1: static rendering only — black background, dim log-frequency grid,
0 dB centre line, per-band coloured nodes on the response curve, top-line
readout, and three chrome buttons (Bypass / Back / Reset). Input wiring
and live updates land in a follow-up step.
"""

from __future__ import annotations

import math
from typing import Callable, Optional

import numpy as np
from PIL import ImageFont

from pistomp.eq.bands import BAND_COLORS, BANDS, Band
from pistomp.eq.curve import (
    GRAPH_FREQS,
    GRAPH_W,
    BandParams,
    CurveCache,
    EqState,
    db_to_y,
    freq_to_x,
)
from uilib.box import Box
from uilib.config import Config
from uilib.panel import Panel
from uilib.text import Button
from uilib.widget import Widget


# ── layout constants ────────────────────────────────────────────────────────

_W = 320
_H = 240

READOUT_Y0 = 0
READOUT_Y1 = 18

GRAPH_Y0 = 22
GRAPH_Y1 = 200  # inclusive bottom row used for db_to_y
GRAPH_H = GRAPH_Y1 - GRAPH_Y0

CHROME_GAP = 4
CHROME_H = 28
CHROME_Y = _H - CHROME_H - 2
CHROME_W = (_W - 4 * CHROME_GAP) // 3

DB_MAX = 18.0

# ── colours ──────────────────────────────────────────────────────────────────

BG_BLACK = (0, 0, 0)
GRID_DIM = (45, 45, 45)
GRID_0DB = (140, 140, 140)
CURVE_COLOR = (220, 220, 220)
HALO_COLOR = (255, 255, 255)
READOUT_COLOR = (200, 200, 200)
INACTIVE_SHADE = 0.45  # multiplier applied to curve when plugin bypassed


# ── grid helpers ─────────────────────────────────────────────────────────────

# Labeled major frequency lines + their dim minors
_FREQ_MAJORS_HZ = (100.0, 1000.0, 10000.0)
_FREQ_MINORS_HZ = (20.0, 40.0, 200.0, 500.0, 2000.0, 5000.0, 20000.0)
_DB_GRID = (-18.0, -12.0, -6.0, 6.0, 12.0, 18.0)  # 0 dB handled separately

_FREQ_MAJORS_X: frozenset[int] = frozenset(int(freq_to_x(f)) for f in _FREQ_MAJORS_HZ)
_FREQ_MINORS_X: frozenset[int] = frozenset(int(freq_to_x(f)) for f in _FREQ_MINORS_HZ)
_FREQ_GRID_X: frozenset[int] = _FREQ_MAJORS_X | _FREQ_MINORS_X


def _db_to_y_scalar(db: float) -> int:
    return int(db_to_y(np.array([db]), GRAPH_Y0, GRAPH_Y1, DB_MAX)[0])


_ZERO_DB_Y: int = _db_to_y_scalar(0.0)
_DB_GRID_Y: frozenset[int] = frozenset(_db_to_y_scalar(db) for db in _DB_GRID)


def bg_color(x: int, y: int) -> tuple[int, int, int]:
    """Background colour at a graph-area pixel, accounting for grid lines.
    Used by surgical erases so the grid is preserved after partial redraws.
    Coordinates are panel-relative."""
    if y == _ZERO_DB_Y:
        return GRID_0DB
    if y in _DB_GRID_Y:
        return GRID_DIM
    if x in _FREQ_GRID_X:
        return GRID_DIM
    return BG_BLACK


# ── GraphWidget ──────────────────────────────────────────────────────────────


class GraphWidget(Widget):
    """Owns the curve, grid and band nodes. Lives in the upper section of
    the panel. V1 redraws everything on `refresh()`; live surgical updates
    arrive in a later step."""

    NODE_R = 2  # inner filled circle radius (-> diameter 4)
    HALO_R = 4  # selection halo radius        (-> diameter 8)

    def __init__(self, box: Box, **kwargs) -> None:
        kwargs.setdefault("bkgnd_color", BG_BLACK)
        super().__init__(box=box, **kwargs)
        self._cache = CurveCache()
        self._state: Optional[EqState] = None
        self._selected_band: Optional[str] = None  # band name or None
        self._curve_y: Optional[np.ndarray] = None  # length GRAPH_W, pixel rows

    # ── state ────────────────────────────────────────────────────────────────

    def set_state(self, state: EqState) -> None:
        self._state = state
        curve_db = self._cache.compute(state)
        self._curve_y = db_to_y(curve_db, GRAPH_Y0, GRAPH_Y1, DB_MAX)

    def set_selected(self, band_name: Optional[str]) -> None:  # type: ignore[override]
        self._selected_band = band_name

    # ── drawing ──────────────────────────────────────────────────────────────

    def _draw_erase(self, image, draw, real_box) -> None:
        # Full-area erase + grid is handled inside _draw to keep one path.
        pass

    def _draw(self, image, draw, real_box) -> None:
        rx0, ry0 = real_box.x0, real_box.y0
        rx1, ry1 = real_box.x1, real_box.y1

        # 1) black background
        draw.rectangle([rx0, ry0, rx1 - 1, ry1 - 1], fill=BG_BLACK)

        # 2) grid — dim minors first, then majors, then 0 dB on top
        for x in _FREQ_MINORS_X:
            draw.line([(rx0 + x, ry0), (rx0 + x, ry1 - 1)], fill=GRID_DIM)
        for x in _FREQ_MAJORS_X:
            draw.line([(rx0 + x, ry0), (rx0 + x, ry1 - 1)], fill=GRID_DIM)
        for db in _DB_GRID:
            y = _db_to_y_scalar(db)
            draw.line([(rx0, ry0 + (y - GRAPH_Y0)), (rx1 - 1, ry0 + (y - GRAPH_Y0))], fill=GRID_DIM)
        draw.line(
            [(rx0, ry0 + (_ZERO_DB_Y - GRAPH_Y0)), (rx1 - 1, ry0 + (_ZERO_DB_Y - GRAPH_Y0))],
            fill=GRID_0DB,
        )

        # 3) curve — single pixel per column
        if self._curve_y is not None:
            shade = INACTIVE_SHADE if (self._state and not self._state.plugin_enabled) else 1.0
            color = tuple(int(c * shade) for c in CURVE_COLOR)
            ys = self._curve_y
            for x in range(GRAPH_W):
                y = int(ys[x])
                draw.point((rx0 + x, ry0 + (y - GRAPH_Y0)), fill=color)

        # 4) band nodes — selected one drawn last so its halo sits on top
        if self._state is not None:
            ordered: list[Band] = [b for b in BANDS if b.name != self._selected_band]
            sel = next((b for b in BANDS if b.name == self._selected_band), None)
            if sel is not None:
                ordered.append(sel)
            for band in ordered:
                self._draw_node(draw, rx0, ry0, band, band.name == self._selected_band)

    def _draw_node(self, draw, ox: int, oy: int, band: Band, selected: bool) -> None:
        assert self._state is not None
        p = self._state.bands.get(band.name)
        if p is None:
            return
        x = freq_to_x(p.freq)
        if band.gain_sym is None:
            # HP / LP — pin to 0 dB line
            y_local = _ZERO_DB_Y
        else:
            y_local = _db_to_y_scalar(p.gain_db)
        cx, cy = ox + int(x), oy + (y_local - GRAPH_Y0)

        color = BAND_COLORS[band.name]
        if not p.enabled:
            color = tuple(c * 6 // 10 for c in color)  # dim if band disabled

        r = self.NODE_R
        draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=color)

        if selected:
            hr = self.HALO_R
            draw.ellipse([cx - hr, cy - hr, cx + hr, cy + hr], outline=HALO_COLOR, width=1)


# ── ReadoutWidget ────────────────────────────────────────────────────────────


class ReadoutWidget(Widget):
    """Single-line text at the top of the panel showing current Nav target."""

    def __init__(self, box: Box, font, **kwargs) -> None:
        kwargs.setdefault("bkgnd_color", BG_BLACK)
        super().__init__(box=box, **kwargs)
        self._font = font
        self._text: str = ""

    def set_text(self, text: str) -> None:
        if text == self._text:
            return
        self._text = text
        self.refresh()

    def _draw_erase(self, image, draw, real_box) -> None:
        draw.rectangle(real_box.PIL_rect, fill=BG_BLACK)

    def _draw(self, image, draw, real_box) -> None:
        if not self._text:
            return
        draw.text((real_box.x0 + 4, real_box.y0 + 1), self._text, fill=READOUT_COLOR, font=self._font)


# ── EqPanel ──────────────────────────────────────────────────────────────────


def _format_readout(band: Optional[Band], p: Optional[BandParams]) -> str:
    if band is None or p is None:
        return ""
    parts = [band.name]
    parts.append(f"freq: {_fmt_freq(p.freq)}")
    parts.append(f"Q: {p.q:.2f}")
    if band.gain_sym is not None:
        parts.append(f"gain: {p.gain_db:+.1f} dB")
    else:
        parts.append("gain: —")
    if not p.enabled:
        parts.append("(off)")
    return "   ".join(parts)


def _fmt_freq(hz: float) -> str:
    if hz >= 1000.0:
        return f"{hz / 1000.0:.2f} kHz"
    return f"{hz:.0f} Hz"


class EqPanel(Panel):
    """Full-screen panel for editing an x42-eq instance."""

    def __init__(
        self,
        initial_state: EqState,
        on_dismiss: Callable[[], None],
        on_toggle_bypass: Callable[[], None],
        on_reset_all: Callable[[], None],
    ) -> None:
        super().__init__(box=Box.xywh(0, 0, _W, _H), auto_destroy=True)

        self._on_dismiss = on_dismiss
        self._on_toggle_bypass = on_toggle_bypass
        self._on_reset_all = on_reset_all

        btn_font = Config().get_font("default")
        readout_font = btn_font

        self._readout = ReadoutWidget(
            box=Box.xywh(0, READOUT_Y0, _W, READOUT_Y1 - READOUT_Y0),
            font=readout_font,
            parent=self,
        )

        self._graph = GraphWidget(
            box=Box.xywh(0, GRAPH_Y0, _W, GRAPH_H),
            parent=self,
        )

        self._btn_bypass = Button(
            box=Box.xywh(CHROME_GAP, CHROME_Y, CHROME_W, CHROME_H),
            text="Bypass",
            font=btn_font,
            outline_radius=4,
            parent=self,
            action=lambda *_: self._on_toggle_bypass(),
        )
        self._btn_back = Button(
            box=Box.xywh(CHROME_GAP * 2 + CHROME_W, CHROME_Y, CHROME_W, CHROME_H),
            text="Back",
            font=btn_font,
            outline_radius=4,
            parent=self,
            action=lambda *_: self._on_dismiss(),
        )
        self._btn_reset = Button(
            box=Box.xywh(CHROME_GAP * 3 + CHROME_W * 2, CHROME_Y, CHROME_W, CHROME_H),
            text="Reset",
            font=btn_font,
            outline_radius=4,
            parent=self,
            action=lambda *_: self._on_reset_all(),
        )
        # Selection cycle: bands first (one virtual selectable per band), then chrome.
        # In v1 (static panel) only the chrome is in the selection list; band
        # selection will be wired in the input step.
        self.add_sel_widget(self._btn_bypass)
        self.add_sel_widget(self._btn_back)
        self.add_sel_widget(self._btn_reset)

        # Initial paint
        self.set_state(initial_state)
        self.set_selected_band(BANDS[0].name)

    # ── state ────────────────────────────────────────────────────────────────

    def set_state(self, state: EqState) -> None:
        self._state = state
        self._graph.set_state(state)
        self._update_readout()
        self._graph.refresh()

    def set_selected_band(self, band_name: Optional[str]) -> None:
        self._graph.set_selected(band_name)
        self._update_readout()
        self._graph.refresh()

    def _update_readout(self) -> None:
        band = next((b for b in BANDS if b.name == self._graph._selected_band), None)
        p = self._state.bands.get(band.name) if (band and self._state) else None
        self._readout.set_text(_format_readout(band, p))

    # ── ticks (no-op in v1) ─────────────────────────────────────────────────

    def tick(self) -> None:
        pass
?