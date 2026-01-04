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

from rtmidi.midiconstants import CONTROL_CHANGE
from typing import Optional, Any

import common.util as util
import pistomp.controller as controller
import pistomp.encoder as encoder
from pistomp.handler import Handler
from pistomp.velocity_tracker import VelocityTracker, clamp
from pistomp.parameter_quantizer import ParameterQuantizer
from common.parameter import Parameter

import logging


class EncoderController(encoder.Encoder, controller.Controller):
    """Encoder with velocity tracking and parameter quantization."""

    def __init__(
        self,
        handler: Handler,
        d_pin: int,
        clk_pin: int,
        midi_CC: Optional[int],
        midi_channel: int,
        midiout: Any,
        type: Optional[str] = None,
        id: Optional[int] = None,
    ):
        super(EncoderController, self).__init__(
            d_pin=d_pin,
            clk_pin=clk_pin,
            callback=self.refresh,
            type=type,
            id=id,
            midi_CC=midi_CC,
            midi_channel=midi_channel,
        )
        self.handler = handler
        self.midiout = midiout
        self.quantizer: Optional[ParameterQuantizer] = None
        self.value_change_callback: Optional[Any] = None
        self.midi_value = 64  # Start at middle value for MIDI Learn
        self.velocity_tracker = VelocityTracker(step_scale=1)
        logging.debug(f"EncoderController init: id={id}, midi_CC={midi_CC}, midi_channel={midi_channel}")

    def bind_to_parameter(self, parameter: Parameter, taper: float = 1.0) -> None:
        """Initialize quantizer and sync to parameter's current value."""
        self.parameter = parameter
        num_steps = 128 if self.midi_CC else 256
        step_scale = num_steps / 256
        self.quantizer = ParameterQuantizer(parameter.minimum, parameter.maximum, num_steps, taper)
        self.quantizer.set_value(parameter.value)
        self.velocity_tracker.set_step_scale(step_scale)
        logging.debug(
            f"EncoderController bound to parameter {parameter.name}: "
            f"midi_CC={self.midi_CC}, num_steps={num_steps}, step_scale={step_scale}, value={parameter.value}"
        )

    def set_value(self, value: float) -> None:
        """Update quantizer position from parameter value."""
        if self.quantizer:
            self.quantizer.set_value(value)

    def refresh(self, direction: int) -> None:
        """Handle encoder rotation: calculate new value, send MIDI, notify handler."""
        logging.debug(f"EncoderController.refresh: id={self.id}, type={self.type}, direction={direction}, has_param={self.parameter is not None}")
        if abs(direction) > 1:
            delta = direction
        else:
            multiplier = self.velocity_tracker.add_rotation(direction)
            if self.quantizer and self.quantizer.taper != 1.0:
                multiplier = self._taper_adjusted_multiplier(multiplier, direction)
            delta = direction * multiplier

        if self.quantizer:
            new_value = self.quantizer.move_steps(delta)
            if self.midi_CC and self.parameter:
                # Only calculate MIDI value if we're going to send it and have a parameter
                self.midi_value = self._value_to_midi(new_value)
            if self.parameter:
                self.parameter.value = new_value
            logging.debug(f"Bound: steps={delta}, value={new_value}")
        else:
            self.midi_value = clamp(self.midi_value + delta, 0, 127)
            logging.debug(f"Unbound: delta={delta}, midi={self.midi_value}")

        if self.midi_CC:
            self.midiout.send_message([self.midi_channel | CONTROL_CHANGE, self.midi_CC, int(self.midi_value)])

        if self.quantizer:
            if self.value_change_callback:
                # Callback mode (blend mode or volume control)
                self.value_change_callback(new_value, self)
            elif self.parameter:
                # Parameter mode (plugin parameters)
                self.handler.encoder_value_changed(self.parameter, new_value)

    def _taper_adjusted_multiplier(self, multiplier: int, direction: int) -> int:
        """Scale multiplier to compensate for non-linear step sizes."""
        current_step = self.quantizer.current_step
        next_step = clamp(current_step + direction, 0, self.quantizer.num_steps - 1)

        current_value = self.quantizer.step_values[current_step]
        next_value = self.quantizer.step_values[next_step]
        step_size = abs(next_value - current_value)

        param_range = self.parameter.maximum - self.parameter.minimum
        linear_step_size = param_range / (self.quantizer.num_steps - 1)

        if step_size < 0.0001:
            return multiplier

        ratio = linear_step_size / step_size
        adjusted = int(multiplier * ratio)
        return max(1, adjusted)

    def _value_to_midi(self, value: float) -> int:
        """Convert parameter value to MIDI CC value [0-127]."""
        midi_value = util.renormalize(
            value, self.parameter.minimum, self.parameter.maximum, self.midi_min, self.midi_max
        )
        return int(clamp(midi_value, 0, 127))

    def get_normalized_value(self) -> float:
        """Get current value normalized to [0.0, 1.0] for blend mode."""
        if self.quantizer:
            return self.quantizer.get_normalized_position()
        return self.midi_value / 127.0

    def read_rotary(self):
        """Poll encoder state (called from hardware polling loop)."""
        super().read_rotary()

    def get_display_info(self) -> controller.AnalogDisplayInfo:
        """Get display information for LCD (analog-controls pattern)."""
        routing = self.get_routing_info()  # Inherited from Controller base class

        info: controller.AnalogDisplayInfo = {
            'type': self.type,
            'id': self.id,
            'category': None,  # Set during parameter binding
        }

        if routing.destination == controller.RoutingDestination.EXTERNAL:
            info['port_name'] = routing.port_name
            info['midi_cc'] = self.midi_CC

        return info
