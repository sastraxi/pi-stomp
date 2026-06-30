"""Graphic EQ panel — vertical bar visualization.

``GraphicEqPanel`` is the abstract base; subclasses provide band specs via
``build_band_specs()``. ``BarWidget`` renders a 4 px wide track + fill bar
per band, centered in equal-width columns, with a coloured selection handle.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Optional

from plugins.base import PluginPanel
from plugins.eq.band_spec import GraphicBandSpec
from plugins.eq.panel import paint_band_node
from uilib.box import Box
from uilib.config import Config
from uilib.misc import InputEvent, get_text_size
from uilib.widget import Widget

# ── layout constants ────────────────────────────────────────────────────────

_W = 320
_H = 240

VISIBLE_BANDS  = 10
COL_W          = _W // VISIBLE_BANDS   # 32 px per column
BAR_W          = 3                      # track + fill width (matches node diameter)

DB_LABEL_H     = 16
FREQ_LABEL_H   = 14
BAR_Y0         = DB_LABEL_H            # 16
BAR_Y1         = 192                   # bar area ends here
BAR_H          = BAR_Y1 - BAR_Y0      # 176
FREQ_LABEL_Y   = BAR_Y1 + 2            # 194 — just below bars, above chrome
WIDGET_H       = FREQ_LABEL_Y + FREQ_LABEL_H  # 208 — includes freq labels, ends at chrome

# ── colours ──────────────────────────────────────────────────────────────────

BG_BLACK        = (0, 0, 0)
TRACK_COLOR     = (40, 40, 40)
FILL_INACTIVE   = (160, 160, 160)
FILL_ACTIVE     = (240, 240, 240)
DB_LABEL_COLOR  = (200, 200, 200)
FREQ_LABEL_COLOR = (110, 110, 110)


# ── label helpers ────────────────────────────────────────────────────────────


def _fmt_freq(hz: float) -> str:
    """Format a frequency as ≤3 chars."""
    if hz >= 10_000:
        return f"{int(round(hz / 1000))}k"
    if hz >= 1_000:
        v = hz / 1000.0
        return f"{v:.3g}k"
    return f"{int(round(hz))}"


def _fmt_db(db: float) -> str:
    """Format a dB value as ≤3 chars (e.g. +6, -12, 0)."""
    v = int(round(db))
    if v == 0:
        return "0"
    return f"{v:+d}"


# ── coordinate helper ────────────────────────────────────────────────────────


def _gain_to_y(gain: float, band: GraphicBandSpec) -> int:
    """Map gain_db to a pixel row; gain_min → BAR_Y1 (bottom), gain_max → BAR_Y0 (top)."""
    span = band.gain_max - band.gain_min
    if span <= 0:
        return BAR_Y1
    norm = (gain - band.gain_min) / span
    norm = max(0.0, min(1.0, norm))
    return int(BAR_Y1 - norm * BAR_H)


# ── palette helper ──────────────────────────────────────────────────────────


def _graphic_palette(n: int) -> list[tuple[int, int, int]]:
    """Generate *n* RGB colours sweeping hue 0°→300°."""
    import colorsys

    out: list[tuple[int, int, int]] = []
    for i in range(n):
        hue = (i / max(n - 1, 1)) * 300.0 / 360.0
        r, g, b = colorsys.hsv_to_rgb(hue, 0.8, 0.9)
        out.append((int(r * 255), int(g * 255), int(b * 255)))
    return out


# ── BarWidget ────────────────────────────────────────────────────────────────


class BarWidget(Widget):
    """4 px-wide track+fill bars for graphic EQs, 10 bands visible at once.

    Each column is COL_W pixels wide. The track and fill bar are BAR_W=4 px,
    centred in the column. A coloured handle sits at the fill's top edge for
    the selected band. Bands scroll horizontally as selection moves.
    """

    def __init__(
        self,
        box: Box,
        bands: Sequence[GraphicBandSpec],
        font,
        db_font=None,
        **kwargs,
    ) -> None:
        kwargs.setdefault("bkgnd_color", BG_BLACK)
        super().__init__(box=box, **kwargs)
        self._bands = bands
        self._font = font
        self._db_font = db_font
        self._state: Optional[GraphicEqState] = None
        self._selected_band: Optional[str] = None
        self._bypassed: bool = False
        self._first_visible: int = 0

    @property
    def first_visible(self) -> int:
        return self._first_visible

    def set_first_visible(self, n: int) -> None:
        n = max(0, min(n, max(0, len(self._bands) - VISIBLE_BANDS)))
        if n != self._first_visible:
            self._first_visible = n
            self.refresh()

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
        ctx.draw_rectangle(ctx.dirty_bounds, fill=BG_BLACK)

        if self._state is None:
            return

        shade = 0.45 if self._bypassed else 1.0
        fv = self._first_visible
        visible = self._bands[fv : fv + VISIBLE_BANDS]

        # 0 dB reference line — faint grey across full width
        zero_y = _gain_to_y(0.0, visible[0]) if visible else BAR_Y0
        ctx.draw_line(
            [(ctx.dirty_bounds.x0, zero_y), (ctx.dirty_bounds.x1 - 1, zero_y)],
            fill=(60, 60, 60),
            width=1,
        )

        for col, band in enumerate(visible):
            cx = col * COL_W + COL_W // 2
            bar_x = cx - BAR_W // 2

            # Track — full height
            ctx.draw_rectangle(Box(bar_x, BAR_Y0, bar_x + BAR_W, BAR_Y1), fill=TRACK_COLOR)

            p = self._state.bands.get(band.name)
            if p is None:
                continue

            gain = p.gain_db if p.enabled else band.gain_min
            gain_y = _gain_to_y(gain, band)

            is_sel = band.name == self._selected_band

            fill_color: tuple[int, int, int] = FILL_ACTIVE if is_sel else FILL_INACTIVE
            if shade < 1.0:
                fill_color = tuple(int(c * shade) for c in fill_color)  # type: ignore[assignment]

            # Fill — bottom to gain position
            if gain_y < BAR_Y1:
                ctx.draw_rectangle(Box(bar_x, gain_y, bar_x + BAR_W, BAR_Y1), fill=fill_color)

            # Node — same circle style as parametric EQ
            node_color: tuple[int, int, int] = band.color
            if shade < 1.0:
                node_color = tuple(int(c * shade) for c in node_color)  # type: ignore[assignment]
            paint_band_node(ctx, cx, gain_y, node_color, is_sel)

            # Frequency label — below bars, above chrome
            if self._font is not None:
                label = _fmt_freq(band.freq_hz)
                tw, th = get_text_size(label, self._font)
                tx = cx - tw // 2
                ctx.draw_text((tx, FREQ_LABEL_Y), label, fill=FREQ_LABEL_COLOR, font=self._font)

            # dB label — top of column for selected band only, smaller font
            if is_sel:
                db_font = self._db_font if self._db_font is not None else self._font
                db_str = _fmt_db(p.gain_db if p.enabled else 0.0)
                tw, th = get_text_size(db_str, db_font)
                tx = cx - tw // 2
                ctx.draw_text((tx, 0), db_str, fill=DB_LABEL_COLOR, font=db_font)


# ── GraphicEqState ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class GraphicBandParams:
    enabled: bool
    gain_db: float = 0.0


@dataclass(frozen=True)
class GraphicEqState:
    """State for graphic EQ panels — gain per band."""

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
        db_font = cfg.get_font("small") or cfg.get_font("default")

        self._bar_widget = BarWidget(
            box=Box.xywh(0, 0, _W, WIDGET_H),
            bands=self.bands,
            font=font,
            db_font=db_font,
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

    def _select_widget_ref(self, w):  # type: ignore[override]
        super()._select_widget_ref(w)
        if isinstance(w, GraphicBandSelectable):
            band_name = w.band.name
            self._bar_widget.set_selected(band_name)
            # Scroll to keep selected band in view
            idx = next((i for i, b in enumerate(self.bands) if b.name == band_name), 0)
            fv = self._bar_widget.first_visible
            if idx < fv:
                self._bar_widget.set_first_visible(idx)
            elif idx >= fv + VISIBLE_BANDS:
                self._bar_widget.set_first_visible(idx - VISIBLE_BANDS + 1)
        else:
            self._bar_widget.set_selected(None)

    # ── band-selectable callbacks ───────────────────────────────────────────

    def _on_band_long(self, band: GraphicBandSpec) -> None:
        snap = self.plugin.pedalboard_snapshot
        if band.gain_sym in snap and not self._is_symbol_locked(self.plugin.instance_id, band.gain_sym):
            self.set_param(band.gain_sym, snap[band.gain_sym])
        self.apply_state(self.snapshot_state())
