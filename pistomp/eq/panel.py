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
    db_to_y_float,
    freq_to_x,
)
from uilib.box import Box
from uilib.config import Config
from uilib.misc import InputEvent, get_text_size
from uilib.panel import Panel
from uilib.text import Button
from uilib.widget import Widget


# Type alias for the per-band geometry we cache for diff-paint
# (image_x, image_y, color_rgb, enabled).
_NodePos = tuple[int, int, tuple[int, int, int], bool]


# ── layout constants ────────────────────────────────────────────────────────

_W = 320
_H = 240

READOUT_Y0 = 0
READOUT_Y1 = 22

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
# Line thickness in pixels, measured PERPENDICULAR to the curve. The vertical
# extent painted at each column is CURVE_THICKNESS * sqrt(1 + slope²), so the
# perceived weight stays constant regardless of slope.
CURVE_THICKNESS = 1.3
HALO_COLOR = (255, 255, 255)
READOUT_COLOR = (200, 200, 200)
INACTIVE_SHADE = 0.45

# "Comet tail" smear under the curve.
# Per-column intensity = clip(|slope| / SMEAR_SLOPE_AT_MAX, 0, 1). Tail length
# in pixels scales with intensity up to SMEAR_H_MAX, and the top-pixel opacity
# also scales with intensity — so flat regions (intensity ≈ 0) paint nothing
# at all, peaks/valleys taper out, and steep slopes get a long, opaque comet.
SMEAR_ALPHA = 0.65
SMEAR_H_MAX = 22
SMEAR_SLOPE_AT_MAX = 3
SMEAR_EXPONENT = 0.8
# Blur the per-column |slope| with a 1D Gaussian (radius in pixels) so the
# tail length transitions smoothly instead of flickering with the per-column
# discrete derivative. sigma = radius / 2 is a good visual compromise.
SMEAR_SLOPE_BLUR_RADIUS = 8


# ── grid helpers ─────────────────────────────────────────────────────────────

# Gridlines are linearly evenly spaced in pixel x. The graph's mapping
# from x to frequency is logarithmic (see freq_to_x), so each major x
# corresponds to whatever frequency lands there — we label that value
# rather than picking round Hz numbers.
_FREQ_MAJOR_STEP_PX = 80  # majors at x = 80, 160, 240
_FREQ_MINOR_STEP_PX = 40  # minors at x = 40, 120, 200, 280
_DB_GRID = (-18.0, -12.0, -6.0, 6.0, 12.0, 18.0)


def _x_to_freq(x: int) -> float:
    import math as _m

    norm = x / (GRAPH_W - 1)
    log_min = _m.log10(20.0)
    log_max = _m.log10(20000.0)
    return 10.0 ** (log_min + norm * (log_max - log_min))


def _fmt_axis_freq(hz: float, with_unit: bool = False) -> str:
    if hz < 1000.0:
        s = f"{int(round(hz))}"
        return f"{s}Hz" if with_unit else s
    k = hz / 1000.0
    if k >= 10.0:
        return f"{int(round(k))}k"
    return f"{k:.1f}k"


_FREQ_MAJORS_X: tuple[int, ...] = (0,) + tuple(x for x in range(_FREQ_MAJOR_STEP_PX, GRAPH_W, _FREQ_MAJOR_STEP_PX))
_FREQ_MINORS_X: tuple[int, ...] = tuple(
    x for x in range(_FREQ_MINOR_STEP_PX, GRAPH_W, _FREQ_MINOR_STEP_PX) if x not in _FREQ_MAJORS_X
)
_FREQ_GRID_X: frozenset[int] = frozenset(_FREQ_MAJORS_X) | frozenset(_FREQ_MINORS_X)

# (label, x_of_gridline) for every vertical gridline. Only the leftmost
# (20 Hz) carries the "Hz" suffix; the rest read as bare numbers / "Xk".
_FREQ_LABELS: tuple[tuple[str, int], ...] = tuple(
    (_fmt_axis_freq(_x_to_freq(x), with_unit=(x == 0)), x) for x in sorted(_FREQ_GRID_X)
)
_DB_LABELS: tuple[tuple[str, float], ...] = (("+18dB", 18.0),)
_AXIS_LABEL_COLOR = (110, 110, 110)


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


# ── smear (comet-tail) helpers ──────────────────────────────────────────────


