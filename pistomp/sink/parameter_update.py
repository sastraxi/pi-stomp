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

"""
ParameterUpdateSink: the single authority for translating raw events into
controller state. Advances the encoder quantizer, stashes the canonical
``midi_value`` on the controller, and writes ``parameter.value`` when the
controller is bound. Downstream sinks (MidiOutSink, ExternalMidiSink, ModSink)
read these fields and never compute them independently.
"""

from __future__ import annotations

from pistomp.analogmidicontrol import as_midi_value
from pistomp.input_router import AnalogEvent, EncoderEvent, InputSink


class ParameterUpdateSink(InputSink):
    def on_encoder(self, event: EncoderEvent) -> None:
        controller = event.controller
        delta = int(round(event.rotations * event.multiplier))
        # Encoder owns the quantizer (step_values, current_step) — see encoder
        # collapse in step 3 of the migration. ``_move_steps`` returns the new
        # parameter value at the new quantized position.
        new_value = controller._move_steps(delta)
        controller.midi_value = controller._value_to_midi(new_value)
        if controller.parameter is not None:
            controller.parameter.value = new_value

    def on_analog(self, event: AnalogEvent) -> None:
        # Analog controls don't write parameter.value in the dispatch path —
        # the parameter is bound for display only (see modhandler external
        # parameter creation). MIDI value is computed once here and cached on
        # the controller for downstream emit sinks.
        from pistomp.analogmidicontrol import as_midi_value

        controller = event.controller
        controller.last_read = event.raw_value
        controller.value = event.raw_value
        controller.midi_value = as_midi_value(event.raw_value)
