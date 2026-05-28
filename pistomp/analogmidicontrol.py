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

from typing import Any

from rtmidi import MidiOut
from rtmidi.midiconstants import CONTROL_CHANGE

import common.util as util
import pistomp.analogcontrol as analogcontrol
import pistomp.controller as controller
from pistomp.controller import AnalogDisplayInfo
from pistomp.input.event import AnalogEvent

import logging


def as_midi_value(adc_value: int):
    """Convert a 10-bit ADC value (0-1023) to a MIDI value (0-127)."""
    return util.renormalize(adc_value, 0, 1023, 0, 127)


class AnalogMidiControl(analogcontrol.AnalogControl, controller.Controller):
    def __init__(self, spi, adc_channel, tolerance, midi_CC, midi_channel, midiout: MidiOut, type, id=None, cfg=None, autosync=False):
        super(AnalogMidiControl, self).__init__(spi, adc_channel, tolerance)
        controller.Controller.__init__(self, midi_channel, midi_CC)
        self.midiout = midiout
        self.autosync = autosync

        self.type = type
        self.id = id
        self.last_read = 0
        self.value = None
        self.cfg: dict[str, Any] = cfg or {}

    def set_midi_channel(self, midi_channel):
        self.midi_channel = midi_channel

    def set_value(self, value):
        self.value = value

    def _clamp_endpoints(self, value: int) -> int:
        if value <= self.tolerance:
            return 0
        if value >= 1023 - self.tolerance:
            return 1023
        return value

    def _send_value(self, value):
        """Dispatch via sink (v3) or take the legacy inline MIDI path (v1).

        v1 path: ``sink is None`` → emit CC directly to ``self.midiout`` (which
        may be an ``ExternalMidiOut`` wrapper for routed controls). This is the
        whole v1 behavior; nothing else needs to happen.

        v3 path: ``sink is not None`` → stash midi_value on self, build an
        ``AnalogEvent``, hand it to the sink. The handler does all of the work
        (volume routing, external MIDI, virtual emit) inside ``handle``."""
        midi_value = as_midi_value(value)
        self.midi_value = midi_value
        self.value = value
        self.last_read = value

        if self.sink is None:
            cc = [self.midi_channel | CONTROL_CHANGE, self.midi_CC, midi_value]
            logging.debug("AnalogControl Sending CC event %s" % cc)
            self.midiout.send_message(cc)
            return

        self.sink.handle(AnalogEvent(
            controller=self,
            raw_value=value,
            midi_value=midi_value,
        ))

    def send_current_value(self):
        """Force-send the current ADC value unconditionally. Used by sync_analog_controls()."""
        value = self._clamp_endpoints(self.readChannel())
        self._send_value(value)

    def initialize(self):
        if not self.autosync:
            return
        self.send_current_value()

    def refresh(self):
        value = self._clamp_endpoints(self.readChannel())
        if abs(value - self.last_read) > self.tolerance:
            self._send_value(value)

    def get_normalized_value(self) -> float:
        return self.last_read / 1023.0

    def get_display_info(self) -> AnalogDisplayInfo:
        info: AnalogDisplayInfo = {'type': self.type, 'category': None}
        if self.id is not None:
            info['id'] = self.id
        return info
