"""Column-major routing-aware grid panel.

Lays out a modalapi.layout.Layout into tiles on a Panel. Iteration order
is column-major (top-to-bottom within a column, then next column). Holes
and dummy nodes are not selectable. Horizontal scrolling is handled by
the ContainerWidget base class' built-in scroll-into-view mechanism.

Geometry follows the locked design parameters:
  - Tile size:      74 x 24
  - Channel gap:    7 px (asymmetric: 1 + 1 + 3 + 1 + 1)
  - Lane offsets:   1 (port 0) and 5 (port 1) within the 7px channel
  - Port y offsets: 8 (port 0) and 16 (port 1) within the 24h tile
"""

from __future__ import annotations

import time
from typing import Callable, Optional

from modalapi.layout import Layout, LayoutEdge, LayoutNode
from uilib.box import Box
from uilib.container import ContainerWidget
from uilib.widget import Widget


TILE_W = 74
TILE_H = 28
CHANNEL = 7  # horizontal column gutter
ROW_GAP = 3  # vertical row gutter — no wires routed here, so chosen for visual breathing room
LANE_OFFSETS: tuple[int, int] = (2, 4)
PORT_OFFSETS_Y: tuple[int, int] = (8, 18)

# Default wire colors keyed on src_port (overridable via constructor).
DEFAULT_WIRE_COLORS: tuple[tuple[int, int, int], tuple[int, int, int]] = (
    (0, 200, 255),  # port 0 — cyan
    (255, 160, 0),  # port 1 — amber
)

# Flow animation: brighter dashes march src->dst over the static wires to show
# audio direction. Only the selected plugin's incident wires animate.
#   FLOW_DASH px lit, then (FLOW_PERIOD - FLOW_DASH) px gap, repeating.
# Phase is derived from the wall clock so the visual speed is constant across
# the variable LCD frame interval (poll_divisor swings ~30-80 ms by SPI clock).
FLOW_PERIOD = 3       # px between dash starts
FLOW_DASH = 2         # px lit per dash
FLOW_PX_PER_SEC = 6.0  # slow, subtle drift

TileFactory = Callable[[LayoutNode, Box, Widget], Widget]
"""(node, box, parent) -> Widget. The factory MUST construct the tile with
`parent` already wired (e.g. pass `parent=parent` to TextWidget). Attaching
later wipes any explicit color/font set on the widget because
`_setup_act_attrs` re-resolves inherited attributes from the parent."""


