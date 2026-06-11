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

import time

from uilib.box import Box
from uilib.widget import Widget


class FootswitchWidget(Widget):
    TAP_COLOR = (255, 180, 0)
    TAP_DIM_COLOR = (110, 78, 0)
    TAP_WIDTH = 60  # legacy draw width, see _draw offsets
    TAP_HEIGHT = 64  # full footswitch panel height

    def __init__(self, box, font, label, color, is_bypassed, taptempo=None, tap_font=None, **kwargs):
        self._init_attrs(Widget.INH_ATTRS, kwargs)
        super(FootswitchWidget, self).__init__(box, **kwargs)
        self.font = font
        self.label = label
        self.color = color
        self.is_bypassed = is_bypassed
        self.draw = None
        self.footswitch_ring_width = 7
        self.background = (0, 0, 0)  # TODO get palette from parent?
        self.foreground = (255, 255, 255)
        self.color_plugin_bypassed = (80, 80, 80)
        self.taptempo = taptempo
        self.tap_font = tap_font if tap_font is not None else font
        self._pulse_on = True
        self._tap_drawn = False

    def _tap_active(self):
        return self.taptempo is not None and self.taptempo.is_enabled()

    def _tap_box(self):
        # Tap art spans the full panel height; the nominal widget box doesn't
        # cover it (legacy draws below its box too).
        assert self.box is not None, "_tap_box() requires self.box to be set"
        return Box(self.box.x0, 0, self.box.x0 + self.TAP_WIDTH, self.TAP_HEIGHT)

    def _draw(self, image, draw, real_box):
        self.xy1 = (real_box.x0, real_box.y0)
        self.xy2 = (real_box.x0 + 60, real_box.y0 + 40)  # TODO should these offsets be here?
        self.draw = draw

        if self._tap_active():
            self._draw_tap(draw, real_box)
            self._tap_drawn = True
            return
        self._tap_drawn = False

        # halo
        self._draw_halo()

        # cap bottom
        fx1 = self.xy1[0] + 10
        fy1 = self.xy2[1] - 34
        fx2 = self.xy2[0] - 10
        fy2 = fy1 + 16
        draw.ellipse(((fx1, fy1), (fx2, fy2)), fill=self.background, outline="gray", width=2)

        # cap top
        fy1 -= 6
        fy2 -= 6
        draw.ellipse(((fx1, fy1), (fx2, fy2)), fill=self.background, outline="gray", width=2)

        # label
        draw.text((self.xy1[0], self.xy2[1]), self.label, self.foreground, self.font)

    def _draw_halo(self):
        assert self.draw is not None, "halo must be drawn within _draw()"
        hx1 = self.xy1[0] + 2
        hy1 = self.xy1[1] + 10
        hx2 = self.xy2[0] - 2
        hy2 = self.xy2[1] - 2
        color = self.color_plugin_bypassed if self.is_bypassed else self.color
        self.draw.ellipse(((hx1, hy1), (hx2, hy2)), fill=None, outline=color, width=self.footswitch_ring_width)

    #
    # Tap-tempo takeover: rounded frame pulsing on the beat, "TAP" header
    # and the current BPM in large digits.
    #
    def _draw_tap(self, draw, real_box):
        assert self.taptempo is not None, "_draw_tap() requires self.taptempo to be set"
        x0 = real_box.x0
        y0 = real_box.y0
        color = self.TAP_COLOR if self._pulse_on else self.TAP_DIM_COLOR
        draw.rounded_rectangle(
            ((x0 + 1, y0 + 1), (x0 + self.TAP_WIDTH - 2, y0 + self.TAP_HEIGHT - 3)), radius=8, outline=color, width=3
        )
        self._draw_tap_text(draw, x0, y0 + 5, "TAP", self.font, color)
        bpm = self.taptempo.get_bpm()
        digits = str(round(bpm)) if bpm else "--"
        self._draw_tap_text(draw, x0, y0 + 29, digits, self.tap_font, self.foreground)

    def _draw_tap_text(self, draw, x0, y, text, font, color):
        bb = font.getbbox(text)
        x = x0 + (self.TAP_WIDTH - (bb[2] - bb[0])) // 2 - bb[0]
        draw.text((x, y), text, color, font)

    def tick(self):
        """Blink the tap frame at the current tempo, phase-locked to the last tap."""
        if not self._tap_active():
            return
        assert self.taptempo is not None  # narrowing; implied by _tap_active()
        bpm = self.taptempo.get_bpm()
        if not bpm:
            return
        period = 60.0 / bpm
        phase = (time.monotonic() - self.taptempo.anchor) % period
        on = phase < period / 2
        if on != self._pulse_on:
            self._pulse_on = on
            self.refresh(self._tap_box())

    def toggle(self, is_bypassed):
        self.is_bypassed = is_bypassed
        if self._tap_active() or self._tap_drawn:
            self.refresh(self._tap_box())
        else:
            self._draw_halo()
