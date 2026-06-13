"""GridPanel geometry + iteration order tests.

Builds the panel against an in-memory PIL-backed PanelStack so we can
exercise selection without an LCD.
"""

from __future__ import annotations

import pytest
from PIL import Image

from modalapi.layout import build_layout_compress
from modalapi.connections import Connection, Endpoint, EndpointKind
from uilib.box import Box
from uilib.gridpanel import (
    CHANNEL,
    DEFAULT_WIRE_COLORS,
    FLOW_DASH,
    FLOW_PERIOD,
    LANE_OFFSETS,
    PORT_OFFSETS_Y,
    ROW_GAP,
    TILE_H,
    TILE_W,
    GridPanel,
    WireFlowOverlay,
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
    # Horizontal pitch = TILE_W + CHANNEL (lanes). Vertical pitch = TILE_H + ROW_GAP (no lanes).
    assert GridPanel.cell_xy(1, 0) == (TILE_W + CHANNEL, 0)
    assert GridPanel.cell_xy(0, 1) == (0, TILE_H + ROW_GAP)
    assert GridPanel.cell_xy(3, 3) == (3 * (TILE_W + CHANNEL), 3 * (TILE_H + ROW_GAP))


def test_four_visible_columns_fit_in_320px() -> None:
    # 4 tiles + 3 inter-tile channels
    last_col_right = GridPanel.cell_xy(3, 0)[0] + TILE_W
    assert last_col_right == 4 * TILE_W + 3 * CHANNEL == 317


def test_four_rows_fit_in_plugin_area() -> None:
    # Plugin area below header / above footswitches must fit 4 rows.
    last_row_bottom = GridPanel.cell_xy(0, 3)[1] + TILE_H
    assert last_row_bottom == 4 * TILE_H + 3 * ROW_GAP


def test_port_attachment_points() -> None:
    # Output port 0 of plugin at (0,0): right edge, y=0+8
    assert GridPanel.out_port_xy(0, 0, 0) == (TILE_W, PORT_OFFSETS_Y[0])
    assert GridPanel.out_port_xy(0, 0, 1) == (TILE_W, PORT_OFFSETS_Y[1])
    # Input port 1 of plugin at (1, 2): left edge of cell, y at row offset + 16
    expected_y = (TILE_H + ROW_GAP) * 2 + PORT_OFFSETS_Y[1]
    assert GridPanel.in_port_xy(1, 2, 1) == (TILE_W + CHANNEL, expected_y)


def test_gutter_lane_x_per_port() -> None:
    # Two distinct lanes inside the channel to the right of `layer`.
    assert GridPanel.gutter_lane_x(0, 0) == TILE_W + LANE_OFFSETS[0]
    assert GridPanel.gutter_lane_x(0, 1) == TILE_W + LANE_OFFSETS[1]
    assert LANE_OFFSETS[0] != LANE_OFFSETS[1]


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
    from uilib.panel import Panel

    host = Panel(box=Box.xywh(0, 0, 320, 240))
    panel_stack.push_panel(host, refresh=False)

    def tile_factory(node, box, parent):
        return TextWidget(box=box, text=node.id, parent=parent)

    return GridPanel(layout, tile_factory, box=Box.xywh(0, 78, 320, 120), parent=host)


def test_iteration_order_is_column_major(panel_stack) -> None:
    # Two columns: col 0 has [A, B], col 1 has [C]
    conns = [
        _conn("capture_1", "A", EndpointKind.SOURCE, EndpointKind.PLUGIN),
        _conn("capture_1", "B", EndpointKind.SOURCE, EndpointKind.PLUGIN),
        _conn("A", "C"),
        _conn("B", "C"),
    ]
    layout = build_layout_compress(["A", "B", "C"], conns)
    panel = _make_panel(layout, panel_stack)

    # Only plugins get widgets — sources/sinks/dummies skipped.
    ids = [w.text for w in panel.sel_children()]  # pyright: ignore[reportAttributeAccessIssue]
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
    layout = build_layout_compress(["A", "B", "D"], conns)
    panel = _make_panel(layout, panel_stack)
    # Only A, B, D are selectable. The dummy in B's column is not.
    ids = sorted(w.text for w in panel.sel_children())  # pyright: ignore[reportAttributeAccessIssue]
    assert ids == ["A", "B", "D"]


def test_widget_for_returns_tile(panel_stack) -> None:
    conns = [_conn("A", "B")]
    layout = build_layout_compress(["A", "B"], conns)
    panel = _make_panel(layout, panel_stack)
    assert panel.widget_for("A") is not None
    assert panel.widget_for("nonexistent") is None


def test_gridpanel_tiles_traverse_via_outer_panel(panel_stack) -> None:
    """A GridPanel sitting in main_panel.sel_list should expose its tiles
    column-major to the outer panel's flat traversal — same UX as if those
    tiles had been added to main_panel directly."""
    from uilib.panel import Panel
    from uilib.text import TextWidget

    main = Panel(box=Box.xywh(0, 0, 320, 240))
    panel_stack.push_panel(main, refresh=False)

    conns = [
        _conn("capture_1", "A", EndpointKind.SOURCE, EndpointKind.PLUGIN),
        _conn("capture_1", "B", EndpointKind.SOURCE, EndpointKind.PLUGIN),
        _conn("A", "C"),
        _conn("B", "C"),
    ]
    layout = build_layout_compress(["A", "B", "C"], conns)
    grid = GridPanel(
        layout,
        lambda node, box, parent: TextWidget(box=box, text=node.id, parent=parent),
        box=Box.xywh(0, 78, 320, 120),
        parent=main,
    )

    # Add a sibling leaf before the grid and one after, to verify "in-and-out".
    before = TextWidget(box=Box.xywh(0, 0, 10, 10), text="<", parent=main)
    after = TextWidget(box=Box.xywh(0, 210, 10, 10), text=">", parent=main)
    main.add_sel_widget(before)
    main.add_sel_widget(grid)
    main.add_sel_widget(after)

    main.sel_widget(before)
    # Outer flat list: before, then plugins in column-major order, then after
    plugin_cols = [[n.id for n in c if n and n.kind == "plugin"] for c in layout.cols]
    expected_plugin_order = [pid for col in plugin_cols for pid in col]
    expected_texts = ["<"] + expected_plugin_order + [">"]
    actual = [w.text for w in main._flat_sel()]
    assert actual == expected_texts

    assert main.sel_ref is not None
    seen = [main.sel_ref.text]
    for _ in range(len(expected_texts) - 1):
        main.sel_next()
        seen.append(main.sel_ref.text)
    assert seen == expected_texts


def test_detaching_a_tile_prunes_it_from_selection(panel_stack) -> None:
    """Tiles inside a GridPanel are not in its sel_list — they're surfaced via
    sel_children. If a tile detaches at runtime, GridPanel must scrub its own
    bookkeeping so the outer panel's flat traversal stops yielding it."""
    conns = [_conn("A", "B")]
    layout = build_layout_compress(["A", "B"], conns)
    panel = _make_panel(layout, panel_stack)
    a_tile = panel.widget_for("A")
    assert a_tile is not None and a_tile in panel.sel_children()
    a_tile.detach()
    assert a_tile not in panel.sel_children()
    assert panel.widget_for("A") is None


# --------------------------------------------------------------------------- #
# Routing geometry.
# --------------------------------------------------------------------------- #


def test_routing_edge_uses_correct_lane_and_port_y(panel_stack) -> None:
    # Stereo plugin: OUT0 uses lane 0 + port y 8; OUT1 uses lane 1 + port y 18.
    # HW-only columns get compacted, so route between two plugins.
    conns = [
        Connection(src=Endpoint(EndpointKind.PLUGIN, "S", "", 0), dst=Endpoint(EndpointKind.PLUGIN, "T", "", 0)),
        Connection(src=Endpoint(EndpointKind.PLUGIN, "S", "", 1), dst=Endpoint(EndpointKind.PLUGIN, "T", "", 1)),
    ]
    layout = build_layout_compress(["S", "T"], conns)
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
    layout = build_layout_compress(["A", "B", "D"], conns)
    panel = _make_panel(layout, panel_stack)

    # The two edges in the dummy chain
    chain = [e for e in layout.edges if e.src.kind == "dummy" or e.dst.kind == "dummy"]
    assert len(chain) == 2
    for edge in chain:
        (_, sy), (_, dy), _ = panel._edge_endpoints(edge)
        # Both endpoints at the OUT1 y (PORT_OFFSETS_Y[1]) within their cells
        assert sy % (TILE_H + ROW_GAP) == PORT_OFFSETS_Y[1]
        assert dy % (TILE_H + ROW_GAP) == PORT_OFFSETS_Y[1]


# --------------------------------------------------------------------------- #
# Wire-flow overlay (marching dots).
# --------------------------------------------------------------------------- #


def test_polyline_pixels_continuous_no_dup_corners() -> None:
    # L-shape: right 2, then down 2. Arc index continuous, corner not doubled.
    pts = [(0, 0), (2, 0), (2, 2)]
    got = list(WireFlowOverlay._polyline_pixels(pts))
    assert got == [
        (0, 0, 0), (1, 1, 0), (2, 2, 0),  # ->right
        (3, 2, 1), (4, 2, 2),             # ->down
    ]


def test_flow_lit_pattern_is_2px_dash_1px_gap() -> None:
    # FLOW_DASH lit then a gap, repeating every FLOW_PERIOD along arc length.
    assert (FLOW_PERIOD, FLOW_DASH) == (3, 2)
    phase = 0.0
    lit = [((s - phase) % FLOW_PERIOD) < FLOW_DASH for s in range(9)]
    assert lit == [True, True, False] * 3


def test_flow_phase_marches_toward_dst() -> None:
    # Advancing phase by 1px shifts the lit set one step up in arc length
    # (i.e. toward dst), not down.
    lit0 = {s for s in range(9) if ((s - 0.0) % FLOW_PERIOD) < FLOW_DASH}
    lit1 = {s for s in range(9) if ((s - 1.0) % FLOW_PERIOD) < FLOW_DASH}
    assert lit1 == {s + 1 for s in lit0 if s + 1 < 9}  # shifted up by one (toward dst)


def _render_overlay(panel, node_id, phase, box=None):
    box = box or Box.xywh(0, 0, 320, 240)
    img = Image.new("RGB", (320, 240), (0, 0, 0))
    draw = __import__("PIL.ImageDraw", fromlist=["ImageDraw"]).Draw(img)
    panel._flow._node = node_id
    panel._flow._dots = node_id is not None
    panel._flow._phase = phase
    panel._flow._draw(img, draw, box)
    return img


def test_overlay_draws_dash_then_gap_along_out_stub(panel_stack) -> None:
    conns = [_conn("A", "B")]
    layout = build_layout_compress(["A", "B"], conns)
    panel = _make_panel(layout, panel_stack)

    base = DEFAULT_WIRE_COLORS[0]
    bright = WireFlowOverlay._brighten(base)
    img = _render_overlay(panel, "A", phase=0.0)

    # A out-port at (TILE_W, 8); stub runs right into the gutter lane.
    y = PORT_OFFSETS_Y[0]
    assert img.getpixel((TILE_W, y)) == bright       # s=0 lit
    assert img.getpixel((TILE_W + 1, y)) == bright   # s=1 lit
    assert img.getpixel((TILE_W + 2, y)) == base     # s=2 gap


def test_overlay_inert_when_nothing_selected(panel_stack) -> None:
    conns = [_conn("A", "B")]
    layout = build_layout_compress(["A", "B"], conns)
    panel = _make_panel(layout, panel_stack)
    img = _render_overlay(panel, None, phase=0.0)
    assert img.getbbox() is None  # fully black: no wires, no dots


def test_overlay_skips_pixels_behind_tiles(panel_stack) -> None:
    # Same-column serpentine: A above B in one column. The spine runs behind
    # both tiles; only the row-gap segment should ever be painted.
    conns = [
        _conn("capture_1", "A", EndpointKind.SOURCE, EndpointKind.PLUGIN),
        _conn("capture_1", "B", EndpointKind.SOURCE, EndpointKind.PLUGIN),
        _conn("A", "B"),
    ]
    layout = build_layout_compress(["A", "B"], conns)
    panel = _make_panel(layout, panel_stack)
    # All of A/B tiles are filled solid colour; assert no overlay pixel landed
    # inside either tile rect.
    img = _render_overlay(panel, "A", phase=0.0)
    for (tx0, ty0, tx1, ty1) in panel.tile_rects():
        for x in range(tx0, tx1):
            for yy in range(ty0, ty1):
                assert img.getpixel((x, yy)) == (0, 0, 0)


def test_tick_tracks_selection(panel_stack) -> None:
    conns = [_conn("A", "B")]
    layout = build_layout_compress(["A", "B"], conns)
    panel = _make_panel(layout, panel_stack)

    panel.tick()
    assert panel._flow._node is None  # nothing selected -> inert

    panel.widget_for("A").set_selected(True)
    panel.tick()
    assert panel._flow._node == "A"

    panel.widget_for("A").set_selected(False)
    panel.tick()
    assert panel._flow._node is None
