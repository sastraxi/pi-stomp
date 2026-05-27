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

from __future__ import annotations

import json
import logging
from typing import TypedDict
from common.parameter import Parameter
from rtmidi import MidiOut


class AnalogDisplayInfo(TypedDict, total=False):
    """Display information for analog controls and encoders."""

    type: str  # Token.KNOB, Token.EXPRESSION, Token.VOLUME
    id: int  # Position on screen (0-based from left)
    category: str | None  # Plugin category (for color coding) or None
    port_name: str | None  # External port name if routed externally
    midi_cc: int | None  # MIDI CC for external routing display


class FootswitchDisplayInfo(TypedDict, total=False):
    """Display information for footswitches."""

    id: int
    label: str | None
    color: tuple[int, int, int] | None  # RGB
    category: str | None


class Controller:
    midiout: MidiOut

    def __init__(self, midi_channel: int, midi_CC: int | None):
        self.midi_channel: int = midi_channel
        self.midi_CC: int | None = midi_CC
        self.parameter: Parameter | None = None
        self.hardware_name: str | None = None
        # type is not declared here — it conflicts with encoder.Encoder.type in EncoderController's MRO.
        # Subclasses that carry type must declare it themselves.
        self.midi_min: int = 0
        self.midi_max: int = 127
        self.midi_value: int = 0

    def to_json(self) -> str:
        return json.dumps(self, default=lambda o: o.__dict__, sort_keys=True, indent=4)

    def set_value(self, value: float) -> None:
        logging.error(f"Controller subclass ({self.__class__.__name__}) hasn't overriden the set_value method")

    def bind_to_parameter(self, parameter: Parameter) -> None:
        self.parameter = parameter
        self.set_value(parameter.value)

    def get_display_info(self) -> AnalogDisplayInfo:
        """Hardware-intrinsic display info. Routing-derived fields (port_name,
        midi_cc for external) are augmented by the caller, which can see the
        routing registry."""
        return {}
