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
MidiOutSink: emits CC to the virtual ALSA MIDI Through port. Sits at the
bottom of the standard pipeline and emits whenever it's reached — i.e. either
the controller is VIRTUAL-routed (ExternalMidiSink early-returned without
consuming) or it's EXTERNAL-routed and the external send failed (fallback).
Does not consume.

Reads ``controller.midi_value`` directly — ParameterUpdateSink is the sole
authority for that value.
"""

from __future__ import annotations

import logging

from rtmidi import MidiOut
from rtmidi.midiconstants import CONTROL_CHANGE

from pistomp.input_router import AnalogEvent, EncoderEvent, InputSink


class MidiOutSink(InputSink):
    def __init__(self, midiout: MidiOut):
        self.midiout = midiout

    def _emit(self, controller) -> None:
        if controller.midi_CC is None:
            return
        message = [controller.midi_channel | CONTROL_CHANGE, controller.midi_CC, int(controller.midi_value)]
        self.midiout.send_message(message)
        logging.debug(f"MidiOutSink sent CC: {message}")

    def on_encoder(self, event: EncoderEvent) -> None:
        self._emit(event.controller)

    def on_analog(self, event: AnalogEvent) -> None:
        self._emit(event.controller)
