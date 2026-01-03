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

from uilib.dialog import *
from uilib.text import *
import common.util as util

import numpy as np
import threading
import traceback

class Parameterdialog(Dialog):
    def __init__(self, stack, param_name, param_value, param_min, param_max,
                 width, height, title, title_font=None, timeout=None, taper=1, **kwargs):
        self._init_attrs(Widget.INH_ATTRS, kwargs)
        super(Parameterdialog,self).__init__(width, height, title, title_font, **kwargs)
        self.stack = stack  # TODO very LAME to require the stack to be passed, ideally panel would be able to pop itself
        self.param_name = param_name
        self.param_value = param_value
        self.param_min = param_min
        self.param_max = param_max

        # adjustment amount per click
        self.parameter_tweak_amount = 8
        self.tweak = util.renormalize_float(self.parameter_tweak_amount, 0, 127, self.param_min, self.param_max)

        self.timeout = timeout
        self.timer = None

        # "graph" are the y-scaled values, "actual" are the actual non-scaled values
        self.taper = taper  # 1 linear, 2 or 3 good for logarithmic
        self.num_actual = 256
        self.num_points = 60
        self.bar_width = 4
        self.actual_abscissa = np.linspace(0, self.num_actual, self.num_actual)
        self.graph_abscissa = np.linspace(1, self.num_points, self.num_points)
        self.actual_points = self._calc_graph_points(self.actual_abscissa, self.param_min, self.param_max)
        self.graph_points  = self._calc_graph_points(self.graph_abscissa, 0, self.num_points)  # TODO

        self.w_value = None
        self._draw_contents()

    def _calc_graph_points(self, x, min, max):
        # Calculate the y-values using a logarithmic function
        points = min + (max - min) * ((x / len(x)) ** self.taper)
        return points

    def _draw_contents(self):
        if self.timeout is None:
            # Only draw close button if not using timeout autoclose
            b = TextWidget(box=Box.xywh(108, 100, 0, 0), text='Close', parent=self, outline=1, sel_width=3,
                           outline_radius=5, align=WidgetAlign.NONE, name='ok_btn')
            b.set_selected(True)
        self._draw_graph()

    def _draw_graph(self):
        # Use actual box dimensions for balanced margins
        margin = 10
        graph_width = self.num_points * self.bar_width
        content_height = self.box.height

        # Position graph baseline with room for labels below
        y0 = content_height - margin - 18  # 18px for label height + spacing
        x_offset = margin

        val_text = util.format_float(self.param_value)
        if self.w_value is None:
            # Center value label horizontally
            self.w_value = TextWidget(box=Box.xywh(self.box.width // 2 - 15, 15, 0, 0), text=val_text, parent=self,
                       align=WidgetAlign.NONE, name='value')
            self.w_value.set_foreground('yellow')
            # Min label at left edge with margin
            TextWidget(box=Box.xywh(margin, y0, 0, 0), text=util.format_float(self.param_min), parent=self, outline=0,
                       align=WidgetAlign.NONE, name='value')
            # Max label at right edge with margin
            TextWidget(box=Box.xywh(self.box.width - margin - 30, y0, 0, 0), text=util.format_float(self.param_max), parent=self, outline=0,
                       align=WidgetAlign.NONE, name='value')
        else:
            self.w_value.set_text(val_text)

        # TODO would be nice to only redraw the lines that need changing
        x = 0
        for i in self.graph_abscissa:
            i = int(i) - 1  # abscissa start at 1, arrays start at 0
            a = int(i * self.num_actual / self.num_points)
            p = self.actual_points[a]
            g = self.graph_points[i]
            line_box = Box.xywh(x + x_offset, y0 - g, self.bar_width, g)
            w = Widget(box=line_box, parent=self, outline=1, sel_width=0, outline_radius=0,
                       align=WidgetAlign.NONE)
            if p <= self.param_value:
                w.set_foreground('yellow')
            else:
                w.set_foreground((100, 100, 240))
            x = x + self.bar_width

        self.refresh()

    def _reset_timeout_timer(self):
        if self.timeout is not None:
            if self.timer is not None:
                self.timer.cancel()
            self.timer = threading.Timer(self.timeout, self.pop)
            self.timer.start()

    def update_value(self, new_value: float) -> None:
        """Update display with new value (controller already calculated it)."""
        self._reset_timeout_timer()
        self.param_value = new_value
        self._draw_graph()

    def parameter_value_change(self, steps):
        self._reset_timeout_timer()

        value = float(self.param_value)
        i = self._find_nearest_element_index(self.actual_points, value)

        if self.taper != 1.0 and abs(steps) > 1:
            steps = self._taper_adjusted_steps(i, steps)

        new = np.clip(i + steps, 0, self.num_actual - 1)
        new_value = self.actual_points[new]

        if new_value > self.param_max:
            new_value = self.param_max
        if new_value < self.param_min:
            new_value = self.param_min
        if new_value == value:
            return
        self.param_value = new_value
        if self.action is not None:
            self.action(self.object, new_value)
        self._draw_graph()

    def _taper_adjusted_steps(self, current_index, steps):
        """Scale step count to compensate for non-linear step sizes."""
        direction = 1 if steps > 0 else -1
        next_index = np.clip(current_index + direction, 0, self.num_actual - 1)

        current_value = self.actual_points[current_index]
        next_value = self.actual_points[next_index]
        step_size = abs(next_value - current_value)

        param_range = self.param_max - self.param_min
        linear_step_size = param_range / (self.num_actual - 1)

        if step_size < 0.0001:
            return steps

        ratio = linear_step_size / step_size
        adjusted = int(abs(steps) * ratio)
        return max(1, adjusted) * direction

    def input_event(self, event):
        if event == InputEvent.CLICK:
            self.pop()
        elif event == InputEvent.LEFT:
            self.parameter_value_change(-1)
        elif event == InputEvent.RIGHT:
            self.parameter_value_change(1)

    def pop(self):
        self.stack.pop_panel(self)
        if self.timer is not None:
            self.timer.cancel()
            self.timer = None

    def _find_nearest_element_index(self, arr, target):
        # binary search of closest value to target within the sorted array
        left = 0
        right = len(arr) - 1
        nearest_index = None
        min_diff = float('inf')

        while left <= right:
            mid = (left + right) // 2
            diff = abs(arr[mid] - target)

            if diff < min_diff:
                min_diff = diff
                nearest_index = mid

            if arr[mid] == target:
                return mid
            elif arr[mid] < target:
                left = mid + 1
            else:
                right = mid - 1

        return nearest_index
