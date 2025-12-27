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

"""Segment mode handler for collage mode."""

import logging
from typing import Any

from rtmidi.midiconstants import CONTROL_CHANGE

from collage.stop import CollageStop
from collage.types import EasingFunc


class SegmentMode:
    """Handles segment-based interpolation with easing."""

    def __init__(
        self,
        stops: list[CollageStop],
        easing_func: EasingFunc,
        apply_mappings_callback: Any  # Callable[[int], None]
    ) -> None:
        """
        Initialize segment mode handler.

        Args:
            stops: List of CollageStop objects (sorted by position)
            easing_func: Easing function to apply
            apply_mappings_callback: Callback to apply MIDI mappings for a segment
        """
        self.stops = stops
        self.easing_func = easing_func
        self.apply_mappings_callback = apply_mappings_callback
        self.current_segment: int = 0

    def handle_pedal_change(
        self,
        percentage: float,
        exp_channel: int,
        exp_cc: int,
        midiout: Any
    ) -> None:
        """
        Handle expression pedal movement in segment mode.

        Applies easing function to transform the expression pedal value,
        then sends the eased CC. Also handles segment switching for multi-stop mode.

        Args:
            percentage: Global position (0.0-1.0)
            exp_channel: MIDI channel of expression pedal
            exp_cc: MIDI CC number of expression pedal
            midiout: MIDI output object
        """
        # Determine current segment
        new_segment = self._get_segment_from_percentage(percentage)

        # Get segment boundaries
        lower_stop = self.stops[new_segment]
        upper_stop = self.stops[new_segment + 1]

        # Calculate local percentage within current segment
        segment_range = upper_stop.position - lower_stop.position
        if segment_range > 0:
            local_pct = (percentage - lower_stop.position) / segment_range
            # Clamp to [0, 1]
            local_pct = max(0.0, min(1.0, local_pct))
        else:
            local_pct = 0.0

        # Apply easing to local percentage
        eased_pct = self.easing_func(local_pct)

        # Convert eased percentage back to global percentage within segment
        eased_global_pct = lower_stop.position + (eased_pct * segment_range)

        # Convert to CC value (0-127)
        eased_cc_value = int(eased_global_pct * 127)
        eased_cc_value = max(0, min(127, eased_cc_value))  # Clamp

        # Send the eased CC value on the expression pedal's channel/CC
        midi_msg = [exp_channel | CONTROL_CHANGE, exp_cc, eased_cc_value]
        midiout.send_message(midi_msg)

        # If segment changed, update MIDI mappings
        if new_segment != self.current_segment:
            logging.debug(f"Segment change: {self.current_segment} -> {new_segment}")
            self.current_segment = new_segment
            self.apply_mappings_callback(new_segment)

    def _get_segment_from_percentage(self, percentage: float) -> int:
        """
        Determine which segment the percentage falls into.

        Args:
            percentage: Global position (0.0-1.0)

        Returns:
            Segment index (0 to len(stops)-2)
        """
        # Find which segment this percentage falls into
        for i in range(len(self.stops) - 1):
            if percentage < self.stops[i + 1].position:
                return i

        # At or beyond last stop - use last segment
        return len(self.stops) - 2
