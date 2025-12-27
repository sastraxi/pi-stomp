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
    """Manages expression pedal callback registration for collage mode."""

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
        self.controlled_pedal: Any = None  # AnalogMidiControl

    def attach_to_pedal(self, analog_controls: list[Any], pedal_id: int) -> None:
        """
        Attach collage mode callback to expression pedal.

        Sets the value_change_callback on the AnalogMidiControl to intercept
        value changes and route them through collage mode handlers.

        Args:
            analog_controls: List of analog controls from hardware
            pedal_id: Expression pedal ID to control

        Raises:
            ValueError: If pedal not found
        """
        # Find expression pedal control
        for control in analog_controls:
            if hasattr(control, 'id') and control.id == pedal_id:
                self.controlled_pedal = control
                control.value_change_callback = self.handle_value_change
                logging.info(f"Attached collage mode to expression pedal {pedal_id} ({self.mode} mode)")
                return

        raise ValueError(f"Expression pedal {pedal_id} not found")

    def detach_from_pedal(self) -> None:
        """Remove collage mode callback from expression pedal."""
        if self.controlled_pedal:
            self.controlled_pedal.value_change_callback = None
            logging.debug("Detached collage mode from expression pedal")
            self.controlled_pedal = None

    def handle_value_change(self, raw_value: int, control: Any) -> None:
        """
        Callback for AnalogMidiControl value changes.

        Called by AnalogMidiControl.refresh() when the pedal value changes.
        Routes the change through the appropriate mode handler.

        Args:
            raw_value: Raw ADC value (0-1023)
            control: The AnalogMidiControl instance
        """
        # Convert ADC value to percentage (0.0-1.0)
        percentage = raw_value / 1023.0  # ADC is 10-bit (0-1023)

        logging.debug(f"Pedal moved: raw={raw_value}, pct={percentage:.3f}")

        # Delegate to mode handler
        if self.mode == 'segment':
            # Segment mode needs exp_channel and exp_cc
            exp_channel = control.midi_channel
            exp_cc = control.midi_CC
            logging.debug(f"Calling segment mode handler: ch={exp_channel}, cc={exp_cc}")
            self.mode_handler.handle_pedal_change(percentage, exp_channel, exp_cc, self.midiout)
        elif self.mode == 'parameter':
            # Parameter mode only needs percentage
            self.mode_handler.handle_pedal_change(percentage, self.midiout)
