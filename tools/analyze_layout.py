#!/usr/bin/env python3
"""Analyse pedalboard grid layouts against the real layout pipeline.

Loads one or more .pedalboard bundles via the same lilv + MOD-Desktop path
the device uses, runs modalapi.layout.build_layout, and reports metrics +
an ASCII render of the grid. Intended as a scratchpad for iterating on the
layout algorithm (e.g. the vertical-snake compaction).

Run via ./analyze_layout.sh (sets up lilv on PYTHONPATH/DYLD_LIBRARY_PATH).
Requires MOD Desktop running at http://127.0.0.1:18181 to resolve plugin
audio-port ordering.

Usage:
    ./analyze_layout.sh <bundle.pedalboard> [more.pedalboard ...]
    ./analyze_layout.sh --all            # every board in the MOD Desktop dir
    ./analyze_layout.sh --all --summary  # one-line metrics row per board
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from pathlib import Path

# Keep the repo root importable regardless of CWD.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image, ImageDraw, ImageFont  # noqa: E402

from modalapi.layout import Layout, LayoutEdge, build_layout  # noqa: E402
from modalapi.pedalboard import Pedalboard  # noqa: E402
# Geometry + wire colors: reuse the real GridPanel so renders match the device.
from uilib.gridpanel import (  # noqa: E402
    CHANNEL,
    DEFAULT_WIRE_COLORS,
    ROW_GAP,
    TILE_H,
    TILE_W,
    GridPanel,
)

MOD_ROOT_URI = "http://127.0.0.1:18181/"
MOD_PEDALBOARD_DIR = Path.home() / "Documents" / "MOD Desktop" / "pedalboards"
RENDER_DIR = Path(__file__).resolve().parent / "renders"

# Visible LCD grid band (see lcd320x240.draw_plugins): 320w x 130h.
VIEWPORT_W, VIEWPORT_H = 320, 130
COL_PITCH = TILE_W + CHANNEL
ROW_PITCH = TILE_H + ROW_GAP


@dataclass
class Metrics:
    title: str
    n_plugins: int
    cols: int
    rows: int
    parallelism: int      # max plugin tiles in any single column (layer)
    dummies: int          # waypoint cells inserted for multi-column edges
    plugin_cells: int
    total_cells: int

    @property
    def empty_cells(self) -> int:
        return self.total_cells - self.plugin_cells

    @property
    def empty_pct(self) -> float:
        return 100.0 * self.empty_cells / self.total_cells if self.total_cells else 0.0

    @property
    def px_w(self) -> int:
        return max(0, self.cols * COL_PITCH - CHANNEL)

    @property
    def px_h(self) -> int:
        return max(0, self.rows * ROW_PITCH - ROW_GAP)

    @property
    def h_screens(self) -> float:
        return self.px_w / VIEWPORT_W

    @property
    def v_screens(self) -> float:
        return self.px_h / VIEWPORT_H


def compute_metrics(title: str, n_plugins: int, layout: Layout) -> Metrics:
    plugin_cells = dummies = 0
    parallelism = 0
    for col in layout.cols:
        col_plugins = sum(1 for n in col if n is not None and n.kind == "plugin")
        parallelism = max(parallelism, col_plugins)
        plugin_cells += col_plugins
        dummies += sum(1 for n in col if n is not None and n.kind == "dummy")
    return Metrics(
        title=title,
        n_plugins=n_plugins,
        cols=layout.n_cols,
        rows=layout.n_rows,
        parallelism=parallelism,
        dummies=dummies,
        plugin_cells=plugin_cells,
        total_cells=layout.n_cols * layout.n_rows,
    )


def ascii_grid(layout: Layout, cell_w: int = 10) -> str:
    """Row-major textual render of the column-major grid.
    Plugin tiles show a truncated id; dummies show '·'; holes are blank."""
    rows, cols = layout.n_rows, layout.n_cols
    lines = []
    for r in range(rows):
        cells = []
        for c in range(cols):
            node = layout.cols[c][r] if r < len(layout.cols[c]) else None
            if node is None:
                cells.append(" " * cell_w)
            elif node.kind == "dummy":
                cells.append("·".ljust(cell_w))
            else:
                cells.append(node.id[: cell_w - 1].ljust(cell_w))
        lines.append("|" + "|".join(cells) + "|")
    sep = "+" + "+".join(["-" * cell_w] * cols) + "+"
    out = [sep]
    for ln in lines:
        out.append(ln)
        out.append(sep)
    return "\n".join(out)


# --------------------------------------------------------------------------- #
# PNG render — faithful to GridPanel: same cell geometry, same 3-segment
# gutter wire routing (out-stub -> vertical lane -> in-stub).
# --------------------------------------------------------------------------- #

MARGIN = 8
BG = (24, 24, 28)
TILE_FILL = (60, 64, 72)
TILE_OUTLINE = (180, 184, 192)
DUMMY_DOT = (90, 90, 100)
TEXT_COLOR = (235, 235, 240)


def _edge_endpoints(edge: LayoutEdge):
    """Replica of GridPanel._edge_endpoints (instance-free): resolve
    (src_xy, dst_xy, lane_x) for one column-spanning edge."""
    src, dst = edge.src, edge.dst
    clamp = GridPanel._clamp_port
    src_y = clamp(src.carried_src_port if src.kind == "dummy" else edge.src_port)
    dst_y = clamp(dst.carried_src_port if dst.kind == "dummy" else edge.dst_port)
    lane = clamp(edge.src_port)
    src_xy = GridPanel.out_port_xy(src.layer, src.row, src_y)
    if dst.kind == "dummy":
        dst_xy = GridPanel.out_port_xy(dst.layer, dst.row, dst_y)
    else:
        dst_xy = GridPanel.in_port_xy(dst.layer, dst.row, dst_y)
    lane_x = GridPanel.gutter_lane_x(src.layer, lane)
    return src_xy, dst_xy, lane_x


def _font(size: int = 11):
    for name in ("Menlo.ttc", "Monaco.ttf", "DejaVuSansMono.ttf", "Arial.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except Exception:
            continue
    return ImageFont.load_default()


def render_png(layout: Layout, title: str, path: Path) -> None:
    w = max(1, layout.n_cols * (TILE_W + CHANNEL) - CHANNEL) + 2 * MARGIN
    h = max(1, layout.n_rows * (TILE_H + ROW_GAP) - ROW_GAP) + 2 * MARGIN
    img = Image.new("RGB", (w, h), BG)
    draw = ImageDraw.Draw(img)
    font = _font(11)

    # Wires first, under the tiles (matches GridPanel draw order).
    for edge in layout.edges:
        (sx, sy), (dx, dy), lane_x = _edge_endpoints(edge)
        color = DEFAULT_WIRE_COLORS[GridPanel._clamp_port(edge.src_port)]
        ox = oy = MARGIN
        draw.line([(ox + sx, oy + sy), (ox + lane_x, oy + sy)], fill=color, width=1)
        draw.line([(ox + lane_x, oy + sy), (ox + lane_x, oy + dy)], fill=color, width=1)
        draw.line([(ox + lane_x, oy + dy), (ox + dx, oy + dy)], fill=color, width=1)

    for c, col in enumerate(layout.cols):
        for r, node in enumerate(col):
            if node is None:
                continue
            x, y = GridPanel.cell_xy(c, r)
            x += MARGIN
            y += MARGIN
            if node.kind == "dummy":
                cx, cy = x + TILE_W // 2, y + TILE_H // 2
                draw.ellipse([cx - 2, cy - 2, cx + 2, cy + 2], fill=DUMMY_DOT)
                continue
            draw.rounded_rectangle([x, y, x + TILE_W, y + TILE_H], radius=5,
                                   fill=TILE_FILL, outline=TILE_OUTLINE, width=1)
            label = node.id[:11]
            draw.text((x + 4, y + TILE_H // 2 - 6), label, fill=TEXT_COLOR, font=font)

    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path)


def load_layout(bundle: Path) -> tuple[Pedalboard, Layout]:
    title = bundle.stem
    pb = Pedalboard(title, str(bundle), root_uri=MOD_ROOT_URI)
    pb.load_bundle(str(bundle), {})
    ids = [p.instance_id.lstrip("/") for p in pb.plugins]
    layout = build_layout(ids, pb.connections)
    return pb, layout


def print_full(bundle: Path, png: bool = True) -> Metrics:
    pb, layout = load_layout(bundle)
    m = compute_metrics(bundle.stem, len(pb.plugins), layout)
    if png:
        out = RENDER_DIR / f"{bundle.stem}.png"
        render_png(layout, bundle.stem, out)
        print(f"png: {out}")
    print(f"\n=== {m.title} ===")
    print(
        f"plugins={m.n_plugins}  grid={m.cols}x{m.rows} (cols x rows)  "
        f"parallelism={m.parallelism}  dummies={m.dummies}"
    )
    print(
        f"cells: {m.plugin_cells}/{m.total_cells} filled  "
        f"empty={m.empty_pct:.0f}%"
    )
    print(
        f"pixels: {m.px_w}x{m.px_h}  scroll: {m.h_screens:.2f} screens horiz, "
        f"{m.v_screens:.2f} vert"
    )
    print(ascii_grid(layout))
    return m


SUMMARY_HEADER = (
    f"{'pedalboard':24} {'plug':>4} {'cols':>4} {'rows':>4} "
    f"{'par':>3} {'dum':>3} {'empty%':>6} {'h-scr':>5} {'v-scr':>5}"
)


def summary_row(m: Metrics) -> str:
    return (
        f"{m.title[:24]:24} {m.n_plugins:>4} {m.cols:>4} {m.rows:>4} "
        f"{m.parallelism:>3} {m.dummies:>3} {m.empty_pct:>5.0f}% "
        f"{m.h_screens:>5.2f} {m.v_screens:>5.2f}"
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("bundles", nargs="*", help="Pedalboard bundle paths")
    ap.add_argument("--all", action="store_true",
                    help=f"Analyse every board in {MOD_PEDALBOARD_DIR}")
    ap.add_argument("--summary", action="store_true",
                    help="One metrics row per board, no ASCII grid")
    ap.add_argument("--no-png", action="store_true",
                    help=f"Skip writing PNG renders to {RENDER_DIR}")
    args = ap.parse_args()

    bundles: list[Path] = [Path(b) for b in args.bundles]
    if args.all:
        bundles += sorted(MOD_PEDALBOARD_DIR.glob("*.pedalboard"))
    if not bundles:
        ap.error("no bundles given (pass paths or --all)")

    metrics: list[Metrics] = []
    for b in bundles:
        if not b.exists():
            print(f"skip (missing): {b}", file=sys.stderr)
            continue
        try:
            if args.summary:
                pb, layout = load_layout(b)
                if not args.no_png:
                    render_png(layout, b.stem, RENDER_DIR / f"{b.stem}.png")
                metrics.append(compute_metrics(b.stem, len(pb.plugins), layout))
            else:
                metrics.append(print_full(b, png=not args.no_png))
        except Exception as e:  # keep going across a batch
            print(f"FAILED {b.stem}: {e}", file=sys.stderr)

    if args.summary and metrics:
        print(SUMMARY_HEADER)
        print("-" * len(SUMMARY_HEADER))
        for m in sorted(metrics, key=lambda x: x.empty_pct, reverse=True):
            print(summary_row(m))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