def _smear_colors_for_state(state: EqState) -> Optional[np.ndarray]:
    """RGB color per graph column, ease-in-out interpolated across the x
    positions of currently-enabled bands. Returns (GRAPH_W, 3) float array,
    or None if no bands are enabled (no smear)."""
    anchors: list[tuple[int, tuple[int, int, int]]] = []
    for band in BANDS:
        p = state.bands.get(band.name)
        if p is None or not p.enabled:
            continue
        anchors.append((int(freq_to_x(p.freq)), BAND_COLORS[band.name]))
    if not anchors:
        return None
    anchors.sort(key=lambda t: t[0])
    xs = np.array([a[0] for a in anchors], dtype=int)
    cs = np.array([a[1] for a in anchors], dtype=float)
    all_x = np.arange(GRAPH_W)
    if len(xs) == 1:
        out = np.broadcast_to(cs[0], (GRAPH_W, 3)).copy()
        return out
    # For each column, find the bracketing pair [xs[i-1], xs[i]] and smoothstep
    # between their colors. Outside the anchor range, clamp to the edge color.
    idx = np.clip(np.searchsorted(xs, all_x, side="right"), 1, len(xs) - 1)
    x0 = xs[idx - 1]
    x1 = xs[idx]
    span = np.maximum(x1 - x0, 1)
    t = np.clip((all_x - x0) / span, 0.0, 1.0)
    t_s = t * t * (3.0 - 2.0 * t)
    out = cs[idx - 1] + (cs[idx] - cs[idx - 1]) * t_s[:, None]
    out[all_x <= xs[0]] = cs[0]
    out[all_x >= xs[-1]] = cs[-1]
    return out


def _gaussian_kernel_1d(radius: int) -> np.ndarray:
    sigma = max(radius / 2.0, 1e-6)
    x = np.arange(-radius, radius + 1, dtype=float)
    k = np.exp(-(x * x) / (2.0 * sigma * sigma))
    return k / k.sum()


_SMEAR_BLUR_KERNEL: np.ndarray = _gaussian_kernel_1d(SMEAR_SLOPE_BLUR_RADIUS)


def _smear_heights_for_curve(curve_y: np.ndarray) -> np.ndarray:
    """Tail length per column derived from a Gaussian-smoothed local |slope|.
    0 px (no smear) when flat, rising linearly to SMEAR_H_MAX at smoothed
    slope ≥ SMEAR_SLOPE_AT_MAX. Smoothing avoids per-column flicker around
    inflection points where the discrete derivative passes through zero."""
    slope = np.abs(np.gradient(curve_y.astype(float)))
    # Normalized convolution: at the edges the kernel hangs off the array, so
    # divide by the kernel mass that actually landed on real samples instead
    # of treating the off-array region as implicit zeros (which dims the ends).
    num = np.convolve(slope, _SMEAR_BLUR_KERNEL, mode="same")
    den = np.convolve(np.ones_like(slope), _SMEAR_BLUR_KERNEL, mode="same")
    smoothed = num / den
    intensity = np.clip(smoothed / SMEAR_SLOPE_AT_MAX, 0.0, 1.0) ** SMEAR_EXPONENT
    # Float length — the renderer integrates a continuous opacity gradient
    # over each row, so quantising the length here would reintroduce stair-
    # stepping as the slope (and the float curve y) vary smoothly.
    return intensity * SMEAR_H_MAX


# ── GraphWidget ──────────────────────────────────────────────────────────────


