# This file is part of pi-stomp.
#
# pi-stomp is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# pi-stomp is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with pi-stomp.  If not, see <https://www.gnu.org/licenses/>.

from dataclasses import dataclass
from typing import Any, Callable, Optional

from uilib.box import Box
from uilib.text import TextWidget, TextHAlign, InputEvent
from uilib.misc import get_text_size
from uilib.dialog import Dialog
from uilib.config import Config


@dataclass
class MenuRow:
    """A typed row in a Menu.

    Callbacks take no args — capture state via closure or default-arg binding.
    `active` renders a checkmark prefix. `font` overrides the menu default.
    """

    label: str
    on_click: Optional[Callable[[], None]] = None
    on_long_click: Optional[Callable[[], None]] = None
    active: bool = False
    font: Optional[Any] = None


class _MenuRowWidget(TextWidget):
    """TextWidget that carries its source MenuRow for typed selection tracking."""

    row: MenuRow


class Menu(Dialog):
    """A vertical pop-up of MenuRows. Each row is a selectable TextWidget
    that dispatches to its row's on_click / on_long_click.

       auto_dismiss=True : pop the menu before running the row callback.
       dismiss_option=True (or auto_dismiss=False) : append a back-arrow row.
    """

    BACK_GLYPH = "\u2b05"
    CHECK_GLYPH = "\u2714 "

    def __init__(
        self,
        rows: list[MenuRow],
        title: str = "",
        font=None,
        max_width=None,
        max_height=None,
        text_halign=TextHAlign.CENTRE,
        auto_dismiss: bool = False,
        dismiss_option: bool = False,
        default_label: Optional[str] = None,
        **kwargs,
    ):
        self.rows: list[MenuRow] = list(rows)
        self.max_width = max_width
        self.max_height = max_height
        self.auto_dismiss = auto_dismiss
        if font is None:
            font = Config().get_font("default")
        assert font is not None, "Menu requires a font (none configured as 'default')"
        self.font = font
        self.font_metrics = font.getmetrics()
        self.text_halign = text_halign
        self.default_label = default_label
        self.item_h = 0

        if not auto_dismiss or dismiss_option:
            self.rows.append(MenuRow(self.BACK_GLYPH, on_click=self._dismiss))

        super().__init__(width=0, height=0, title=title, **kwargs)

        y = 0
        for row in self.rows:
            text = (self.CHECK_GLYPH + row.label) if row.active else row.label
            w = _MenuRowWidget(
                box=Box.xywh(0, y, self.box.width, self.item_h),
                text_halign=self.text_halign,
                font=row.font or self.font,
                text=text,
                parent=self,
                action=self._make_action(row),
            )
            w.row = row  # typed accessor for callers (e.g. selection tracking)
            self.add_sel_widget(w)
            if row.label == self.default_label:
                self.sel_widget(w)
            y += self.item_h

        self.refresh()

    def _make_action(self, row: MenuRow):
        def action(event, _widget):
            if event == InputEvent.LONG_CLICK and row.on_long_click is not None:
                if self.auto_dismiss:
                    self._dismiss()
                row.on_long_click()
                return
            if event in (InputEvent.CLICK, InputEvent.LONG_CLICK):
                cb = row.on_click
                if cb is None:
                    return
                if self.auto_dismiss:
                    self._dismiss()
                cb()

        return action

    def _dismiss(self):
        # Idempotent: the dismiss row's on_click is _dismiss itself, so under
        # auto_dismiss the wrapper pops first and the row callback is a no-op.
        stack = self._get_stack()
        if stack and self in stack.stack:
            stack.pop_panel(self)

    def _adjust_box(self):
        # Width fixed at 240; height = item_h * len(rows), bounded by max_height.
        # item_h derives from the first row's metrics (assumes uniform line height).
        #
        # TODO: Make margins configurable
        #
        # TODO: Re-adjust item widgets here instead of in constructor. Right
        # now we rely on the pass done in the constructor (without a parent)
        # because it calculates item_h which is then used to layout the menu
        # items. But we could just pile them on top of each other and move
        # them once attached.
        #
        w = 240
        h = 0
        h_margin = 10
        v_margin = 0
        for row in self.rows:
            text = (self.CHECK_GLYPH + row.label) if row.active else row.label
            font = row.font or self.font
            metrics = font.getmetrics() if font is not self.font else self.font_metrics
            tw, th = get_text_size(text, font, metrics)
            tw = tw + h_margin * 2
            th = th + v_margin * 2
            if h == 0:
                self.item_h = th
                h = th * len(self.rows)
        if self.max_height is not None and h > self.max_height:
            h = self.max_height
        self.box = Box.xywh(0, 0, w, h)
        super()._adjust_box()
