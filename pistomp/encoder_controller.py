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
from pistomp.parameter_quantizer import ParameterQuantizer
from common.parameter import Parameter

import logging


def clamp(value, min_value, max_value):
    return max(min_value, min(max_value, value))


class EncoderController(encoder.Encoder, controller.Controller):
    """Encoder with speed-based amplification and parameter quantization."""

    # Speed thresholds (accumulated rotations between poll cycles)
    FAST_THRESHOLD = 4      # 4+ rotations = very fast
    MEDIUM_THRESHOLD = 2    # 2-3 rotations = fast

    # Multipliers
    FAST_MULTIPLIER = 8
    MEDIUM_MULTIPLIER = 4
    SLOW_MULTIPLIER = 1

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
        self.value_change_callback: Optional[Any] = None

        # default quantizer / value is for MIDI CC (0-127) if unbound
        self.quantizer = ParameterQuantizer(0, 127, 128, 1.0)
        self.quantizer.set_value(64)  # Start at middle value for MIDI Learn
        self.midi_value = int(self.quantizer.get_value())

        logging.debug(f"EncoderController init: id={id}, midi_CC={midi_CC}, midi_channel={midi_channel}")

    def bind_to_parameter(self, parameter: Parameter, taper: float = 1.0) -> None:
        """Initialize quantizer and sync to parameter's current value."""
        self.parameter = parameter
        num_steps = 128 if self.midi_CC else 256
        self.quantizer = ParameterQuantizer(parameter.minimum, parameter.maximum, num_steps, taper)
        self.quantizer.set_value(parameter.value)
        logging.debug(
            f"EncoderController bound to parameter {parameter.name}: "
            f"midi_CC={self.midi_CC}, num_steps={num_steps}, value={parameter.value}"
        )

    def set_value(self, value: float) -> None:
        """Update quantizer position from parameter value."""
        if self.quantizer:
            self.quantizer.set_value(value)

    def refresh(self, direction: int) -> None:
        """Handle encoder rotation with speed-based amplification."""
        logging.debug(f"EncoderController.refresh: id={self.id}, type={self.type}, direction={direction}, has_param={self.parameter is not None}")

        # Use accumulated count as speed indicator (accumulated in 10ms poll cycle)
        abs_dir = abs(direction)
        if abs_dir >= self.FAST_THRESHOLD:
            multiplier = self.FAST_MULTIPLIER
        elif abs_dir >= self.MEDIUM_THRESHOLD:
            multiplier = self.MEDIUM_MULTIPLIER
        else:
            multiplier = self.SLOW_MULTIPLIER

        delta = direction * multiplier
        new_value = self.quantizer.move_steps(delta)
        logging.debug(f"Speed: abs_dir={abs_dir}, multiplier={multiplier}, delta={delta}")
            
        self.midi_value = self._value_to_midi(new_value)
        if self.parameter:
            self.parameter.value = new_value
            
        logging.debug(f"Encoder refresh: steps={delta}, value={new_value}, midi={self.midi_value}")

        if self.midi_CC:
            self.midiout.send_message([self.midi_channel | CONTROL_CHANGE, self.midi_CC, int(self.midi_value)])

        if self.value_change_callback:
            self.value_change_callback(new_value, self)
        elif self.parameter:
            self.handler.encoder_value_changed(self.parameter, new_value)

    def _value_to_midi(self, value: float) -> int:
        """Convert parameter value to MIDI CC value [0-127]."""
        if self.parameter is None:
            midi_value = value
        else:
            midi_value = util.renormalize(
                value, self.parameter.minimum, self.parameter.maximum, self.midi_min, self.midi_max
            )
        return int(clamp(midi_value, 0, 127))

    def get_normalized_value(self) -> float:
        """Get current value normalized to [0.0, 1.0]."""
        return self.quantizer.get_normalized_position()

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
