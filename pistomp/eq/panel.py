"""Full-screen EQ panel for fil4 / x42-eq.

Owns its own snapshot of band parameter state. The handler hands us an
initial `EqState` plus a `send_param(symbol, value)` callback (writes via
the websocket bridge — runtime only, not persisted). All in-panel edits
update local state, push the change via `send_param`, then refresh the
graph.

Selection cycle (Nav rotation): HP, LS, B1-B4, HS, LP, Bypass, Back, Reset.
Per-band Nav targets are invisible selectables — selecting one shows a
halo on the band's circle and updates the readout. Nav CLICK on a band
toggles its enable; LONG_CLICK resets the band to its pedalboard-open
snapshot. Chrome buttons fire their action callbacks normally.

Tweak1/2/3 rotation comes in via `tweak_event(idx, rotations)`; idx 1=gain,
2=freq, 3=Q on the currently-selected band. HP/LP have no gain (Tweak1
inert there). Bands disabled by their enable_sym still respond to tweaks
so the user can dial them in before re-enabling.
"""

from __future__ import annotations

import math
from dataclasses import replace
from typing import Callable, Optional

import numpy as np

from pistomp.eq.bands import BANDS, BAND_COLORS, Band, PLUGIN_ENABLE_SYM
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
from uilib.misc import InputEvent, get_text_size
from uilib.panel import Panel
from uilib.text import Button
from uilib.widget import Widget


# ── layout constants ────────────────────────────────────────────────────────

_W = 320
_H = 240

READOUT_Y0 = 0
READOUT_Y1 = 18

GRAPH_Y0 = 22
GRAPH_Y1 = 200
GRAPH_H = GRAPH_Y1 - GRAPH_Y0

# Chrome button geometry — copied from TunerPanel so the look matches.
_BTN_GAP = 2
_BTN_H = 28
_BTN_Y = _H - _BTN_H - _BTN_GAP
_BTN_W = (_W - 4 * _BTN_GAP) // 3
_BTN_BYPASS_ACTIVE_COLOR = (140, 50, 0)  # matches TunerPanel's mute-active

DB_MAX = 18.0

# ── colours ──────────────────────────────────────────────────────────────────

BG_BLACK = (0, 0, 0)
GRID_DIM = (45, 45, 45)
GRID_0DB = (140, 140, 140)
CURVE_COLOR = (220, 220, 220)
HALO_COLOR = (255, 255, 255)
READOUT_COLOR = (200, 200, 200)
INACTIVE_SHADE = 0.45


# ── grid helpers ─────────────────────────────────────────────────────────────

_FREQ_MAJORS_HZ = (100.0, 1000.0, 10000.0)
_FREQ_MINORS_HZ = (20.0, 40.0, 200.0, 500.0, 2000.0, 5000.0, 20000.0)
_DB_GRID = (-18.0, -12.0, -6.0, 6.0, 12.0, 18.0)

_FREQ_MAJORS_X: frozenset[int] = frozenset(int(freq_to_x(f)) for f in _FREQ_MAJORS_HZ)
_FREQ_MINORS_X: frozenset[int] = frozenset(int(freq_to_x(f)) for f in _FREQ_MINORS_HZ)
_FREQ_GRID_X: frozenset[int] = _FREQ_MAJORS_X | _FREQ_MINORS_X


def _db_to_y_scalar(db: float) -> int:
    return int(db_to_y(np.array([db]), GRAPH_Y0, GRAPH_Y1, DB_MAX)[0])


_ZERO_DB_Y: int = _db_to_y_scalar(0.0)
_DB_GRID_Y: frozenset[int] = frozenset(_db_to_y_scalar(db) for db in _DB_GRID)


def bg_color(x: int, y: int) -> tuple[int, int, int]:
    if y == _ZERO_DB_Y:
        return GRID_0DB
    if y in _DB_GRID_Y:
        return GRID_DIM
    if x in _FREQ_GRID_X:
        return GRID_DIM
    return BG_BLACK


