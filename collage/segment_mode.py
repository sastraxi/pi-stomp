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

from collage.stop import CollageStop
from collage.types import EasingFunc, ParameterTypeGetter


class SegmentMode:
    """Handles segment-based interpolation with easing."""

    def __init__(
        self,
        stops: list[CollageStop],
        easing_func: EasingFunc,
        parameter_setter: Any,  # ParameterSetter
        param_type_getter: ParameterTypeGetter,
        instance_number_getter: Any  # Callable[[str], int | None]
    ) -> None:
        """
        Initialize segment mode handler.

        Args:
            stops: List of CollageStop objects (sorted by position)
            easing_func: Easing function to apply
            parameter_setter: ParameterSetter instance for setting parameters
            param_type_getter: Function to get parameter type
            instance_number_getter: Function to get instance number from instance_id
        """
        self.stops = stops
        self.easing_func = easing_func
        self.parameter_setter = parameter_setter
        self.param_type_getter = param_type_getter
        self.instance_number_getter = instance_number_getter
        self.current_segment: int = 0

    def handle_pedal_change(
        self,
        percentage: float,
        exp_channel: int,  # Unused, kept for API compatibility
        exp_cc: int,       # Unused, kept for API compatibility
        midiout: Any       # Unused, kept for API compatibility
    ) -> None:
        """
        Handle expression pedal movement in segment mode.

        Applies easing function to transform the expression pedal value,
        then queues parameters via WebSocket (non-blocking).

        Args:
            percentage: Global position (0.0-1.0)
            exp_channel: Unused (kept for compatibility)
            exp_cc: Unused (kept for compatibility)
            midiout: Unused (kept for compatibility)
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

        logging.debug(
            f"Segment {new_segment}: pct={percentage:.3f}, local={local_pct:.3f}, "
            f"eased={eased_pct:.3f}"
        )

        # Queue parameters via WebSocket (non-blocking)
        self.parameter_setter.apply_segment_parameters(
            lower_stop,
            upper_stop,
            eased_pct,
            self.param_type_getter,
            self.instance_number_getter
        )

        # If segment changed, log it
        if new_segment != self.current_segment:
            logging.info(f"Segment change: {self.current_segment} -> {new_segment}")
            self.current_segment = new_segment

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
