"""GridPanel geometry + iteration order tests.

Builds the panel against an in-memory PIL-backed PanelStack so we can
exercise selection without an LCD.
"""

from __future__ import annotations

import pytest
from PIL import Image

from modalapi.layout import build_layout
from modalapi.connections import Connection, Endpoint, EndpointKind
from uilib.box import Box
from uilib.gridpanel import (
    CHANNEL,
    LANE_OFFSETS,
    PORT_OFFSETS_Y,
    TILE_H,
    TILE_W,
    GridPanel,
)
from uilib.panel import PanelStack
from uilib.text import TextWidget


class _StubLcd:
    def dimensions(self):
        return (320, 240)

    def default_format(self):
        return "RGB"

    def update(self, image, box=None):
        pass


@pytest.fixture
def panel_stack():
    return PanelStack(_StubLcd(), use_dimming=False)


# --------------------------------------------------------------------------- #
# Geometry helpers (pure, no widget tree required).
# --------------------------------------------------------------------------- #


def test_cell_xy_origin() -> None:
    assert GridPanel.cell_xy(0, 0) == (0, 0)


def test_cell_xy_spacing_matches_locked_design() -> None:
    # Tile pitch is 74+7=81 horizontally, 24+7=31 vertically
    assert GridPanel.cell_xy(1, 0) == (TILE_W + CHANNEL, 0)
    assert GridPanel.cell_xy(0, 1) == (0, TILE_H + CHANNEL)
    assert GridPanel.cell_xy(3, 3) == (3 * (TILE_W + CHANNEL), 3 * (TILE_H + CHANNEL))


def test_four_visible_columns_fit_in_320px() -> None:
    # 4 tiles + 3 inter-tile channels
    last_col_right = GridPanel.cell_xy(3, 0)[0] + TILE_W
    assert last_col_right == 4 * TILE_W + 3 * CHANNEL == 317


def test_four_rows_fit_in_plugin_area() -> None:
    # Plugin area is 240 - 78 - 51 (footswitch row) = ~111
    last_row_bottom = GridPanel.cell_xy(0, 3)[1] + TILE_H
    assert last_row_bottom == 4 * TILE_H + 3 * CHANNEL == 117


def test_port_attachment_points() -> None:
    # Output port 0 of plugin at (0,0): right edge, y=0+8
    assert GridPanel.out_port_xy(0, 0, 0) == (TILE_W, PORT_OFFSETS_Y[0])
    assert GridPanel.out_port_xy(0, 0, 1) == (TILE_W, PORT_OFFSETS_Y[1])
    # Input port 1 of plugin at (1, 2): left edge of cell, y at row offset + 16
    expected_y = (TILE_H + CHANNEL) * 2 + PORT_OFFSETS_Y[1]
    assert GridPanel.in_port_xy(1, 2, 1) == (TILE_W + CHANNEL, expected_y)


def test_gutter_lane_x_per_port() -> None:
    # Channel layout: pad(1) + lane0(1) + pad(2) + lane1(1) + pad(2)?
    # Actually: pad(1) + lane(1) + pad(3) + lane(1) + pad(1). Lanes at
    # offset 1 and 5 within the 7px channel that starts at x = TILE_W.
    assert GridPanel.gutter_lane_x(0, 0) == TILE_W + LANE_OFFSETS[0]
    assert GridPanel.gutter_lane_x(0, 1) == TILE_W + LANE_OFFSETS[1]
    # Lanes are 4px apart (centre-to-centre: 5 - 1 = 4)
    assert LANE_OFFSETS[1] - LANE_OFFSETS[0] == 4


# --------------------------------------------------------------------------- #
# Column-major iteration: selection walks top-to-bottom then next column.
# --------------------------------------------------------------------------- #


def _ep(kind: EndpointKind, id_: str) -> Endpoint:
    return Endpoint(kind=kind, id=id_, port_symbol="", port_idx=0)


def _conn(
    src_id: str, dst_id: str, src_kind: EndpointKind = EndpointKind.PLUGIN, dst_kind: EndpointKind = EndpointKind.PLUGIN
) -> Connection:
    return Connection(src=_ep(src_kind, src_id), dst=_ep(dst_kind, dst_id))


def _make_panel(layout, panel_stack):
    # Build the panel with a placeholder factory; attach to the stack first
    # so child widgets inherit needed attributes.
    panel = GridPanel.__new__(GridPanel)
    # Bypass __init__ tile build until after stack attach
    from uilib.panel import Panel

    Panel.__init__(panel, box=Box.xywh(0, 78, 320, 120))
    panel.layout = layout
    panel.visible_cols = 4
    panel.tile_widgets = {}
    panel_stack.push_panel(panel, refresh=False)

    def tile_factory(node, box):
        return TextWidget(box=box, text=node.id, parent=panel)

    panel._build(tile_factory)
    return panel