# ── GraphWidget ──────────────────────────────────────────────────────────────


class GraphWidget(Widget):
    """Owns the curve, grid and band nodes. V1 redraws everything on
    `refresh()`; surgical updates are TODO."""

    NODE_R = 2
    HALO_R = 4

    def __init__(self, box: Box, **kwargs) -> None:
        kwargs.setdefault("bkgnd_color", BG_BLACK)
        super().__init__(box=box, **kwargs)
        self._cache = CurveCache()
        self._state: Optional[EqState] = None
        self._selected_band: Optional[str] = None
        self._curve_y: Optional[np.ndarray] = None
        self._bypassed: bool = False

    def set_state(self, state: EqState) -> None:
        self._state = state
        curve_db = self._cache.compute(state)
        self._curve_y = db_to_y(curve_db, GRAPH_Y0, GRAPH_Y1, DB_MAX)

    def set_selected(self, band_name: Optional[str]) -> None:  # type: ignore[override]
        self._selected_band = band_name

    def set_bypassed(self, bypassed: bool) -> None:
        self._bypassed = bypassed

    def _draw_erase(self, image, draw, real_box) -> None:
        pass

    def _draw(self, image, draw, real_box) -> None:
        rx0, ry0 = real_box.x0, real_box.y0
        rx1, ry1 = real_box.x1, real_box.y1

        draw.rectangle([rx0, ry0, rx1 - 1, ry1 - 1], fill=BG_BLACK)

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

        if self._curve_y is not None:
            shade = INACTIVE_SHADE if self._bypassed else 1.0
            color = tuple(int(c * shade) for c in CURVE_COLOR)
            ys = self._curve_y
            for x in range(GRAPH_W):
                y = int(ys[x])
                draw.point((rx0 + x, ry0 + (y - GRAPH_Y0)), fill=color)

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
            y_local = _ZERO_DB_Y
        else:
            y_local = _db_to_y_scalar(p.gain_db)
        cx, cy = ox + int(x), oy + (y_local - GRAPH_Y0)

        color = BAND_COLORS[band.name]
        if not p.enabled:
            color = tuple(c * 6 // 10 for c in color)

        r = self.NODE_R
        draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=color)

        if selected:
            hr = self.HALO_R
            draw.ellipse([cx - hr, cy - hr, cx + hr, cy + hr], outline=HALO_COLOR, width=1)


# ── ReadoutWidget ────────────────────────────────────────────────────────────


# Top-row column anchors. Left-anchored columns (name/freq/Q) place their
# left edge at the given x; the gain column is right-anchored — its right
# edge sits at `_READOUT_GAIN_RIGHT` (px from panel left), so values like
# "+18.0 dB" / "disabled" line up flush with the right side of the LCD.
_READOUT_COLS_LEFT: tuple[tuple[str, int], ...] = (
    ("name", 6),
    ("freq", 60),
    ("q", 160),
)
_READOUT_GAIN_RIGHT: int = _W - 6  # 6 px from the right edge


class ReadoutWidget(Widget):
    """Top-bar with statically-positioned name / freq / Q / gain columns.
    Each column is independently set via `set_field`; only changed columns
    re-render. Free-form text (chrome hints) uses `set_message` instead."""

    def __init__(self, box: Box, font, **kwargs) -> None:
        kwargs.setdefault("bkgnd_color", BG_BLACK)
        super().__init__(box=box, **kwargs)
        self._font = font
        self._fields: dict[str, str] = {k: "" for k, _ in _READOUT_COLS_LEFT}
        self._fields["gain"] = ""
        self._message: Optional[str] = None  # if set, replaces field layout

    def set_fields(self, name: str, freq: str, q: str, gain: str) -> None:
        new = {"name": name, "freq": freq, "q": q, "gain": gain}
        if self._message is None and new == self._fields:
            return
        self._fields = new
        self._message = None
        self.refresh()

    def set_message(self, text: str) -> None:
        if self._message == text:
            return
        self._message = text
        self.refresh()

    def _draw_erase(self, image, draw, real_box) -> None:
        draw.rectangle(real_box.PIL_rect, fill=BG_BLACK)

    def _draw(self, image, draw, real_box) -> None:
        if self._message is not None:
            draw.text((real_box.x0 + 6, real_box.y0 + 1), self._message,
                      fill=READOUT_COLOR, font=self._font)
            return
        for key, x in _READOUT_COLS_LEFT:
            text = self._fields.get(key, "")
            if text:
                draw.text((real_box.x0 + x, real_box.y0 + 1), text,
                          fill=READOUT_COLOR, font=self._font)
        gain = self._fields.get("gain", "")
        if gain:
            tw, _ = get_text_size(gain, self._font)
            x = real_box.x0 + _READOUT_GAIN_RIGHT - tw
            draw.text((x, real_box.y0 + 1), gain, fill=READOUT_COLOR, font=self._font)


