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

from uilib.config import Config
from uilib.widget import Widget


class FootswitchWidget(Widget):
    """Minimal footswitch indicator: a colored top bar with a centered label.

    Accent color is the configured footswitch color when ON, or DIMMED_BG when
    OFF. Unbound slots show "FS{n}" as a placeholder.
    """

    DIMMED_BG = (90, 90, 90)  # #5a5a5a
    DEFAULT_COLOR = (255, 255, 255)
    BAR_BORDER = (58, 58, 58)  # #3a3a3a — 1px line below the bar

    BAR_H = 3
    BAR_TO_LABEL = 1

    def __init__(self, box, num, label, color, is_bypassed, **kwargs):
        self._init_attrs(Widget.INH_ATTRS, kwargs)
        super(FootswitchWidget, self).__init__(box, **kwargs)
        self.font = Config().get_font("footswitch")
        self.num = num
        self.label = label
        self.color = color
        self.is_bypassed = is_bypassed

    def _draw(self, image, draw, real_box):
        x0, y0 = real_box.x0, real_box.y0
        w, h = real_box.width, real_box.height

        is_on = not self.is_bypassed
        accent = (self.color if self.color is not None else self.DEFAULT_COLOR) if is_on else self.DIMMED_BG

        # Top bar — full widget width with a 1px dark divider beneath it.
        draw.rectangle([(x0, y0), (x0 + w - 1, y0 + self.BAR_H - 1)], fill=accent)
        draw.rectangle([(x0, y0 + self.BAR_H), (x0 + w - 1, y0 + self.BAR_H)], fill=self.BAR_BORDER)

        assert self.font
        text = self.label if self.label else chr(ord("A") + self.num)
        bbox = self.font.getbbox(text)
        tw = bbox[2] - bbox[0]
        th = bbox[3] - bbox[1]

        label_area_top = y0 + self.BAR_H + 1 + self.BAR_TO_LABEL
        label_area_h = h - (self.BAR_H + 1 + self.BAR_TO_LABEL)
        tx = x0 + (w - tw) // 2 - bbox[0]
        ty = label_area_top + (label_area_h - th) // 2 - bbox[1]
        draw.text((tx, ty), text, fill=accent, font=self.font)

    def toggle(self, is_bypassed):
        self.is_bypassed = is_bypassed
