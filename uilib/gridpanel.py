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

from typing import Callable, Optional

from modalapi.layout import Layout, LayoutEdge, LayoutNode
from uilib.box import Box
from uilib.panel import Panel
from uilib.widget import Widget


TILE_W = 74
TILE_H = 24
CHANNEL = 7
LANE_OFFSETS: tuple[int, int] = (1, 5)
PORT_OFFSETS_Y: tuple[int, int] = (8, 16)

# Default wire colors keyed on src_port (overridable via constructor).
DEFAULT_WIRE_COLORS: tuple[tuple[int, int, int], tuple[int, int, int]] = (
    (0, 200, 255),  # port 0 — cyan
    (255, 160, 0),  # port 1 — amber
)

TileFactory = Callable[[LayoutNode, Box], Widget]


class GridPanel(Panel):
    """A Panel that arranges LayoutNodes on a column-major grid.

    Selection iteration is column-major (insertion order). Only "plugin"
    nodes are added to the selectable list — sources, sinks, and dummies
    are skipped entirely (no widget created), making holes show as empty
    cells the selection naturally jumps over.
    """

    def __init__(
        self,
        layout: Layout,
        tile_factory: TileFactory,
        box: Box,
        visible_cols: int = 4,
        wire_colors: tuple[tuple[int, int, int], tuple[int, int, int]] = DEFAULT_WIRE_COLORS,
        **kwargs,
    ) -> None:
        super().__init__(box=box, **kwargs)
        self.layout = layout
        self.visible_cols = visible_cols
        self.wire_colors = wire_colors
        self.tile_widgets: dict[str, Widget] = {}
        self._build(tile_factory)

    # ------------------------------------------------------------------ #
    # Geometry helpers (also used by the routing render pass).
    # ------------------------------------------------------------------ #

    @staticmethod
    def cell_xy(layer: int, row: int) -> tuple[int, int]:
        return ((TILE_W + CHANNEL) * layer, (TILE_H + CHANNEL) * row)

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
        # Column-major insertion → default Panel.sel_next walks top-to-bottom
        # within a column, then jumps to the top of the next column.
        for layer_idx, col in enumerate(self.layout.cols):
            for row_idx, node in enumerate(col):
                if node is None or node.kind != "plugin":
                    continue  # holes, sources, sinks, dummies: no tile widget
                box = self.cell_box(layer_idx, row_idx)
                widget = tile_factory(node, box)
                # tile_factory should attach the widget to this panel as its
                # parent so coords resolve correctly. Defensive: attach here
                # if it didn't.
                if widget.parent is None:
                    widget.attach(self)
                self.add_sel_widget(widget)
                self.tile_widgets[node.id] = widget

    # ------------------------------------------------------------------ #
    # Public API.
    # ------------------------------------------------------------------ #

    def widget_for(self, node_id: str) -> Optional[Widget]:
        return self.tile_widgets.get(node_id)

    # ------------------------------------------------------------------ #
    # Routing render pass.
    # ------------------------------------------------------------------ #

    def _edge_endpoints(self, edge: LayoutEdge) -> tuple[tuple[int, int], tuple[int, int], int]:
        """Resolve (src_xy, dst_xy, vertical_lane_x) for one column-spanning
        edge. Dummies use carried_src_port as both their in and out port."""
        src, dst = edge.src, edge.dst
        if src.kind == "dummy":
            src_y_idx = src.carried_src_port
        else:
            src_y_idx = edge.src_port
        if dst.kind == "dummy":
            dst_y_idx = dst.carried_src_port
        else:
            dst_y_idx = edge.dst_port
        src_xy = self.out_port_xy(src.layer, src.row, src_y_idx)
        dst_xy = self.in_port_xy(dst.layer, dst.row, dst_y_idx)
        lane_x = self.gutter_lane_x(src.layer, edge.src_port)
        return src_xy, dst_xy, lane_x

    def _draw(self, image, draw, real_box) -> None:
        """Draw the routing wires under any child tiles. Manhattan routing:
        out-stub right -> vertical in gutter at lane[src_port] -> in-stub
        right into dst. Opaque so shared segments render cleanly regardless
        of draw order."""
        super()._draw(image, draw, real_box)
        ox, oy = real_box.topleft
        for edge in self.layout.edges:
            (sx, sy), (dx, dy), lane_x = self._edge_endpoints(edge)
            color = self.wire_colors[edge.src_port]
            # Three segments. Coords are panel-local; offset by real_box origin.
            draw.line([(ox + sx, oy + sy), (ox + lane_x, oy + sy)], fill=color, width=1)
            draw.line([(ox + lane_x, oy + sy), (ox + lane_x, oy + dy)], fill=color, width=1)
            draw.line([(ox + lane_x, oy + dy), (ox + dx, oy + dy)], fill=color, width=1)