# ── invisible band selectable ────────────────────────────────────────────────


class _BandSelectable(Widget):
    """Nav-cycle target with no visual presence of its own — the band's
    coloured circle on the graph is the indicator (halo when selected)."""

    def __init__(self, panel: "EqPanel", band: Band) -> None:
        super().__init__(box=Box.xywh(0, 0, 1, 1), parent=panel, visible=True)
        self._panel = panel
        self.band = band

    def set_selected(self, selected: bool) -> None:  # type: ignore[override]
        self.selected = selected
        if selected:
            self._panel._on_band_focus(self.band)

    def input_event(self, event) -> bool:  # type: ignore[override]
        if event == InputEvent.CLICK:
            self._panel._on_band_click(self.band)
            return True
        if event == InputEvent.LONG_CLICK:
            self._panel._on_band_long(self.band)
            return True
        return False

    def scroll_into_view(self) -> bool:
        return False

    def _draw(self, image, draw, real_box) -> None:
        pass

    def _draw_erase(self, image, draw, real_box) -> None:
        pass


# ── readout formatting ──────────────────────────────────────────────────────


def _fmt_freq(hz: float) -> str:
    if hz >= 1000.0:
        return f"{hz / 1000.0:.2f} kHz"
    return f"{hz:.0f} Hz"


def _band_readout_fields(band: Band, p: BandParams) -> tuple[str, str, str, str]:
    name = band.name
    freq = _fmt_freq(p.freq)
    q = f"Q {p.q:.2f}"
    if not p.enabled:
        gain = "disabled"
    elif band.gain_sym is None:
        gain = "—"
    else:
        gain = f"{p.gain_db:+.1f} dB"
    return name, freq, q, gain


# ── tweak step sizes ────────────────────────────────────────────────────────

_GAIN_STEP_DB = 0.5
_FREQ_STEP = 2.0 ** (1.0 / 12.0)  # one semitone per click
_Q_STEP = 0.05

# Speed multipliers mirror EncoderController.refresh — keep behaviour
# consistent between MIDI-bound use and panel-bound use.
_FAST_THRESHOLD = 4
_MEDIUM_THRESHOLD = 2
_FAST_MULT = 8
_MEDIUM_MULT = 4


def _speed_multiplier(rotations: int) -> int:
    n = abs(rotations)
    if n >= _FAST_THRESHOLD:
        return _FAST_MULT
    if n >= _MEDIUM_THRESHOLD:
        return _MEDIUM_MULT
    return 1


# ── EqPanel ──────────────────────────────────────────────────────────────────


