"""Custom widgets for the NAM plugin panel.

Two pieces:

* ``MiniArcKnob`` — small arc-ring display for input/output/quality, sized
  to fit three stacked in a 64px-wide left column.

* ``FileListView`` — virtualized list of NAM file paths. Only the rows
  currently inside the viewport are instantiated; the rest are scrolled
  past in the backing surface and never cost a paint or a tick. The
  currently-selected row is the only one that contains a ``ScrollingText``
  child, so its marquee animation is the only one that advances per tick.

The widgets live here (rather than inline in ``panel.py``) because the
test file builds them in isolation and the snapshot test wants to render
them without spinning up the full panel.
"""

from __future__ import annotations

import os
from typing import Optional

import pygame
from typing_extensions import override

from uilib.box import Box
from uilib.config import Config
from uilib.glyphs import ArcRingGlyph
from uilib.misc import TextHAlign, get_text_size
from uilib.paint import PaintContext
from uilib.text import ScrollingText
from uilib.widget import Widget

# MiniArcKnob ────────────────────────────────────────────────────────────────


# Colours borrowed from pistomp/nam/panel.py (the NAM capture panel) so
# the three control arcs look like siblings of the capture-view knobs.
_ARC_FG = (195, 135, 40)  # amber filled arc
_ARC_BG = (38, 30, 14)  # dim warm dark — empty track
_ARC_TIP = (255, 210, 80)  # bright amber — tip dot
_LABEL_FG = (115, 115, 125)
_VALUE_FG = (175, 175, 195)


def _format_value(value: float, unit: str) -> str:
    """Render a knob readout. ``"dB"`` is signed with one decimal (unit
    is implied by the row label — printing "dB" again would be visual
    noise on a 64px-wide column); quality (0..1) is unsigned with two
    decimals. Mirrors the EQ panel's per-band readouts
    (``plugins/eq/panel.py:807``)."""
    if unit == "dB":
        return f"{value:+.1f}"
    return f"{value:.2f}"


