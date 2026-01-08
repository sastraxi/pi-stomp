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

import adafruit_mcp3xxx.mcp3008 as MCP
from adafruit_mcp3xxx.analog_in import AnalogIn

from rtmidi.midiutil import open_midioutput
from rtmidi.midiconstants import CONTROL_CHANGE

import common.util as util
import pistomp.analogcontrol as analogcontrol

import logging


def as_midi_value(adc_value: int):
    """Convert a 10-bit ADC value (0-1023) to a MIDI value (0-127)."""
    return util.renormalize(adc_value, 0, 1023, 0, 127)


class AnalogMidiControl(analogcontrol.AnalogControl):
    def __init__(self, spi, adc_channel, tolerance, midi_CC, midi_channel, midiout, type, id=None, cfg={}, value_change_callback=None):
        super(AnalogMidiControl, self).__init__(spi, adc_channel, tolerance)
        self.midi_CC = midi_CC
        self.midiout = midiout
        self.midi_channel = midi_channel

        # Parent member overrides
        self.type = type
        self.id = id
        self.last_read = 0          # this keeps track of the last potentiometer value
        self.value = None
        self.cfg = cfg
        self.value_change_callback = value_change_callback

    def set_midi_channel(self, midi_channel):
        self.midi_channel = midi_channel

    def set_value(self, value):
        self.value = value

    def _send_value(self, value):
        """
        Route value to MIDI and/or callback based on current configuration.

        Args:
            value: Raw ADC value (0-1023)
        """
        # Always convert to MIDI and send
        set_volume = as_midi_value(value)
        cc = [self.midi_channel | CONTROL_CHANGE, self.midi_CC, set_volume]
        logging.debug("AnalogControl Sending CC event %s" % cc)
        self.midiout.send_message(cc)

        if self.value_change_callback:
            # Also delegate to callback (e.g., blend mode interpolation)
            self.value_change_callback(value, self)

    def send_current_value(self):
        """
        Force-send the current analog control value.
        Used for syncing state during pedalboard load or blend mode activation.
        Routes via callback if registered, otherwise sends MIDI.
        """
        # read the analog pin
        value = self.readChannel()

        # Route to callback or MIDI
        self._send_value(value)

        # save the reading to prevent duplicate sends on next poll
        self.last_read = value

    # Override of base class method
    def refresh(self):
        # read the analog pin
        value = self.readChannel()

        # how much has it changed since the last read?
        pot_adjust = abs(value - self.last_read)
        value_changed = (pot_adjust > self.tolerance)

        if value_changed:
            # Route to callback or MIDI
            self._send_value(value)

            # save the potentiometer reading for the next loop
            self.last_read = value