class GraphWidget(Widget):
    """Owns the curve, grid and band nodes.

    State-change setters (`set_state`, `set_selected`, `set_bypassed`) compute
    the dirty x-extent against cached previous state and call `self.refresh`
    with only that sub-box; `_draw` paints from-scratch but clips work to the
    requested `real_box` so only changed columns are touched (and only those
    columns are flushed over SPI by the panel stack).

    Assumes the widget spans the full panel width with image x == local x
    (same convention used by TunerPanel's widgets).
    """

    NODE_R = 3
    HALO_R = 6

    def __init__(self, box: Box, axis_font=None, **kwargs) -> None:
        kwargs.setdefault("bkgnd_color", BG_BLACK)
        super().__init__(box=box, **kwargs)
        self._axis_font = axis_font
        self._cache = CurveCache()
        self._state: Optional[EqState] = None
        self._selected_band: Optional[str] = None
        self._curve_y: Optional[np.ndarray] = None
        self._curve_y_float: Optional[np.ndarray] = None
        # Per-column y range covered by the polyline's half-segments to each
        # neighbour — used by the AA rasterizer to spread ink across rows
        # in proportion to the slope at that column.
        self._curve_y_lo: Optional[np.ndarray] = None
        self._curve_y_hi: Optional[np.ndarray] = None
        self._node_positions: dict[str, _NodePos] = {}
        self._bypassed: bool = False
        self._smear_colors: Optional[np.ndarray] = None  # (GRAPH_W, 3) or None
        self._smear_h: Optional[np.ndarray] = None  # (GRAPH_W,) ints, 1..SMEAR_H_MAX

    # ── state setters (self-refresh with surgical sub-box) ──────────────────

    def set_state(self, state: EqState) -> None:
        new_curve_db = self._cache.compute(state)
        new_curve_y_float = db_to_y_float(new_curve_db, GRAPH_Y0, GRAPH_Y1, DB_MAX)
        new_curve_y = np.round(new_curve_y_float).astype(int)
        new_y_lo, new_y_hi = self._neighbor_extents(new_curve_y_float)
        new_nodes = self._compute_nodes(state)
        new_smear_colors = _smear_colors_for_state(state)
        new_smear_h = _smear_heights_for_curve(new_curve_y_float)

        old_curve = self._curve_y
        old_nodes = self._node_positions
        old_smear_colors = self._smear_colors
        old_smear_h = self._smear_h

        self._state = state

        if old_curve is None:
            # First paint: commit everything, refresh handled by panel.refresh().
            self._curve_y = new_curve_y
            self._curve_y_float = new_curve_y_float
            self._curve_y_lo = new_y_lo
            self._curve_y_hi = new_y_hi
            self._node_positions = new_nodes
            self._smear_colors = new_smear_colors
            self._smear_h = new_smear_h
            return

        x_min, x_max = self._dirty_extent_for_curve(old_curve, new_curve_y)
        x_min, x_max = self._extend_extent_for_smear(
            x_min,
            x_max,
            old_smear_colors,
            new_smear_colors,
            old_smear_h,
            new_smear_h,
        )
        x_min, x_max = self._extend_extent_for_nodes(x_min, x_max, old_nodes, new_nodes)

        if x_min is None or x_max is None:
            # No dirty columns — keep the committed/displayed arrays as-is so
            # the next diff compares against what's actually on screen. This
            # lets sub-threshold smear drift accumulate over many tweaks
            # until it eventually crosses the visibility threshold.
            return

        self._curve_y = new_curve_y
        self._curve_y_float = new_curve_y_float
        self._curve_y_lo = new_y_lo
        self._curve_y_hi = new_y_hi
        self._node_positions = new_nodes
        self._smear_colors = new_smear_colors
        self._smear_h = new_smear_h
        self._refresh_x_range(x_min, x_max)

    def set_selected(self, band_name: Optional[str]) -> None:  # type: ignore[override]
        if band_name == self._selected_band:
            return
        old = self._selected_band
        self._selected_band = band_name

        x_min: Optional[int] = None
        x_max: Optional[int] = None
        for name in (old, band_name):
            ext = self._node_x_extent(name)
            if ext is None:
                continue
            nx0, nx1 = ext
            x_min = nx0 if x_min is None else min(x_min, nx0)
            x_max = nx1 if x_max is None else max(x_max, nx1)
        self._refresh_x_range(x_min, x_max)

    def set_bypassed(self, bypassed: bool) -> None:
        if self._bypassed == bypassed:
            return
        self._bypassed = bypassed
        self.refresh()  # curve colour shifts globally — repaint everything

    # ── dirty-extent helpers ────────────────────────────────────────────────

    @staticmethod
    def _dirty_extent_for_curve(
        old: np.ndarray,
        new: np.ndarray,
    ) -> tuple[Optional[int], Optional[int]]:
        diff = np.flatnonzero(old != new)
        if diff.size == 0:
            return None, None
        return int(diff[0]), int(diff[-1]) + 1

    @staticmethod
    def _extend_extent_for_smear(
        x_min: Optional[int],
        x_max: Optional[int],
        old_colors: Optional[np.ndarray],
        new_colors: Optional[np.ndarray],
        old_h: Optional[np.ndarray],
        new_h: Optional[np.ndarray],
    ) -> tuple[Optional[int], Optional[int]]:
        # Sub-perceptual tolerances. Both arrays are continuous floats now,
        # so an exact `!=` would flag the full Gaussian kernel radius dirty
        # for every micro-tweak even when the visible delta is below the
        # LCD's quantisation. Threshold at ~half a pixel of comet-length
        # change and ~2 LSBs of an 8-bit colour channel.
        H_EPS = 0.5
        C_EPS = 2.0
        diffs: list[np.ndarray] = []
        if (old_colors is None) != (new_colors is None):
            diffs.append(np.arange(GRAPH_W))
        elif old_colors is not None and new_colors is not None:
            diffs.append(np.flatnonzero(np.any(np.abs(old_colors - new_colors) > C_EPS, axis=1)))
        if (old_h is None) != (new_h is None):
            diffs.append(np.arange(GRAPH_W))
        elif old_h is not None and new_h is not None:
            diffs.append(np.flatnonzero(np.abs(old_h - new_h) > H_EPS))
        combined = np.concatenate(diffs) if diffs else np.array([], dtype=int)
        if combined.size == 0:
            return x_min, x_max
        cmin = int(combined.min())
        cmax = int(combined.max()) + 1
        x_min = cmin if x_min is None else min(x_min, cmin)
        x_max = cmax if x_max is None else max(x_max, cmax)
        return x_min, x_max

    def _extend_extent_for_nodes(
        self,
        x_min: Optional[int],
        x_max: Optional[int],
        old_nodes: dict[str, _NodePos],
        new_nodes: dict[str, _NodePos],
    ) -> tuple[Optional[int], Optional[int]]:
        node_r = self.HALO_R + 1
        names = set(old_nodes) | set(new_nodes)
        for name in names:
            if old_nodes.get(name) == new_nodes.get(name):
                continue
            for n in (old_nodes.get(name), new_nodes.get(name)):
                if n is None:
                    continue
                cx, _, _, _ = n
                nx0, nx1 = cx - node_r, cx + node_r + 1
                x_min = nx0 if x_min is None else min(x_min, nx0)
                x_max = nx1 if x_max is None else max(x_max, nx1)
        return x_min, x_max

    def _node_x_extent(self, name: Optional[str]) -> Optional[tuple[int, int]]:
        if name is None:
            return None
        pos = self._node_positions.get(name)
        if pos is None:
            return None
        cx = pos[0]
        node_r = self.HALO_R + 1
        return cx - node_r, cx + node_r + 1

    def _refresh_x_range(self, x_min: Optional[int], x_max: Optional[int]) -> None:
        if x_min is None or x_max is None:
            return
        bx = self.box
        if bx is None:
            return
        x_min = max(bx.x0, x_min)
        x_max = min(bx.x1, x_max)
        if x_min >= x_max:
            return
        self.refresh(Box(x_min, bx.y0, x_max, bx.y1))

    @staticmethod
    def _neighbor_extents(ys_f: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """For each column, the y range covered by the polyline's two
        half-segments to the immediate neighbours (column-centre to
        midpoint with the next/prev column). Returns (y_lo, y_hi)."""
        mids = (ys_f[:-1] + ys_f[1:]) * 0.5
        y_left = np.empty_like(ys_f)
        y_right = np.empty_like(ys_f)
        y_left[0] = ys_f[0]
        y_left[1:] = mids
        y_right[:-1] = mids
        y_right[-1] = ys_f[-1]
        return np.minimum(y_left, y_right), np.maximum(y_left, y_right)

    @staticmethod
    def _compute_nodes(state: EqState) -> dict[str, _NodePos]:
        out: dict[str, _NodePos] = {}
        for band in BANDS:
            p = state.bands.get(band.name)
            if p is None:
                continue
            cx = int(freq_to_x(p.freq))
            cy = _ZERO_DB_Y if band.gain_sym is None else _db_to_y_scalar(p.gain_db)
            color = BAND_COLORS[band.name]
            if not p.enabled:
                color = tuple(c * 6 // 10 for c in color)
            out[band.name] = (cx, cy, color, p.enabled)
        return out

    # ── paint ───────────────────────────────────────────────────────────────

    def _draw_erase(self, image, draw, real_box) -> None:
        pass  # _draw handles its own background, clipped to real_box

    def _draw(self, image, draw, real_box) -> None:
        # Widget is at panel origin (0, GRAPH_Y0) so image x == local x.
        rx0, ry0 = real_box.x0, real_box.y0
        rx1, ry1 = real_box.x1, real_box.y1

        # Background fill — only the dirty rect
        draw.rectangle([rx0, ry0, rx1 - 1, ry1 - 1], fill=BG_BLACK)

        # Vertical grid lines that fall in [rx0, rx1)
        for x in _FREQ_GRID_X:
            if rx0 <= x < rx1:
                draw.line([(x, max(ry0, GRAPH_Y0)), (x, min(ry1, GRAPH_Y1) - 1)], fill=GRID_DIM)

        # Horizontal grid lines — clip x extent to the dirty rect. Vertical
        # is intentionally unclipped: refreshes are always full graph height,
        # and the -18 dB grid line lands exactly on GRAPH_Y1 (the widget's
        # exclusive bottom edge) — Pillow paints the row, the panel-stack
        # blit captures it.
        hx0 = max(rx0, 0)
        hx1 = min(rx1, _W)
        if hx0 < hx1:
            for db in _DB_GRID:
                y = _db_to_y_scalar(db)
                draw.line([(hx0, y), (hx1 - 1, y)], fill=GRID_DIM)
            draw.line([(hx0, _ZERO_DB_Y), (hx1 - 1, _ZERO_DB_Y)], fill=GRID_0DB)

        # Curve + comet-tail smear — only columns within the dirty rect.
        # Smear paints first (so curve and nodes land on top); each smear
        # pixel is alpha-blended against bg_color(x, y) so grid lines bleed
        # through the tail rather than getting erased.
        if self._curve_y is not None:
            shade = INACTIVE_SHADE if self._bypassed else 1.0
            curve_color = tuple(int(c * shade) for c in CURVE_COLOR)
            cx0 = max(rx0, 0)
            cx1 = min(rx1, GRAPH_W)
            ys = self._curve_y
            ys_f = self._curve_y_float
            y_lo = self._curve_y_lo
            y_hi = self._curve_y_hi
            smear_colors = self._smear_colors
            smear_h = self._smear_h
            has_smear = smear_colors is not None and smear_h is not None
            cr, cg, cb = curve_color
            for x in range(cx0, cx1):
                base_y = int(ys[x])
                hx = float(smear_h[x]) if has_smear else 0.0
                if hx > 0.0:
                    sr, sg, sb = smear_colors[x]
                    sr *= shade
                    sg *= shade
                    sb *= shade
                    top_alpha = SMEAR_ALPHA * hx / SMEAR_H_MAX
                    # Continuous opacity ramp α(y) = top_alpha · (1 − (y−yf)/hx)
                    # for y ∈ [yf, yf+hx]. Per-row alpha is the integral of
                    # that ramp over [R, R+1] (clipped to the active range),
                    # which gives true anti-aliased rendering as yf and hx
                    # slide through fractional values — no pop-in at integer
                    # boundaries.
                    yf = float(ys_f[x]) if ys_f is not None else float(base_y)
                    end_y = yf + hx
                    inv_2hx = 0.5 / hx
                    R = int(math.floor(yf))
                    R_end = int(math.floor(end_y))
                    while R <= R_end and R < GRAPH_Y1:
                        a = yf if R < yf else float(R)
                        b = end_y if R + 1 > end_y else float(R + 1)
                        if b > a:
                            u_a = a - yf
                            u_b = b - yf
                            alpha = top_alpha * ((u_b - u_a) - (u_b * u_b - u_a * u_a) * inv_2hx)
                            if alpha > 0.0 and R >= GRAPH_Y0:
                                br, bg_, bb = bg_color(x, R)
                                draw.point(
                                    (x, R),
                                    fill=(
                                        int(br + (sr - br) * alpha),
                                        int(bg_ + (sg - bg_) * alpha),
                                        int(bb + (sb - bb) * alpha),
                                    ),
                                )
                        R += 1
                # Analytical line rasterization for column x. The line is
                # treated as having unit thickness PERPENDICULAR to its
                # direction; projected onto the column that's a vertical
                # extent of sqrt(1 + slope²) centred on the column's mean y.
                # Each row's coverage is its overlap with that extent (capped
                # at 1), so fully-crossed rows always sit at full alpha and
                # steep slopes don't visually thin out. Each pixel is then
                # alpha-blended against bg_color so the grid bleeds through.
                if y_lo is not None and y_hi is not None:
                    yl = float(y_lo[x])
                    yh = float(y_hi[x])
                    mid = (yl + yh) * 0.5
                    half_extent = math.sqrt(1.0 + (yh - yl) ** 2) * (CURVE_THICKNESS * 0.5)
                    y_lo_ext = mid - half_extent
                    y_hi_ext = mid + half_extent
                    r_lo = int(math.floor(y_lo_ext))
                    r_hi = int(math.floor(y_hi_ext))
                    for ry in range(r_lo, r_hi + 1):
                        if ry < GRAPH_Y0 or ry >= GRAPH_Y1:
                            continue
                        overlap = min(ry + 1, y_hi_ext) - max(ry, y_lo_ext)
                        if overlap <= 0.0:
                            continue
                        a = overlap if overlap < 1.0 else 1.0
                        br, bg_, bb = bg_color(x, ry)
                        draw.point(
                            (x, ry),
                            fill=(
                                int(br + (cr - br) * a),
                                int(bg_ + (cg - bg_) * a),
                                int(bb + (cb - bb) * a),
                            ),
                        )
                else:
                    draw.point((x, base_y), fill=curve_color)

        # Band nodes — skip those whose bbox misses the dirty rect.
        # Draw selected last so the halo lands on top.
        if self._state is not None and self._node_positions:
            node_r = self.HALO_R + 1
            ordered: list[Band] = [b for b in BANDS if b.name != self._selected_band]
            sel = next((b for b in BANDS if b.name == self._selected_band), None)
            if sel is not None:
                ordered.append(sel)
            for band in ordered:
                pos = self._node_positions.get(band.name)
                if pos is None:
                    continue
                cx, cy, color, _enabled = pos
                if cx + node_r <= rx0 or cx - node_r >= rx1:
                    continue
                self._paint_node(draw, cx, cy, color, band.name == self._selected_band)

        # Axis labels (small font). Clipped to the dirty rect so they only
        # repaint when their columns are part of the refresh.
        if self._axis_font is not None:
            self._paint_axis_labels(draw, rx0, rx1)

    def _paint_axis_labels(self, draw, rx0: int, rx1: int) -> None:
        font = self._axis_font
        # dB labels at the left edge.
        for text, db in _DB_LABELS:
            tw, th = get_text_size(text, font)
            x = 2
            if x + tw <= rx0 or x >= rx1:
                continue
            y = _db_to_y_scalar(db)
            if db > 0:
                ty = y + 1
            else:
                ty = y - th - 1
            draw.text((x, ty), text, fill=_AXIS_LABEL_COLOR, font=font)
        # Freq labels along the bottom, placed to the right of each major
        # gridline so the line itself stays unobscured.
        for text, fx in _FREQ_LABELS:
            tw, th = get_text_size(text, font)
            tx = fx + 2
            if tx + tw <= rx0 or tx >= rx1:
                continue
            ty = GRAPH_Y1 - th - 1
            draw.text((tx, ty), text, fill=_AXIS_LABEL_COLOR, font=font)

    def _paint_node(self, draw, cx: int, cy: int, color: tuple[int, int, int], selected: bool) -> None:
        r = self.NODE_R
        # 2px black ring sits between the coloured node (r=3) and the halo
        # (inner edge r=5). Painting it for every band turns the previously
        # transparent gap into a solid outline; for the selected band the
        # halo lands flush on top of it.
        draw.ellipse([cx - r - 2, cy - r - 2, cx + r + 2, cy + r + 2], fill=BG_BLACK)
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
            draw.text((real_box.x0 + 6, real_box.y0 + 1), self._message, fill=READOUT_COLOR, font=self._font)
            return
        for key, x in _READOUT_COLS_LEFT:
            text = self._fields.get(key, "")
            if text:
                draw.text((real_box.x0 + x, real_box.y0 + 1), text, fill=READOUT_COLOR, font=self._font)
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
        # Halo and readout updates are driven by EqPanel._select_widget_idx
        # so chrome focus correctly clears the previously-selected band.

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

        cfg = Config()
        btn_font = cfg.get_font("default")
        axis_font = cfg.get_font("tiny")
        _, btn_text_h = get_text_size("Bypass", btn_font)
        btn_v_margin = max(0, (_BTN_H - btn_text_h) // 2)

        self._readout = ReadoutWidget(
            box=Box.xywh(0, READOUT_Y0, _W, READOUT_Y1 - READOUT_Y0),
            font=btn_font,
            parent=self,
        )
        self._graph = GraphWidget(
            box=Box.xywh(0, GRAPH_Y0, _W, GRAPH_H),
            axis_font=axis_font,
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

    # ── selection routing ───────────────────────────────────────────────────

    def _select_widget_idx(self, idx):  # type: ignore[override]
        super()._select_widget_idx(idx)
        new = self.sel_list[idx]
        band_name = new.band.name if isinstance(new, _BandSelectable) else None
        self._graph.set_selected(band_name)
        self._update_readout()

    # ── band-selectable callbacks ───────────────────────────────────────────

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
