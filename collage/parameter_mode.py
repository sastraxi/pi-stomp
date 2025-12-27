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

"""Parameter mode handler for collage mode."""

import logging
from typing import Any

from rtmidi.midiconstants import CONTROL_CHANGE

from collage.stop import CollageStop
from collage.types import InterpolationFunc


class ParameterMode:
    """Handles full parameter interpolation with virtual CCs."""

    def __init__(
        self,
        stops: list[CollageStop],
        interpolation_func: InterpolationFunc,
        virtual_cc_mappings: dict[str, int],
        virtual_midi_channel: int
    ) -> None:
        """
        Initialize parameter mode handler.

        Args:
            stops: List of CollageStop objects (sorted by position)
            interpolation_func: Interpolation function to use
            virtual_cc_mappings: Dict mapping "instance_id:symbol" -> CC number
            virtual_midi_channel: MIDI channel for virtual CCs
        """
        self.stops = stops
        self.interpolation_func = interpolation_func
        self.virtual_cc_mappings = virtual_cc_mappings
        self.virtual_midi_channel = virtual_midi_channel

    def handle_pedal_change(self, percentage: float, midiout: Any) -> None:
        """
        Handle expression pedal movement in parameter mode.

        Computes interpolated state across all stops and sends virtual MIDI CCs
        for each parameter.

        Args:
            percentage: Global position (0.0-1.0)
            midiout: MIDI output object
        """
        # Call interpolation function to get complete interpolated state
        interpolated_state = self.interpolation_func(percentage, self.stops)

        # Send virtual MIDI CC for each parameter
        for instance_id, params in interpolated_state.items():
            for symbol, value in params.items():
                param_key = f"{instance_id}:{symbol}"

                # Get virtual CC number for this parameter
                cc_num = self.virtual_cc_mappings.get(param_key)
                if cc_num is None:
                    logging.warning(f"No virtual CC mapping for {param_key}, skipping")
                    continue

                # Scale parameter value (0.0-1.0) to MIDI CC value (0-127)
                cc_value = int(value * 127)
                cc_value = max(0, min(127, cc_value))  # Clamp

                # Send virtual MIDI CC
                self._send_virtual_midi_cc(cc_num, cc_value, midiout)

    def _send_virtual_midi_cc(self, cc_num: int, value: int, midiout: Any) -> None:
        """
        Send virtual MIDI CC message on the virtual channel.

        Args:
            cc_num: MIDI CC number (70-127)
            value: MIDI CC value (0-127)
            midiout: MIDI output object
        """
        if not midiout:
            logging.warning("send_virtual_midi_cc: midiout not available")
            return

        # Build MIDI CC message: [channel | CONTROL_CHANGE, cc_number, value]
        midi_msg = [self.virtual_midi_channel | CONTROL_CHANGE, cc_num, value]
        midiout.send_message(midi_msg)
        logging.debug(f"Sent virtual MIDI CC: channel={self.virtual_midi_channel}, cc={cc_num}, value={value}")