def test_iteration_order_is_column_major(panel_stack) -> None:
    # Two columns: col 0 has [A, B], col 1 has [C]
    conns = [
        _conn("capture_1", "A", EndpointKind.SOURCE, EndpointKind.PLUGIN),
        _conn("capture_1", "B", EndpointKind.SOURCE, EndpointKind.PLUGIN),
        _conn("A", "C"),
        _conn("B", "C"),
    ]
    layout = build_layout(["A", "B", "C"], conns)
    panel = _make_panel(layout, panel_stack)

    # Only plugins get widgets — sources/sinks/dummies skipped.
    ids = [w.text for w in panel.sel_list]
    # capture_1 column has no widgets; A and B both in plugin column;
    # column-major insertion = layer iter outer, row iter inner.
    plugin_cols = [[n.id for n in c if n and n.kind == "plugin"] for c in layout.cols]
    expected = [pid for col in plugin_cols for pid in col]
    assert ids == expected


def test_holes_and_dummies_excluded_from_selection(panel_stack) -> None:
    # Skip-layer edge creates a dummy in the middle column
    conns = [
        _conn("A", "B"),
        _conn("B", "D"),
        _conn("A", "D"),  # spans 2 columns -> dummy in B's column
    ]
    layout = build_layout(["A", "B", "D"], conns)
    panel = _make_panel(layout, panel_stack)
    # Only A, B, D are selectable. The dummy in B's column is not.
    ids = sorted(w.text for w in panel.sel_list)
    assert ids == ["A", "B", "D"]


def test_widget_for_returns_tile(panel_stack) -> None:
    conns = [_conn("A", "B")]
    layout = build_layout(["A", "B"], conns)
    panel = _make_panel(layout, panel_stack)
    assert panel.widget_for("A") is not None
    assert panel.widget_for("nonexistent") is None


# --------------------------------------------------------------------------- #
# Routing geometry.
# --------------------------------------------------------------------------- #


def test_routing_edge_uses_correct_lane_and_port_y(panel_stack) -> None:
    # Stereo plugin: OUT1 -> playback_1 uses lane 0 + port y 8;
    # OUT2 -> playback_2 uses lane 1 + port y 16.
    conns = [
        _conn("capture_1", "S", EndpointKind.SOURCE, EndpointKind.PLUGIN),
        Connection(src=_ep(EndpointKind.PLUGIN, "S"), dst=_ep(EndpointKind.SINK, "playback_1")),
        Connection(
            src=Endpoint(EndpointKind.PLUGIN, "S", "", 1),
            dst=Endpoint(EndpointKind.SINK, "playback_2", "", 0),
        ),
    ]
    layout = build_layout(["S"], conns)
    panel = _make_panel(layout, panel_stack)

    out_edges = {e.src_port: e for e in layout.edges if e.src.id == "S"}
    (sx0, sy0), (dx0, dy0), lane0 = panel._edge_endpoints(out_edges[0])
    (sx1, sy1), (dx1, dy1), lane1 = panel._edge_endpoints(out_edges[1])

    # Same source x (right edge of S), but different y per port.
    assert sx0 == sx1
    assert sy0 + (PORT_OFFSETS_Y[1] - PORT_OFFSETS_Y[0]) == sy1
    # Lanes differ by LANE_OFFSETS spread (4px).
    assert lane1 - lane0 == LANE_OFFSETS[1] - LANE_OFFSETS[0]


def test_dummy_passes_wire_straight_through(panel_stack) -> None:
    # Skip-layer edge -> dummy in the middle column. The dummy's in and out
    # ports must be at the same y, equal to PORT_OFFSETS_Y[src_port].
    conns = [
        _conn("A", "B"),
        _conn("B", "D"),
        Connection(
            src=Endpoint(EndpointKind.PLUGIN, "A", "", 1),
            dst=Endpoint(EndpointKind.PLUGIN, "D", "", 1),
        ),
    ]
    layout = build_layout(["A", "B", "D"], conns)
    panel = _make_panel(layout, panel_stack)

    # The two edges in the dummy chain
    chain = [e for e in layout.edges if e.src.kind == "dummy" or e.dst.kind == "dummy"]
    assert len(chain) == 2
    for edge in chain:
        (_, sy), (_, dy), _ = panel._edge_endpoints(edge)
        # Both endpoints at the OUT1 y (PORT_OFFSETS_Y[1]) within their cells
        assert sy % (TILE_H + CHANNEL) == PORT_OFFSETS_Y[1]
        assert dy % (TILE_H + CHANNEL) == PORT_OFFSETS_Y[1]
