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
import common.parameter as Parameter

import numpy as np
import threading
import traceback

class Parameterdialog(Dialog):
    def __init__(self, stack, parameter,
                 width, height, title, title_font=None, timeout=None, **kwargs):
        self._init_attrs(Widget.INH_ATTRS, kwargs)
        super(Parameterdialog,self).__init__(width, height, title, title_font, **kwargs)
        self.stack = stack  # TODO very LAME to require the stack to be passed, ideally panel would be able to pop itself
        self.parameter: Parameter = parameter
        
        # adjustment amount per click
        if self.parameter.type in (Parameter.Type.INTEGER, Parameter.Type.ENUMERATION, Parameter.Type.TOGGLED):
            self.parameter_tweak_amount = 1
        else:
            self.parameter_tweak_amount = 8

        self.tweak = util.renormalize_float(self.parameter_tweak_amount, 0, 127, self.parameter.minimum, self.parameter.maximum)

        self.timeout = timeout
        self.timer = None

        # "graph" are the y-scaled values, "actual" are the actual non-scaled values
        self.taper = self.parameter.get_taper()  # Derive from parameter type
        self.num_actual = 256  # High resolution for better stepping
        self.num_points = 60
        self.bar_width = 4
        self.actual_abscissa = np.linspace(0, self.num_actual, self.num_actual)
        self.graph_abscissa = np.linspace(1, self.num_points, self.num_points)
        self.actual_points = self._calc_graph_points(self.actual_abscissa, self.parameter.minimum, self.parameter.maximum)
        self.graph_points  = self._calc_graph_points(self.graph_abscissa, 0, self.num_points)  # TODO

        self.w_value = None
        self.w_bars = []  # Reusable bar widgets
        self._draw_contents()

    def _calc_graph_points(self, x, min, max):
        # Calculate the y-values using a logarithmic function
        points = min + (max - min) * ((x / len(x)) ** self.taper)
        return points

    def _draw_contents(self):
        # Always draw close button, even if using timeout autoclose
        b = TextWidget(box=Box.xywh(108, 100, 0, 0), text='Close', parent=self, outline=1, sel_width=3,
                       outline_radius=5, align=WidgetAlign.NONE, name='ok_btn')
        b.set_selected(True)
        self._draw_graph()

    def _draw_graph(self):
        # TODO detailed dimensions, colors, etc. should not be defined in uilib
        y0 = 80
        x_offset = 10

        val_text = self.parameter.format(self.parameter.value)
        min_text = self.parameter.format(self.parameter.minimum)
        max_text = self.parameter.format(self.parameter.maximum)

        # Calculate text width and centered position
        font = Config().get_font('default')
        text_width, text_height = get_text_size(val_text, font)
        x_centered = (self.box.width - text_width) // 2

        if self.w_value is None:
            self.w_value = TextWidget(box=Box.xywh(x_centered, 25, 0, 0), text=val_text, parent=self,
                       align=WidgetAlign.NONE, name='value')
            self.w_value.set_foreground('yellow')
            TextWidget(box=Box.xywh(0, y0, 0, 0), text=min_text, parent=self, outline=0,
                       align=WidgetAlign.NONE, name='value')
            TextWidget(box=Box.xywh(220, y0, 0, 0), text=max_text, parent=self, outline=0,
                       align=WidgetAlign.NONE, name='value')
        else:
            # Update text and reposition to keep centered
            self.w_value.set_text(val_text)
            # Manually update box position without breaking parent relationship
            self.w_value.box = Box.xywh(x_centered, 25, 0, 0)

        # Create bar widgets on first call, reuse them afterward
        if not self.w_bars:
            x = 0
            for i in self.graph_abscissa:
                i = int(i) - 1  # abscissa start at 1, arrays start at 0
                g = self.graph_points[i]
                line_box = Box.xywh(x + x_offset, y0 - g, self.bar_width, g)
                w = Widget(box=line_box, parent=self, outline=1, sel_width=0, outline_radius=0,
                           align=WidgetAlign.NONE)
                self.w_bars.append(w)
                x = x + self.bar_width

        # Just update colors (fast!)
        for idx, i in enumerate(self.graph_abscissa):
            i = int(i) - 1
            a = int(i * self.num_actual / self.num_points)
            p = self.actual_points[a]
            g = self.graph_points[i]
            line_box = Box.xywh(x + x_offset, y0 - g, self.bar_width, g)
            w = Widget(box=line_box, parent=self, outline=1, sel_width=0, outline_radius=0,
                       align=WidgetAlign.NONE)
            if p <= self.parameter.value:
                w.set_foreground('yellow')
            else:
                self.w_bars[idx].set_foreground((100, 100, 240))

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
        self.parameter.value = new_value
        self._draw_graph()

    def parameter_value_change(self, direction):
        self._reset_timeout_timer()

        # Calculate new value
        new_value = self.parameter.value + (direction * self.tweak)

        # Clamp
        if new_value > self.parameter.maximum:
            new_value = self.parameter.maximum
        if new_value < self.parameter.minimum:
            new_value = self.parameter.minimum

        # Integer rounding
        if self.parameter.type in (Parameter.Type.INTEGER, Parameter.Type.ENUMERATION, Parameter.Type.TOGGLED):
            new_value = round(new_value)

        if new_value == self.parameter.value:
            return

        self.parameter.value = new_value
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

        param_range = self.parameter.maximum - self.parameter.minimum
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
