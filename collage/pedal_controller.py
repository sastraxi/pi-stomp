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

"""Expression pedal hijacking and control for collage mode."""

import logging
from typing import Any, Literal


class PedalController:
    """Manages expression pedal hijacking and refresh handling."""

    def __init__(
        self,
        mode: Literal['segment', 'parameter'],
        mode_handler: Any,  # SegmentMode or ParameterMode
        midiout: Any
    ) -> None:
        """
        Initialize pedal controller.

        Args:
            mode: Operating mode ('segment' or 'parameter')
            mode_handler: SegmentMode or ParameterMode instance
            midiout: MIDI output object
        """
        self.mode = mode
        self.mode_handler = mode_handler
        self.midiout = midiout
        self.hijacked_control: Any = None  # AnalogMidiControl
        self.original_refresh: Any = None  # Original refresh method

    def hijack_pedal(self, analog_controls: list[Any], pedal_id: int) -> None:
        """
        Hijack expression pedal refresh() method.

        Stores original refresh method and replaces it with hijacked_refresh.

        Args:
            analog_controls: List of analog controls from hardware
            pedal_id: Expression pedal ID to hijack

        Raises:
            ValueError: If pedal not found
        """
        # Find expression pedal control
        for control in analog_controls:
            if hasattr(control, 'id') and control.id == pedal_id:
                self.hijacked_control = control
                self.original_refresh = control.refresh
                control.refresh = self.hijacked_refresh
                logging.info(f"Hijacked expression pedal {pedal_id} for {self.mode} mode")
                return

        raise ValueError(f"Expression pedal {pedal_id} not found for hijacking")

    def restore_pedal(self) -> None:
        """Restore original refresh method to hijacked control."""
        if hasattr(self, 'hijacked_control') and hasattr(self, 'original_refresh'):
            if self.hijacked_control and self.original_refresh:
                self.hijacked_control.refresh = self.original_refresh
                logging.debug("Restored expression pedal refresh method")

    def hijacked_refresh(self) -> None:
        """
        Replacement for AnalogMidiControl.refresh().

        Reads ADC value, checks for changes, and delegates to mode handler.
        Does NOT call original refresh - we send transformed MIDI ourselves.
        """
        # Read raw ADC value (but don't send MIDI yet)
        raw_value = self.hijacked_control.readChannel()
        value_changed = abs(raw_value - self.hijacked_control.last_read) > self.hijacked_control.tolerance

        if not value_changed:
            return

        # Convert ADC value to percentage (0.0-1.0)
        percentage = raw_value / 1023.0  # ADC is 10-bit (0-1023)

        # Delegate to mode handler
        if self.mode == 'segment':
            # Segment mode needs exp_channel and exp_cc
            exp_channel = self.hijacked_control.midi_channel
            exp_cc = self.hijacked_control.midi_CC
            self.mode_handler.handle_pedal_change(percentage, exp_channel, exp_cc, self.midiout)
        elif self.mode == 'parameter':
            # Parameter mode only needs percentage
            self.mode_handler.handle_pedal_change(percentage, self.midiout)

        # Update last_read to prevent duplicate sends
        self.hijacked_control.last_read = raw_value
