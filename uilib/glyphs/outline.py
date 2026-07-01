"""Corner-tile blitters for rounded-rect outlines and fills on panel/dialog bodies.

Renders one small rounded-rect glyph per distinct corner radius, slices the
corner pieces, and blits them at the panel corners plus draws the straight
edge segments with plain pygame primitives. The cache key is tiny (per corner
radius / border width / color, not per panel size), so the many dialog sizes
in use share a handful of small surfaces instead of each caching a full-size
SRCALPHA mask.

Two renderers:

* ``render_rounded_outline`` — border-only ring (transparent interior), drawn
  *over* children so the border covers content painted to the panel edge.
* ``render_rounded_fill`` — opaque rounded fill (transparent outside), used by
  the titlebar strip whose corners sit above the panel body.
"""

from __future__ import annotations

from functools import lru_cache

import pygame

from common.color import ColorRGB, RectBorder
from uilib.glyphs.rounded_rect import RoundedRectGlyph
from uilib.radius import Radius


def _tile_size(rc: int, border_width: int) -> int:
    """Corner tile extent: the corner radius plus the border width plus 1px AA halo."""
    return max(rc, border_width) + 1


@lru_cache(maxsize=256)
def _corner_outline_tile(rc: int, border_width: int, color: ColorRGB, corner: str) -> pygame.Surface:
    """One AA corner of a border-only ring, for blitting at the matching panel corner.

    Renders a small uniform-radius rounded rect of size ``2(r+w+1)`` with the
    given border, then slices the quadrant matching *corner*
    (``"tl"``/``"tr"``/``"bl"``/``"br"``) — a self-contained corner piece with
    two straight edges (to abut the edge lines) and one AA arc.
    """
    if rc <= 0 and border_width <= 0:
        return pygame.Surface((1, 1), pygame.SRCALPHA)
    half = _tile_size(rc, border_width)
    full = half * 2
    border = RectBorder(top=color, right=color, bottom=color, left=color)
    glyph = RoundedRectGlyph(full, full, Radius.uniform(rc), fill=None, border=border, border_width=border_width)
    rendered = glyph.render()
    rects = {
        "tl": (0, 0, half, half),
        "tr": (half, 0, half, half),
        "bl": (0, half, half, half),
        "br": (half, half, half, half),
    }
    return rendered.subsurface(rects[corner]).copy()


def render_rounded_outline(
    width: int,
    height: int,
    radius: Radius,
    color: ColorRGB,
    border_width: int,
) -> pygame.Surface:
    """Border-only rounded-rect outline on a transparent SRCALPHA surface.

    The interior is transparent so content painted under it shows through; the
    border ring is drawn over children. Straight edges use ``pygame.draw.line``
    (axis-aligned, no AA needed); corners blit the cached AA corner tiles.
    """
    surf = pygame.Surface((width, height), pygame.SRCALPHA)
    if border_width <= 0:
        return surf
    r = Radius._coerce(radius)
    # Corner tiles — each sliced from the matching quadrant of its exemplar.
    tl_tile = _corner_outline_tile(r.top_left, border_width, color, "tl")
    tr_tile = _corner_outline_tile(r.top_right, border_width, color, "tr")
    bl_tile = _corner_outline_tile(r.bottom_left, border_width, color, "bl")
    br_tile = _corner_outline_tile(r.bottom_right, border_width, color, "br")
    surf.blit(tl_tile, (0, 0))
    surf.blit(tr_tile, (width - tr_tile.get_width(), 0))
    surf.blit(bl_tile, (0, height - bl_tile.get_height()))
    surf.blit(br_tile, (width - br_tile.get_width(), height - br_tile.get_height()))
    # Straight edge segments (axis-aligned → no AA needed), spanning between
    # the corner regions. A corner occupies [0, r] along its adjacent edges.
    top_len = width - r.top_left - r.top_right
    bot_len = width - r.bottom_left - r.bottom_right
    left_len = height - r.top_left - r.bottom_left
    right_len = height - r.top_right - r.bottom_right
    if top_len > 0:
        pygame.draw.line(surf, color, (r.top_left, 0), (width - r.top_right, 0), border_width)
    if bot_len > 0:
        # Bottom edge at y=height-border_width..height-1 so the full thickness
        # lands on-surface (pygame.draw.line centers on the endpoint, so drawing
        # at height-1 would clip half the stroke off the bottom).
        pygame.draw.line(surf, color, (r.bottom_left, height - border_width),
                         (width - r.bottom_right, height - border_width), border_width)
    if left_len > 0:
        pygame.draw.line(surf, color, (0, r.top_left), (0, height - r.bottom_left), border_width)
    if right_len > 0:
        pygame.draw.line(surf, color, (width - border_width, r.top_right),
                         (width - border_width, height - r.bottom_right), border_width)
    return surf


def render_rounded_fill(
    width: int,
    height: int,
    radius: Radius,
    color: ColorRGB,
) -> pygame.Surface:
    """Opaque rounded-rect fill on a transparent SRCALPHA surface.

    The interior is solid ``color``; outside the rounded rect is transparent
    with 1px AA falloff at the corners. Used for the titlebar strip, whose
    bottom is square (meets the panel body) and whose top corners round.
    """
    return RoundedRectGlyph(width, height, radius, fill=color, border=None).render()
