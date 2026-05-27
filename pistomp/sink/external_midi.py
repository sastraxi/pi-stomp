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
ExternalMidiSink: emits CC to external MIDI ports for controllers whose
``hardware.external_port_name(controller)`` resolves. Consumes the event on
successful send so the virtual-port MidiOutSink underneath is skipped. On
failure (port closed / disconnected) it does *not* consume — the event falls
through to MidiOutSink, which mirrors today's ExternalMidiOut fallback
behavior structurally via the sink stack rather than inline branching.

Reads ``controller.midi_value`` directly — ParameterUpdateSink is the sole
authority for that value.
"""

from __future__ import annotations

from modalapi.external_midi import ExternalMidiManager
from pistomp.hardware import Hardware
from pistomp.input_router import AnalogEvent, EncoderEvent, InputSink


class ExternalMidiSink(InputSink):
    def __init__(self, manager: ExternalMidiManager, hardware: Hardware):
        self.manager = manager
        self.hardware = hardware

    def _emit(self, event) -> None:
        controller = event.controller
        if controller.midi_CC is None:
            return
        port_name = self.hardware.external_port_name(controller)
        if port_name is None:
            return
        success = self.manager.send_cc(
            port_name, controller.midi_channel, controller.midi_CC, int(controller.midi_value)
        )
        if success:
            event.consumed = True
        # On failure, leave event un-consumed so MidiOutSink below emits to the
        # virtual port — preserves today's ExternalMidiOut fallback.

    def on_encoder(self, event: EncoderEvent) -> None:
        self._emit(event)

    def on_analog(self, event: AnalogEvent) -> None:
        self._emit(event)