class MiniArcKnob(Widget):
    """Small arc-only knob. Label above, value under, no internal children.

    The widget holds a single ``ArcRingGlyph`` sized to fit the box and
    ``set_value`` triggers a self-refresh with a tight tip-dot bbox (we
    only need the tip dot + value text to move on a typical encoder tick).
    """

    _ARC_PAD = 6  # space for label / value text above + below the ring

    def __init__(
        self,
        box: Box,
        label: str,
        min_val: float,
        max_val: float,
        unit: str = "",
        parent: Widget | None = None,
    ) -> None:
        super().__init__(box=box, bkgnd_color=(0, 0, 0), parent=parent)
        self._label = label
        self._min = min_val
        self._max = max_val
        self._unit = unit
        self._value = min_val

        # Lay out the arc inside the box, leaving _ARC_PAD on top + bottom
        # for the label and value strings. Width is the limiting axis when
        # the box is narrow (which is the case in a 3-stacked column).
        arc_box_h = max(1, box.height - 2 * self._ARC_PAD - 12)  # 12 ≈ label+value
        arc_box_w = max(1, box.width)
        self._arc_r = max(6, min(arc_box_w // 2 - 2, arc_box_h // 2))
        self._arc = ArcRingGlyph(self._arc_r, ring_half=2.5, tip_radius=2.5)

        cfg = Config()
        self._label_font = cfg.get_font("list") or cfg.get_font("default")
        self._value_font = cfg.get_font("list") or cfg.get_font("default")
        assert self._label_font is not None
        assert self._value_font is not None

    def set_value(self, value: float) -> None:
        new_val = max(self._min, min(self._max, value))
        if new_val == self._value:
            return
        self._value = new_val
        self.refresh()

    def set_range(self, min_val: float, max_val: float) -> None:
        self._min = min_val
        self._max = max_val
        self.refresh()

    @property
    def value(self) -> float:
        return self._value

    def _t(self) -> float:
        if self._max == self._min:
            return 0.0
        return max(0.0, min(1.0, (self._value - self._min) / (self._max - self._min)))

    @override
    def _draw(self, ctx: PaintContext) -> None:
        w, h = ctx.width, ctx.height
        # Arc: centred horizontally, in the band between label and value.
        # We give 10px to the label (top) and 12px to the value (bottom).
        arc_box_h = max(1, h - 22)
        cx = w // 2
        cy = 4 + arc_box_h // 2

        surf = self._arc.render(self._t(), _ARC_FG, _ARC_BG, _ARC_TIP)
        hs = self._arc.half_size
        ctx.paste(surf, (cx - hs, cy - hs))

        # Label across the top
        ctx.draw_text(
            (cx, cy + 2),
            self._label,
            fill=_LABEL_FG,
            font=self._label_font,
            anchor="mm",
        )
        # Value across the bottom
        ctx.draw_text(
            (cx, h - 7),
            _format_value(self._value, self._unit),
            fill=_VALUE_FG,
            font=self._value_font,
            anchor="mm",
        )


# FileListView ──────────────────────────────────────────────────────────────


# Visually matches the notes panel's scrollbar (plugins/notes/panel.py:26-55).
_SB_WIDTH = 4
_SB_GAP = 2
_SB_COLOR = (160, 160, 160)
_SEL_BG = (40, 40, 48)
_CHECK = "\u2714"  # ✓


class _ListRow(Widget):
    """One row of the file list.

    Non-selected rows are static (no children, _draw returns after one
    text blit). The selected row contains a single ``ScrollingText`` child
    which is the only thing in the panel that ticks per LCD poll.
    """

    def __init__(self, box: Box, font, is_selected: bool, parent: "FileListView") -> None:
        kwargs: dict = {"bkgnd_color": (0, 0, 0)}
        if is_selected:
            kwargs["bkgnd_color"] = _SEL_BG
        super().__init__(box=box, parent=parent, **kwargs)
        self._font = font
        self._is_selected = is_selected
        self._list = parent
        self._scrolling: Optional[ScrollingText] = None
        if is_selected:
            # Reserve the left 14px for the check mark + a couple px gap.
            # The ScrollingText is the only child; its _anchor_time
            # advances every LCD poll (uilib/text.py:462). Off rows have
            # no such child → no tick cost.
            self._scrolling = ScrollingText(
                box=Box.xywh(14, 0, box.width - 14, box.height),
                text=parent._text_for_row(parent._top + (box.y0 // parent._row_h)),
                font=font,
                text_halign=TextHAlign.LEFT,
                h_margin=2,
                v_margin=0,
                parent=self,
            )

    @override
    def _draw(self, ctx: PaintContext) -> None:
        # The ScrollingText child paints its own text when present. For
        # non-selected rows we paint the basename here.
        if self._scrolling is None:
            text = self._list._text_for_row(self._list._top + (self.box.y0 // self._list._row_h))
            if text:
                ctx.draw_text((14, 0), text, fill=(180, 180, 190), font=self._font)
        if self._is_selected:
            # Check mark on the left of the row
            ctx.draw_text(
                (8, ctx.height // 2),
                _CHECK,
                fill=(255, 210, 80),
                font=self._font,
                anchor="mm",
            )

    @override
    def _draw_selection(self, ctx: PaintContext) -> None:
        # Suppress the base Widget's selection reticule — the dark row
        # background + ✓ glyph above are the visual indicators. Without
        # this override, a 240×18 selection box would outline the whole
        # row at the selected index.
        pass


class FileListView(Widget):
    """Virtualized file list.

    Public API:
      * ``set_files(paths)`` — re-populate. Triggers a full refresh.
      * ``selected_index`` / ``set_selected_index(i)`` — current row.
      * ``step(delta)`` — move selection by ``±1`` and scroll into view.

    Implementation notes:
      * We do *not* inherit from ``ContainerWidget`` — we manage row
        children explicitly so we can tear them down and rebuild on
        selection/scroll changes. (The ContainerWidget virtual path is
        for *tall static content*; here we have N logical rows and want
        only a small window of them to ever exist as widgets.)
      * The selected row is the only one with a ``ScrollingText`` child;
        when ``set_selected_index`` swaps rows we detach the old row's
        children (so its ``_anchor_time`` stops advancing) and attach
        a fresh ScrollingText on the new row. This is the "only
        currently-selected file animates" guarantee — it falls out of
        the widget tree, not from a tick guard.
    """

    def __init__(
        self,
        box: Box,
        row_h: int = 18,
        files: Optional[list[str]] = None,
        parent: Widget | None = None,
    ) -> None:
        super().__init__(box=box, bkgnd_color=(0, 0, 0), parent=parent)
        self._row_h = row_h
        self._files: list[str] = list(files) if files else []
        self._top = 0
        self._selected = 0
        # Total scrollable content height
        self._content_h = max(1, len(self._files) * row_h)
        self._viewport_h = box.height
        # font
        cfg = Config()
        self._font = cfg.get_font("list") or cfg.get_font("default")
        assert self._font is not None
        # viewport-window computation
        self._vis_count = max(1, self._viewport_h // row_h)
        self._build_rows()

    def _visible_indices(self) -> range:
        return range(self._top, min(len(self._files), self._top + self._vis_count))

    def _text_for_row(self, idx: int) -> str:
        if 0 <= idx < len(self._files):
            # Files are full paths from the filesystem scan; the row label
            # is the basename so the user sees the model name, not the
            # directory layout.
            return os.path.basename(self._files[idx])
        return ""

    def set_files(self, files: list[str]) -> None:
        self._files = list(files)
        self._content_h = max(1, len(self._files) * self._row_h)
        if self._selected >= len(self._files):
            self._selected = max(0, len(self._files) - 1)
        self._top = min(self._top, max(0, self._max_top()))
        self._build_rows()
        self.refresh()

    def set_selected_index(self, idx: int, *, scroll: bool = True) -> None:
        if not self._files:
            return
        idx = max(0, min(len(self._files) - 1, idx))
        if idx == self._selected:
            return
        self._selected = idx
        if scroll:
            self._scroll_into_view()
        # The row at the new position needs a ScrollingText child; the
        # row at the old position must drop its ScrollingText so its
        # _anchor_time stops advancing.
        self._build_rows()
        self.refresh()

    def step(self, delta: int) -> None:
        if not self._files:
            return
        self.set_selected_index(self._selected + delta)

    @property
    def selected_index(self) -> int:
        return self._selected

    @property
    def selected_path(self) -> str:
        if 0 <= self._selected < len(self._files):
            return self._files[self._selected]
        return ""

    def _max_top(self) -> int:
        return max(0, len(self._files) - self._vis_count)

    def _scroll_into_view(self) -> None:
        if self._selected < self._top:
            self._top = self._selected
        elif self._selected >= self._top + self._vis_count:
            self._top = self._selected - self._vis_count + 1
        self._top = max(0, min(self._max_top(), self._top))

    def _row_box(self, vis_idx: int) -> Box:
        # vis_idx is the index within the visible window
        y = vis_idx * self._row_h
        return Box.xywh(0, y, self.box.width - (_SB_WIDTH + _SB_GAP), self._row_h)

    def _build_rows(self) -> None:
        # Tear down existing rows
        for c in list(self.children):
            c.detach()
        # Rebuild only the visible window
        for vis_idx, file_idx in enumerate(self._visible_indices()):
            row_box = self._row_box(vis_idx)
            is_sel = file_idx == self._selected
            _ListRow(row_box, self._font, is_sel, parent=self)

    def input_event(self, event) -> bool:
        from uilib.misc import InputEvent

        if event == InputEvent.LEFT:
            if self._selected > 0:
                self.step(-1)
                return True
            # At the top of the list — let the parent panel move selection
            # backwards (typically to the chrome buttons).
            return False
        if event == InputEvent.RIGHT:
            if self._selected < len(self._files) - 1:
                self.step(1)
                return True
            # At the bottom of the list — let the parent panel move
            # selection forwards to the chrome buttons.
            return False
        return super().input_event(event)

    @override
    def _draw_erase(self, ctx: PaintContext) -> None:
        # Background already set to black; nothing more to erase.
        pass

    @override
    def _draw_selection(self, ctx: PaintContext) -> None:
        # Suppress the base Widget's selection reticule. The selected
        # row's dark background + ✓ glyph are the visual indicators; a
        # yellow outline around the whole file list area would dominate
        # the chrome and look like a debug bounding box.
        pass

    @override
    def _draw(self, ctx: PaintContext) -> None:
        # Scrollbar — only when content overflows
        if len(self._files) > self._vis_count:
            track_x = ctx.width - _SB_WIDTH
            track_h = ctx.height
            thumb_h = max(8, track_h * self._vis_count // len(self._files))
            max_top = self._max_top()
            if max_top > 0:
                thumb_y = (track_h - thumb_h) * self._top // max_top
            else:
                thumb_y = 0
            ctx.draw_rectangle(
                Box.xywh(track_x, thumb_y, _SB_WIDTH, thumb_h),
                fill=_SB_COLOR,
                radius=2,
            )