class EqPanel(Panel):
    """Full-screen panel for editing an x42-eq instance.

    Callbacks supplied by the handler:
      - send_param(symbol, value): push a control-port change to mod-host.
        For boolean enable_syms, pass 0.0 / 1.0.
      - on_dismiss(): close the panel.
    """

    def __init__(
        self,
        initial_state: EqState,
        pedalboard_snapshot: EqState,
        send_param: Callable[[str, float], None],
        on_toggle_bypass: Callable[[], None],
        on_dismiss: Callable[[], None],
        bypassed: bool = False,
    ) -> None:
        super().__init__(box=Box.xywh(0, 0, _W, _H), auto_destroy=True)

        self._send_param = send_param
        self._on_toggle_bypass = on_toggle_bypass
        self._on_dismiss = on_dismiss
        # Pedalboard-saved values: target for Reset (chrome) and per-band
        # Nav-longpress reset. Captured by the handler at pedalboard load,
        # never mutated by panel edits.
        self._pedalboard_snapshot: EqState = pedalboard_snapshot
        self._state: EqState = initial_state
        self._bypassed = bypassed

        btn_font = Config().get_font("default")
        _, btn_text_h = get_text_size("Bypass", btn_font)
        btn_v_margin = max(0, (_BTN_H - btn_text_h) // 2)

        self._readout = ReadoutWidget(
            box=Box.xywh(0, READOUT_Y0, _W, READOUT_Y1 - READOUT_Y0),
            font=btn_font,
            parent=self,
        )
        self._graph = GraphWidget(
            box=Box.xywh(0, GRAPH_Y0, _W, GRAPH_H),
            parent=self,
        )

        # Band selectables first (Nav cycles bands → chrome → bands → ...)
        self._band_sels: dict[str, _BandSelectable] = {}
        for band in BANDS:
            sel = _BandSelectable(self, band)
            self._band_sels[band.name] = sel
            self.add_sel_widget(sel)

        # Chrome order: Back, Bypass, Reset (Bypass middle so it mirrors
        # TunerPanel's Mute position).
        self._btn_back = Button(
            box=Box.xywh(_BTN_GAP, _BTN_Y, _BTN_W, _BTN_H),
            text="Back",
            font=btn_font,
            v_margin=btn_v_margin,
            outline_radius=4,
            parent=self,
            action=lambda *_: self._on_dismiss(),
        )
        self._btn_bypass = Button(
            box=Box.xywh(_BTN_GAP * 2 + _BTN_W, _BTN_Y, _BTN_W, _BTN_H),
            text="Bypass",
            font=btn_font,
            v_margin=btn_v_margin,
            outline_radius=4,
            parent=self,
            action=lambda *_: self._on_toggle_bypass(),
        )
        self._btn_reset = Button(
            box=Box.xywh(_BTN_GAP * 3 + _BTN_W * 2, _BTN_Y, _BTN_W, _BTN_H),
            text="Reset",
            font=btn_font,
            v_margin=btn_v_margin,
            outline_radius=4,
            parent=self,
            action=lambda *_: self._reset_all(),
        )
        self.add_sel_widget(self._btn_back)
        self.add_sel_widget(self._btn_bypass)
        self.add_sel_widget(self._btn_reset)

        # Initial paint
        self._apply_bypass_style(self._bypassed)
        self._graph.set_state(self._state)
        self.sel_widget(self._band_sels[BANDS[0].name])  # selects first band

    # ── external state push (handler → panel) ───────────────────────────────

    def set_bypassed(self, bypassed: bool) -> None:
        """Called by the handler when the plugin's :bypass flips (so the
        button reflects state changes that originated elsewhere, e.g. a
        footswitch press while the panel is up)."""
        if self._bypassed == bypassed:
            return
        self._bypassed = bypassed
        self._apply_bypass_style(bypassed)
        self._btn_bypass.refresh()
        # Curve dimming is tied to bypass — mirror TunerPanel's mute style.
        self._graph.set_bypassed(bypassed)
        self._graph.refresh()
        self._update_readout()

    def _apply_bypass_style(self, bypassed: bool) -> None:
        self._btn_bypass.set_background(_BTN_BYPASS_ACTIVE_COLOR if bypassed else (0, 0, 0))

    # ── state helpers ───────────────────────────────────────────────────────

    @property
    def selected_band(self) -> Optional[Band]:
        if self.sel is None:
            return None
        w = self.sel_list[self.sel]
        return w.band if isinstance(w, _BandSelectable) else None

    def _replace_band(self, band: Band, **changes) -> None:
        old = self._state.bands[band.name]
        new = replace(old, **changes)
        new_bands = dict(self._state.bands)
        new_bands[band.name] = new
        self._state = replace(self._state, bands=new_bands)
        self._graph.set_state(self._state)
        self._graph.refresh()
        self._update_readout()

    def _update_readout(self) -> None:
        sel_w = self.sel_list[self.sel] if self.sel is not None else None
        if isinstance(sel_w, _BandSelectable):
            p = self._state.bands.get(sel_w.band.name)
            if p is None:
                self._readout.set_message("")
            else:
                name, freq, q, gain = _band_readout_fields(sel_w.band, p)
                self._readout.set_fields(name, freq, q, gain)
        elif sel_w is self._btn_bypass:
            self._readout.set_message("Plugin bypassed" if self._bypassed else "Bypass plugin")
        elif sel_w is self._btn_back:
            self._readout.set_message("Close EQ")
        elif sel_w is self._btn_reset:
            self._readout.set_message("Reset to pedalboard")
        else:
            self._readout.set_message("")

    # ── band-selectable callbacks ───────────────────────────────────────────

    def _on_band_focus(self, band: Band) -> None:
        self._graph.set_selected(band.name)
        self._graph.refresh()
        self._update_readout()

    def _on_band_click(self, band: Band) -> None:
        p = self._state.bands[band.name]
        new_enabled = not p.enabled
        self._send_param(band.enable_sym, 1.0 if new_enabled else 0.0)
        self._replace_band(band, enabled=new_enabled)

    def _on_band_long(self, band: Band) -> None:
        snap = self._pedalboard_snapshot.bands.get(band.name)
        if snap is None:
            return
        self._send_param(band.enable_sym, 1.0 if snap.enabled else 0.0)
        self._send_param(band.freq_sym, snap.freq)
        self._send_param(band.q_sym, snap.q)
        if band.gain_sym is not None:
            self._send_param(band.gain_sym, snap.gain_db)
        self._replace_band(band, enabled=snap.enabled, freq=snap.freq, q=snap.q, gain_db=snap.gain_db)

    # ── chrome callbacks ────────────────────────────────────────────────────

    def _reset_all(self) -> None:
        snap = self._pedalboard_snapshot
        for band in BANDS:
            p = snap.bands.get(band.name)
            if p is None:
                continue
            self._send_param(band.enable_sym, 1.0 if p.enabled else 0.0)
            self._send_param(band.freq_sym, p.freq)
            self._send_param(band.q_sym, p.q)
            if band.gain_sym is not None:
                self._send_param(band.gain_sym, p.gain_db)
        self._state = replace(snap)
        self._graph.set_state(self._state)
        self._graph.refresh()
        self._update_readout()

    # ── Tweak1/2/3 (rotation only) ──────────────────────────────────────────

    def tweak_event(self, idx: int, rotations: int) -> None:
        band = self.selected_band
        if band is None or rotations == 0:
            return
        delta = rotations * _speed_multiplier(rotations)
        p = self._state.bands[band.name]
        if idx == 1:
            if band.gain_sym is None:
                return  # HP/LP have no gain
            new_gain = _clip(p.gain_db + delta * _GAIN_STEP_DB, band.gain_min, band.gain_max)
            if new_gain == p.gain_db:
                return
            self._send_param(band.gain_sym, new_gain)
            self._replace_band(band, gain_db=new_gain)
        elif idx == 2:
            new_freq = _clip(p.freq * (_FREQ_STEP**delta), band.freq_min, band.freq_max)
            if new_freq == p.freq:
                return
            self._send_param(band.freq_sym, new_freq)
            self._replace_band(band, freq=new_freq)
        elif idx == 3:
            new_q = _clip(p.q + delta * _Q_STEP, band.q_min, band.q_max)
            if new_q == p.q:
                return
            self._send_param(band.q_sym, new_q)
            self._replace_band(band, q=new_q)

    # ── tick (no-op until live curve diffing lands) ─────────────────────────

    def tick(self) -> None:
        pass


def _clip(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))
