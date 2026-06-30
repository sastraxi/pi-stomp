"""Graphic EQ panel — traditional horizontal-bar visualization.

``GraphicEqPanel`` is the abstract base; subclasses provide band specs via
``build_band_specs()``. ``BarWidget`` renders the vertical bars, frequency
labels, and dB readout.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Optional

import numpy as np
import pygame

from plugins.base import PluginPanel
from plugins.eq.band_spec import GraphicBandSpec
from plugins.eq.curve import FREQ_MAX_HZ, FREQ_MIN_HZ, GRAPH_W, freq_to_x
from uilib.box import Box
from uilib.config import Config
from uilib.misc import InputEvent, get_text_size
from uilib.widget import Widget

# ── layout constants ────────────────────────────────────────────────────────

_W = 320
_H = 240

LABEL_Y0 = 0
LABEL_Y1 = 16

BAR_Y0 = 16
BAR_Y1 = 200
BAR_H = BAR_Y1 - BAR_Y0

READOUT_Y0 = 200
READOUT_Y1 = 212

DB_MAX = 18.0

# ── colours ──────────────────────────────────────────────────────────────────

BG_BLACK = (0, 0, 0)
GRID_DIM = (45, 45, 45)
GRID_0DB = (140, 140, 140)
HALO_COLOR = (255, 255, 255)
READOUT_COLOR = (200, 200, 200)
LABEL_COLOR = (110, 110, 110)
INACTIVE_ALPHA = 0.35  # multiplier for unselected bars

_DB_GRID = (-12.0, -6.0, 6.0, 12.0)


# ── palette helper ──────────────────────────────────────────────────────────


def _graphic_palette(n: int) -> list[tuple[int, int, int]]:
    """Generate *n* RGB colours sweeping hue from 0° to 300° (red→yellow→green→cyan→blue→magenta)."""
    import colorsys

    out: list[tuple[int, int, int]] = []
    for i in range(n):
        hue = (i / max(n - 1, 1)) * 300.0 / 360.0
        r, g, b = colorsys.hsv_to_rgb(hue, 0.8, 0.9)
        out.append((int(r * 255), int(g * 255), int(b * 255)))
    return out


# ── coordinate helpers ──────────────────────────────────────────────────────


def _gain_to_y(gain_db: float, y_top: int, y_bot: int, db_max: float = DB_MAX) -> int:
    """Map a dB value to a pixel row. y_top = +db_max, y_bot = -db_max."""
    norm = (db_max - max(-db_max, min(db_max, gain_db))) / (2.0 * db_max)
    return int(y_top + norm * (y_bot - y_top))


def _db_to_y_scalar(db: float) -> int:
    return _gain_to_y(db, BAR_Y0, BAR_Y1)


_ZERO_DB_Y: int = _db_to_y_scalar(0.0)
_DB_GRID_Y: frozenset[int] = frozenset(_db_to_y_scalar(db) for db in _DB_GRID)


# ── BarWidget ────────────────────────────────────────────────────────────────


class BarWidget(Widget):
    """Vertical-bar visualization for graphic EQs.

    Bars positioned by ``freq_hz`` on a log x-axis; height mapped from gain
    within [gain_min, gain_max]. Frequency labels along the top, dB readout
    along the bottom. Does NOT reuse ``GraphWidget``.
    """

    BAR_MIN_WIDTH = 4
    BAR_GAP = 2

    def __init__(
        self,
        box: Box,
        bands: Sequence[GraphicBandSpec],
        font,
        **kwargs,
    ) -> None:
        kwargs.setdefault("bkgnd_color", BG_BLACK)
        super().__init__(box=box, **kwargs)
        self._bands = bands
        self._font = font
        self._state: Optional[GraphicEqState] = None
        self._selected_band: Optional[str] = None
        self._bypassed: bool = False

        # Precompute bar x-positions and widths
        self._bar_xs: list[int] = []
        self._bar_widths: list[int] = []
        for i, band in enumerate(bands):
            x = int(freq_to_x(band.freq_hz))
            self._bar_xs.append(x)
            if i < len(bands) - 1:
                next_x = int(freq_to_x(bands[i + 1].freq_hz))
                w = max(self.BAR_MIN_WIDTH, next_x - x - self.BAR_GAP)
            else:
                w = self.BAR_MIN_WIDTH
            self._bar_widths.append(w)

        # Staggered frequency labels — show every Nth label to avoid overlap
        n_bands = len(bands)
        label_step = max(1, n_bands // 8)
        self._freq_labels: list[tuple[str, int]] = []
        for i, band in enumerate(bands):
            if i % label_step == 0:
                label = self._fmt_freq_label(band.freq_hz)
                self._freq_labels.append((label, self._bar_xs[i]))

    @staticmethod
    def _fmt_freq_label(hz: float) -> str:
        if hz >= 1000.0:
            k = hz / 1000.0
            if k >= 10.0:
                return f"{int(round(k))}k"
            return f"{k:.1f}k"
        return f"{int(round(hz))}"

    # ── state setters ──────────────────────────────────────────────────────

    def set_state(self, state: GraphicEqState) -> None:
        self._state = state
        self.refresh()

    def set_selected(self, band_name: Optional[str]) -> None:  # type: ignore[override]
        if band_name == self._selected_band:
            return
        self._selected_band = band_name
        self.refresh()

    def set_bypassed(self, bypassed: bool) -> None:
        if self._bypassed == bypassed:
            return
        self._bypassed = bypassed
        self.refresh()

    # ── paint ───────────────────────────────────────────────────────────────

    def _draw_erase(self, ctx) -> None:
        pass

    def _draw(self, ctx) -> None:
        db = ctx.dirty_bounds
        rx0, ry0 = db.x0, db.y0
        rx1, ry1 = db.x1, db.y1

        # Background
        ctx.draw_rectangle(db, fill=BG_BLACK)

        # Horizontal dB gridlines
        hx0 = max(rx0, 0)
        hx1 = min(rx1, _W)
        hy0 = max(ry0, 0)
        hy1 = min(ry1, BAR_H)
        if hx0 < hx1 and hy0 < hy1:
            for db_val in _DB_GRID:
                y = _db_to_y_scalar(db_val) - BAR_Y0
                if hy0 <= y < hy1:
                    ctx.draw_line([(hx0, y), (hx1 - 1, y)], fill=GRID_DIM, width=1)
            zero_y = _ZERO_DB_Y - BAR_Y0
            if hy0 <= zero_y < hy1:
                ctx.draw_line([(hx0, zero_y), (hx1 - 1, zero_y)], fill=GRID_0DB, width=1)

        # Bars
        if self._state is not None:
            shade = 0.45 if self._bypassed else 1.0
            for i, band in enumerate(self._bands):
                p = self._state.bands.get(band.name)
                if p is None:
                    continue
                cx = self._bar_xs[i]
                bw = self._bar_widths[i]
                if cx + bw // 2 <= rx0 or cx - bw // 2 >= rx1:
                    continue

                gain = p.gain_db if p.enabled else 0.0
                y_top = _gain_to_y(gain, BAR_Y0, BAR_Y1)
                y_bot = _ZERO_DB_Y

                bar_box = Box(cx - bw // 2, min(y_top, y_bot), cx + bw // 2 + 1, max(y_top, y_bot) + 1)

                is_selected = band.name == self._selected_band
                if is_selected:
                    color = band.color
                else:
                    color = tuple(int(c * INACTIVE_ALPHA) for c in band.color)

                if shade < 1.0:
                    color = tuple(int(c * shade) for c in color)

                ctx.draw_rectangle(bar_box, fill=color)

                if is_selected:
                    ctx.draw_rectangle(bar_box, outline=HALO_COLOR, width=1)

        # Frequency labels at top
        if self._font is not None:
            for text, fx in self._freq_labels:
                tw, th = get_text_size(text, self._font)
                tx = fx - tw // 2
                if tx + tw <= rx0 or tx >= rx1:
                    continue
                ty = 0
                ctx.draw_text((tx, ty), text, fill=LABEL_COLOR, font=self._font)

        # dB readout at bottom for selected band
        if self._state is not None and self._selected_band is not None:
            band = next((b for b in self._bands if b.name == self._selected_band), None)
            if band is not None:
                p = self._state.bands.get(band.name)
                if p is not None:
                    if p.enabled:
                        readout = f"{band.name}: {p.gain_db:+.1f} dB"
                    else:
                        readout = f"{band.name}: disabled"
                    tw, th = get_text_size(readout, self._font)
                    tx = (_W - tw) // 2
                    ty = READOUT_Y0 - BAR_Y0
                    ctx.draw_text((tx, ty), readout, fill=READOUT_COLOR, font=self._font)


# ── GraphicEqState ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class GraphicBandParams:
    enabled: bool
    gain_db: float = 0.0


@dataclass(frozen=True)
class GraphicEqState:
    """State for graphic EQ panels — gain per band, no curve."""

    plugin_enabled: bool
    bands: dict[str, GraphicBandParams]  # keyed by GraphicBandSpec.name


# ── GraphicBandSelectable ────────────────────────────────────────────────────


class GraphicBandSelectable(Widget):
    """Nav-cycle target for graphic EQ bands.

    CLICK is a no-op (returns False so the event falls through).
    LONG_CLICK resets the band to the pedalboard snapshot.
    """

    def __init__(self, panel: GraphicEqPanel, band: GraphicBandSpec) -> None:
        super().__init__(box=Box.xywh(0, 0, 1, 1), parent=panel, visible=True)
        self._panel: GraphicEqPanel = panel
        self.band = band

    def set_selected(self, selected: bool) -> None:  # type: ignore[override]
        self.selected = selected

    def input_event(self, event) -> bool:  # type: ignore[override]
        if event == InputEvent.CLICK:
            return False
        if event == InputEvent.LONG_CLICK:
            self._panel._on_band_long(self.band)
            return True
        return False

    def scroll_into_view(self) -> bool:
        return False

    def _draw(self, ctx) -> None:
        pass

    def _draw_erase(self, ctx) -> None:
        pass

    def _draw_selection(self, ctx) -> None:
        pass


# ── GraphicEqPanel (ABC) ─────────────────────────────────────────────────────


class GraphicEqPanel(PluginPanel[GraphicEqState]):
    """Abstract base for graphic EQ panels.

    Subclasses provide ``build_band_specs()`` returning the list of
    ``GraphicBandSpec`` for this plugin.
    """

    # ── subclass contract ──────────────────────────────────────────────────

    def build_band_specs(self) -> Sequence[GraphicBandSpec]:
        raise NotImplementedError

    # ── PluginPanel subclass contract ────────────────────────────────────────

    def snapshot_state(self) -> GraphicEqState:
        params = self.plugin.parameters

        def _val(symbol: str, default: float) -> float:
            p = params.get(symbol)
            return float(p.value) if p is not None and p.value is not None else default

        bands: dict[str, GraphicBandParams] = {}
        for band in self.bands:
            bands[band.name] = GraphicBandParams(
                enabled=True,
                gain_db=_val(band.gain_sym, 0.0),
            )
        return GraphicEqState(
            plugin_enabled=bool(_val("enable", 1.0)),
            bands=bands,
        )

    def apply_state(self, state: GraphicEqState) -> None:
        self._state = state
        self._bar_widget.set_state(state)
        self._update_readout()

    def build_widgets(self) -> None:
        self.bands = self.build_band_specs()
        self._state = self.snapshot_state()
        cfg = Config()
        font = cfg.get_font("tiny") or cfg.get_font("default")

        self._bar_widget = BarWidget(
            box=Box.xywh(0, BAR_Y0, _W, BAR_H),
            bands=self.bands,
            font=font,
            parent=self,
        )

        self._band_sels: dict[str, GraphicBandSelectable] = {}
        for band in self.bands:
            sel = GraphicBandSelectable(self, band)
            self._band_sels[band.name] = sel
            self.add_sel_widget(sel)

        self._bar_widget.set_bypassed(self.plugin.is_bypassed())
        self.apply_state(self.snapshot_state())
        self.sel_widget(self._band_sels[self.bands[0].name])

    def on_encoder_rotation(self, encoder_id: int, rotations: int) -> bool:
        if encoder_id not in (1, 2, 3) or rotations == 0:
            return False
        band = self.selected_band
        if band is None:
            return encoder_id != 3
        delta = rotations
        p = self._state.bands[band.name]
        if encoder_id == 1:
            new_gain = max(band.gain_min, min(band.gain_max, p.gain_db + delta * 0.5))
            if new_gain == p.gain_db:
                return True
            self.set_param(band.gain_sym, new_gain)
            self._replace_band(band, gain_db=new_gain)
            return True
        elif encoder_id in (2, 3):
            return True  # consume but no-op
        return False

    def tick(self) -> None:
        bypassed = self.plugin.is_bypassed()
        if bypassed != getattr(self, "_last_bypassed", None):
            self._last_bypassed = bypassed
            self._bar_widget.set_bypassed(bypassed)
            self._update_readout()
        super().tick()

    def _refresh_bypass_style(self) -> None:
        super()._refresh_bypass_style()
        self._bar_widget.set_bypassed(self.plugin.is_bypassed())
        self._update_readout()

    # ── state helpers ───────────────────────────────────────────────────────

    @property
    def selected_band(self) -> Optional[GraphicBandSpec]:
        if self.sel_ref is None:
            return None
        w = self.sel_ref
        return w.band if isinstance(w, GraphicBandSelectable) else None

    def _replace_band(self, band: GraphicBandSpec, **changes) -> None:
        old = self._state.bands[band.name]
        new = type(old)(**{**old.__dict__, **changes})
        new_bands = dict(self._state.bands)
        new_bands[band.name] = new
        self._state = type(self._state)(
            plugin_enabled=self._state.plugin_enabled,
            bands=new_bands,
        )
        self._bar_widget.set_state(self._state)
        self._update_readout()

    def _update_readout(self) -> None:
        sel_w = self.sel_ref
        if isinstance(sel_w, GraphicBandSelectable):
            p = self._state.bands.get(sel_w.band.name)
            if p is not None:
                self._bar_widget.set_state(self._state)
        elif sel_w is self._btn_bypass:
            pass
        elif sel_w is self._btn_back:
            pass
        elif sel_w is self._btn_reset:
            pass

    def _select_widget_ref(self, w):  # type: ignore[override]
        super()._select_widget_ref(w)
        band_name = w.band.name if isinstance(w, GraphicBandSelectable) else None
        self._bar_widget.set_selected(band_name)

    # ── band-selectable callbacks ───────────────────────────────────────────

    def _on_band_long(self, band: GraphicBandSpec) -> None:
        snap = self.plugin.pedalboard_snapshot
        if band.gain_sym in snap and not self._is_symbol_locked(self.plugin.instance_id, band.gain_sym):
            self.set_param(band.gain_sym, snap[band.gain_sym])
        self.apply_state(self.snapshot_state())