class GridPanel(ContainerWidget):
    """A scrollable column-major grid of LayoutNodes.

    The container's `box` defines the *visible* viewport (typically 320px
    wide); tiles are positioned in the panel's own virtual coordinate space
    which can extend well past the viewport. ContainerWidget's
    `_scroll_into_view` handles bringing offscreen tiles into view when
    selection moves onto them.

    Tiles are exposed to a host Panel's selection traversal via
    `sel_children()` in column-major order. Only "plugin" nodes get a
    widget — sources, sinks, dummies and holes are skipped, so the outer
    selection naturally jumps over empty cells.
    """

    def __init__(
        self,
        layout: Layout,
        tile_factory: TileFactory,
        box: Box,
        wire_colors: tuple[tuple[int, int, int], tuple[int, int, int]] = DEFAULT_WIRE_COLORS,
        **kwargs,
    ) -> None:
        super().__init__(box=box, **kwargs)
        self.layout = layout
        self.wire_colors = wire_colors
        self.tile_widgets: dict[str, Widget] = {}
        self.tile_order: list[Widget] = []  # column-major insertion order
        self._build(tile_factory)
        # On-top dot overlay. Attached last so it composites over the tiles;
        # it animates only the selected plugin's incident wires and partial-
        # refreshes just those gutter strips (no tile recomposite). Its box
        # spans the full virtual content so it covers wires past the viewport.
        cw = max(self.box.width, (TILE_W + CHANNEL) * len(self.layout.cols))
        ch = max(self.box.height,
                 (TILE_H + ROW_GAP) * max((len(c) for c in self.layout.cols), default=0))
        self._flow = WireFlowOverlay(self, box=Box.xywh(0, 0, cw, ch), parent=self)

    def tick(self) -> None:
        """Advance the wire-flow animation one step (called from the LCD poll)."""
        self._flow.tick()

    # ------------------------------------------------------------------ #
    # Geometry helpers (also used by the routing render pass).
    # ------------------------------------------------------------------ #

    @staticmethod
    def cell_xy(layer: int, row: int) -> tuple[int, int]:
        return ((TILE_W + CHANNEL) * layer, (TILE_H + ROW_GAP) * row)

    @classmethod
    def cell_box(cls, layer: int, row: int) -> Box:
        x, y = cls.cell_xy(layer, row)
        return Box.xywh(x, y, TILE_W, TILE_H)

    @classmethod
    def out_port_xy(cls, layer: int, row: int, port_idx: int) -> tuple[int, int]:
        """Right-edge attachment point for an output wire."""
        x, y = cls.cell_xy(layer, row)
        return (x + TILE_W, y + PORT_OFFSETS_Y[port_idx])

    @classmethod
    def in_port_xy(cls, layer: int, row: int, port_idx: int) -> tuple[int, int]:
        """Left-edge attachment point for an input wire."""
        x, y = cls.cell_xy(layer, row)
        return (x, y + PORT_OFFSETS_Y[port_idx])

    @classmethod
    def gutter_lane_x(cls, layer: int, port_idx: int) -> int:
        """X coord of the vertical lane in the gap to the right of `layer`."""
        return (TILE_W + CHANNEL) * layer + TILE_W + LANE_OFFSETS[port_idx]

    # ------------------------------------------------------------------ #
    # Build tiles from layout.
    # ------------------------------------------------------------------ #

    def _build(self, tile_factory: TileFactory) -> None:
        # Column-major insertion → outer Panel's flat traversal walks
        # top-to-bottom within a column, then jumps to the top of the next.
        for layer_idx, col in enumerate(self.layout.cols):
            for row_idx, node in enumerate(col):
                if node is None or node.kind != "plugin":
                    continue  # holes, sources, sinks, dummies: no tile widget
                box = self.cell_box(layer_idx, row_idx)
                widget = tile_factory(node, box, self)
                assert widget.parent is self, (
                    "tile_factory must attach the widget to the GridPanel "
                    "(pass parent=parent to the widget constructor)"
                )
                widget.selectable = True
                self.tile_widgets[node.id] = widget
                self.tile_order.append(widget)

    # ------------------------------------------------------------------ #
    # Selection: expose tiles to the parent panel via sel_children so the
    # outer flat traversal walks them column-major as if they were direct
    # entries in the parent's sel_list.
    # ------------------------------------------------------------------ #

    def sel_children(self):
        return list(self.tile_order)

    def _notify_detach(self, widget):
        """A tile detaching at runtime must be removed from tile_order and
        tile_widgets so the outer panel's flat sel traversal (which calls
        our sel_children()) stops yielding a detached widget."""
        if widget in self.tile_order:
            self.tile_order.remove(widget)
            for nid, w in list(self.tile_widgets.items()):
                if w is widget:
                    del self.tile_widgets[nid]
                    break

    # ------------------------------------------------------------------ #
    # Public API.
    # ------------------------------------------------------------------ #

    def widget_for(self, node_id: str) -> Optional[Widget]:
        return self.tile_widgets.get(node_id)

    # ------------------------------------------------------------------ #
    # Routing render pass.
    # ------------------------------------------------------------------ #

    @staticmethod
    def _clamp_port(idx: int) -> int:
        """Multi-channel plugins (e.g. an 8-in mixer) report port indices ≥ 2.
        We only have two visual lanes today; fold extras into the second one
        so rendering doesn't crash."""
        return 0 if idx <= 0 else 1

    def _edge_endpoints(self, edge: LayoutEdge) -> tuple[tuple[int, int], tuple[int, int], int]:
        """Resolve (src_xy, dst_xy, vertical_lane_x) for one column-spanning
        edge. Dummies use carried_src_port as both their in and out port.

        When the destination is a dummy, we anchor dst_xy at the *right* edge
        of the dummy cell so the segment crosses the whole cell. Otherwise
        each edge in a dummy chain would only touch the dummy's left edge,
        leaving the cell's width visually unconnected.
        """
        src, dst = edge.src, edge.dst
        src_y_idx = self._clamp_port(src.carried_src_port if src.kind == "dummy" else edge.src_port)
        dst_y_idx = self._clamp_port(dst.carried_src_port if dst.kind == "dummy" else edge.dst_port)
        lane_idx = self._clamp_port(edge.src_port)
        src_xy = self.out_port_xy(src.layer, src.row, src_y_idx)
        if dst.kind == "dummy":
            dst_xy = self.out_port_xy(dst.layer, dst.row, dst_y_idx)
        else:
            dst_xy = self.in_port_xy(dst.layer, dst.row, dst_y_idx)
        lane_x = self.gutter_lane_x(src.layer, lane_idx)
        return src_xy, dst_xy, lane_x

    def _edge_polyline(self, edge: LayoutEdge) -> list[tuple[int, int]]:
        """Ordered src->dst corner points (panel-local) for one edge. Used both
        by the static wire draw and the flow-dot overlay so the two never drift.

        Same-column (serpentine) edges run as a single vertical line at the
        column's horizontal centre; the runs behind the src/dst tiles are
        hidden by the tiles, so the visible wire sits in the row gap. Stereo
        pairs nudge apart by port so the two wires don't coincide. Cross-column
        edges are Manhattan: out-stub right -> vertical lane -> in-stub right."""
        if edge.src.layer == edge.dst.layer:
            port = self._clamp_port(edge.src_port)
            x0, _ = self.cell_xy(edge.src.layer, 0)
            cx = x0 + TILE_W // 2 + (3 if port else 0)
            _, sy = self.cell_xy(edge.src.layer, edge.src.row)
            _, dy = self.cell_xy(edge.dst.layer, edge.dst.row)
            return [(cx, sy + TILE_H // 2), (cx, dy + TILE_H // 2)]
        (sx, sy), (dx, dy), lane_x = self._edge_endpoints(edge)
        return [(sx, sy), (lane_x, sy), (lane_x, dy), (dx, dy)]

    def _draw(self, image, draw, real_box) -> None:
        """Draw the routing wires under any child tiles. See _edge_polyline for
        the routing geometry. Opaque so shared segments render cleanly
        regardless of draw order."""
        super()._draw(image, draw, real_box)
        ox, oy = real_box.topleft
        for edge in self.layout.edges:
            color = self.wire_colors[self._clamp_port(edge.src_port)]
            pts = self._edge_polyline(edge)
            for (ax, ay), (bx, by) in zip(pts, pts[1:]):
                draw.line([(ox + ax, oy + ay), (ox + bx, oy + by)], fill=color, width=1)

    # ------------------------------------------------------------------ #
    # Flow-overlay support.
    # ------------------------------------------------------------------ #

    def incident_edges(self, node_id: str) -> list[LayoutEdge]:
        """Edges with the given plugin node as src or dst (its visible wires)."""
        return [e for e in self.layout.edges
                if e.src.id == node_id or e.dst.id == node_id]

    def selected_node_id(self) -> Optional[str]:
        for node_id, w in self.tile_widgets.items():
            if getattr(w, "selected", False):
                return node_id
        return None

    def tile_rects(self) -> list[tuple[int, int, int, int]]:
        """(x0,y0,x1,y1) of every plugin tile, panel-local. The flow overlay
        draws on top of the tiles, so it skips dots that fall inside one
        (the serpentine spine's run behind tiles must stay hidden)."""
        rects = []
        for layer_idx, col in enumerate(self.layout.cols):
            for row_idx, node in enumerate(col):
                if node is None or node.kind != "plugin":
                    continue
                b = self.cell_box(layer_idx, row_idx)
                rects.append((b.x0, b.y0, b.x1, b.y1))
        return rects


class WireFlowOverlay(Widget):
    """Marching-dot flow animation drawn on top of a GridPanel's tiles.

    Animates only the currently-selected plugin's incident wires. Each visible
    wire pixel is repainted every refresh — base wire colour in the gaps, a
    brighter dot where lit — which both erases the previous frame's dots and
    draws the new ones, so no full-panel recomposite is needed. The lit set is
    `(s - phase) % FLOW_PERIOD < FLOW_DASH` along arc-length `s`, with `phase`
    derived from the wall clock so the drift speed is independent of the LCD
    frame interval. Refreshes are scoped to the incident wires' bounding box
    (thin column gutters, no tiles) and only fire when the integer phase
    advances — the lit pattern is constant within a 1px phase step.
    """

    def __init__(self, grid: GridPanel, box: Box, **kwargs) -> None:
        super().__init__(box=box, **kwargs)
        self._grid = grid
        self._phase = 0.0
        self._node: Optional[str] = None  # plugin whose wires _draw repaints
        self._dots = True                 # overlay dots, or just clear them
        self._last_iphase: Optional[int] = None

    @staticmethod
    def _brighten(c: tuple[int, int, int]) -> tuple[int, int, int]:
        return tuple(min(255, int(v + (255 - v) * 0.55)) for v in c)

    @staticmethod
    def _polyline_pixels(pts):
        """Yield (s, x, y) for each integer pixel along an axis-aligned
        polyline, src->dst, with a continuous arc index `s` and no doubled
        corners."""
        s = 0
        yield s, pts[0][0], pts[0][1]
        for (ax, ay), (bx, by) in zip(pts, pts[1:]):
            stepx = (bx > ax) - (bx < ax)
            stepy = (by > ay) - (by < ay)
            x, y = ax, ay
            for _ in range(abs(bx - ax) + abs(by - ay)):
                x += stepx
                y += stepy
                s += 1
                yield s, x, y

    def _incident_bbox(self, node_id: str) -> Optional[Box]:
        """Bounding box (panel-local) of the node's incident wires, padded for
        wire/dot width. None if the node has no edges."""
        xs: list[int] = []
        ys: list[int] = []
        for edge in self._grid.incident_edges(node_id):
            for x, y in self._grid._edge_polyline(edge):
                xs.append(x)
                ys.append(y)
        if not xs:
            return None
        return Box(min(xs) - 1, min(ys) - 1, max(xs) + 2, max(ys) + 2)

    def _draw_erase(self, image, draw, box) -> None:
        pass  # transparent: never wipe the tiles/wires we sit on top of

    def _draw(self, image, draw, real_box) -> None:
        node = self._node
        if node is None:
            return
        grid = self._grid
        sx, sy = grid.offset
        ox, oy = -sx, -sy  # content -> image origin (scroll-aware)
        cx0, cy0, cx1, cy1 = real_box.rect
        tiles = grid.tile_rects()
        phase = self._phase
        for edge in grid.incident_edges(node):
            base = grid.wire_colors[grid._clamp_port(edge.src_port)]
            bright = self._brighten(base)
            for s, x, y in self._polyline_pixels(grid._edge_polyline(edge)):
                ix, iy = x + ox, y + oy
                if not (cx0 <= ix < cx1 and cy0 <= iy < cy1):
                    continue
                if any(tx0 <= x < tx1 and ty0 <= y < ty1
                       for tx0, ty0, tx1, ty1 in tiles):
                    continue  # hidden behind a tile
                lit = self._dots and ((s - phase) % FLOW_PERIOD) < FLOW_DASH
                draw.point((ix, iy), fill=bright if lit else base)

    def tick(self) -> None:
        node = self._grid.selected_node_id()
        self._phase = time.monotonic() * FLOW_PX_PER_SEC
        iphase = int(self._phase)
        if node != self._node:
            # Selection moved: repaint the old node's wires once with no dots
            # to wipe its stale dots, then switch to the new node.
            if self._node is not None:
                old, self._node, self._dots = self._node, self._node, False
                box = self._incident_bbox(old)
                if box is not None:
                    self.refresh(box)
                self._dots = True
            self._node = node
            self._last_iphase = None
        if self._node is None:
            return
        if iphase != self._last_iphase:
            self._last_iphase = iphase
            box = self._incident_bbox(self._node)
            if box is not None:
                self.refresh(box)
