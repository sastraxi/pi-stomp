"""Behavioural library for the DP fold layout (modalapi.build_layout_dp).

Each case states a graph (plugins + audio connections) and pins the expected
grid. The grid is rendered as one string per row, plugin ids in cells and "."
for holes — readable enough to eyeball the intended shape. Dummies live only on
edges (not in cols), so they never appear here; `violations()` separately
asserts no wire was forced through a plugin.

Invariants every case upholds:
  * width stays within the 4-wide viewport when the graph fits (no over-fold);
  * a linear chain snakes boustrophedon (down a column, up the next);
  * parallel branches keep aligned rows (no crossing);
  * no edge routes through a plugin (no through-plugin dummy).
"""

from modalapi.connections import Connection, Endpoint, EndpointKind
from modalapi.layout import build_layout_dp, occupied_cells


# --------------------------------------------------------------------------- #
# Builders
# --------------------------------------------------------------------------- #


def _ep(id_: str, port: int = 0, kind: EndpointKind = EndpointKind.PLUGIN) -> Endpoint:
    return Endpoint(kind=kind, id=id_, port_symbol="", port_idx=port)


def conn(src: str, dst: str, src_port: int = 0, dst_port: int = 0) -> Connection:
    return Connection(src=_ep(src, src_port), dst=_ep(dst, dst_port))


def chain(*ids: str) -> list[Connection]:
    """capture is implicit; just the plugin->plugin edges of a linear chain."""
    return [conn(ids[i], ids[i + 1]) for i in range(len(ids) - 1)]


def dp(ids: list[str], conns: list[Connection]):
    return build_layout_dp(ids, conns)


def grid(layout) -> list[str]:
    rows = []
    for r in range(layout.n_rows):
        cells = []
        for c in range(layout.n_cols):
            n = layout.cols[c][r] if r < len(layout.cols[c]) else None
            cells.append(n.id if (n and n.kind == "plugin") else ".")
        rows.append(" ".join(cells))
    return rows


def violations(layout) -> set[str]:
    """Dummy waypoints that landed on a plugin cell — i.e. a wire forced
    through a plugin because its column had no gap."""
    occ = occupied_cells(layout)
    return {n.id for e in layout.edges for n in (e.src, e.dst) if n.kind == "dummy" and (n.layer, n.row) in occ}


def cell_of(layout, plugin_id: str) -> tuple[int, int]:
    for c, col in enumerate(layout.cols):
        for r, n in enumerate(col):
            if n is not None and n.id == plugin_id:
                return (c, r)
    raise AssertionError(f"{plugin_id} not placed")


# --------------------------------------------------------------------------- #
# Linear chains: fill the viewport width first, then wrap (boustrophedon).
# --------------------------------------------------------------------------- #


def test_linear_4_stays_a_flat_row() -> None:
    # Fits the 4-wide viewport, so it must NOT fold narrower-and-taller.
    lay = dp(list("ABCD"), chain(*"ABCD"))
    assert (lay.n_cols, lay.n_rows) == (4, 1)
    assert grid(lay) == ["A B C D"]
    assert not violations(lay)


def test_linear_8_fills_width_then_wraps_boustrophedon() -> None:
    lay = dp(list("ABCDEFGH"), chain(*"ABCDEFGH"))
    assert (lay.n_cols, lay.n_rows) == (4, 2)
    # Down col0 (A,B), up col1 (C at bottom, D at top), down col2, up col3.
    assert grid(lay) == [
        "A D E H",
        "B C F G",
    ]
    assert not violations(lay)


def test_linear_12_three_rows_boustrophedon() -> None:
    lay = dp(list("ABCDEFGHIJKL"), chain(*"ABCDEFGHIJKL"))
    assert (lay.n_cols, lay.n_rows) == (4, 3)
    assert grid(lay) == [
        "A F G L",
        "B E H K",
        "C D I J",
    ]
    assert not violations(lay)


# --------------------------------------------------------------------------- #
# Parallel branches must stay on aligned rows (no crossing).
# --------------------------------------------------------------------------- #


def test_two_parallel_chains_keep_aligned_rows() -> None:
    # A->B and C->D are independent; they must not cross into a fused gutter.
    lay = dp(list("ABCD"), [conn("A", "B"), conn("C", "D")])
    assert (lay.n_cols, lay.n_rows) == (2, 2)
    assert grid(lay) == [
        "A B",
        "C D",
    ]
    # The defining property: each branch is a straight horizontal (same row).
    assert cell_of(lay, "A")[1] == cell_of(lay, "B")[1]
    assert cell_of(lay, "C")[1] == cell_of(lay, "D")[1]
    assert cell_of(lay, "A")[1] != cell_of(lay, "C")[1]
    assert not violations(lay)


def test_diamond_split_merge() -> None:
    # A -> {B, C} -> D
    lay = dp(list("ABCD"), [conn("A", "B"), conn("A", "C"), conn("B", "D"), conn("C", "D")])
    assert (lay.n_cols, lay.n_rows) == (2, 2)
    assert grid(lay) == [
        "A C",
        "B D",
    ]
    assert not violations(lay)


def test_fanout_three() -> None:
    # A -> B, A -> C, A -> D
    lay = dp(list("ABCD"), [conn("A", "B"), conn("A", "C"), conn("A", "D")])
    assert (lay.n_cols, lay.n_rows) == (2, 2)
    assert grid(lay) == [
        "A C",
        "B D",
    ]
    assert not violations(lay)


# --------------------------------------------------------------------------- #
# Skip edge gets absorbed into adjacent columns — no through-plugin routing.
# --------------------------------------------------------------------------- #


def test_skip_edge_routes_without_crossing_a_plugin() -> None:
    # Chain A->B->C->D plus a bypass A->D.
    lay = dp(list("ABCD"), [conn("A", "B"), conn("B", "C"), conn("C", "D"), conn("A", "D")])
    assert (lay.n_cols, lay.n_rows) == (2, 2)
    assert grid(lay) == [
        "A D",
        "B C",
    ]
    # A and D share a row, so the bypass is a clean single-column hop.
    assert cell_of(lay, "A")[1] == cell_of(lay, "D")[1]
    assert not violations(lay)


# --------------------------------------------------------------------------- #
# Degenerate cases.
# --------------------------------------------------------------------------- #


def test_single_plugin() -> None:
    lay = dp(["A"], [])
    assert (lay.n_cols, lay.n_rows) == (1, 1)
    assert grid(lay) == ["A"]
    assert not violations(lay)


def test_no_plugins() -> None:
    lay = dp([], [])
    assert lay.n_cols == 0
    assert not violations(lay)


def test_disconnected_plugins_pack_in() -> None:
    # Three plugins, no edges — should still fit the viewport with no dummies.
    lay = dp(list("ABC"), [])
    assert lay.n_cols <= 4
    assert not violations(lay)
    placed = {n.id for col in lay.cols for n in col if n is not None}
    assert placed == {"A", "B", "C"}
