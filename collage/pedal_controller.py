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
from typing import Any

from collage.stop import CollageStop
from collage.types import EnrichedDiffMap, InterpolationFunc


class PedalController:
    """
    Manages expression pedal callback for collage mode with optimized critical path.

    Implements the critical path (10ms polling loop) for pedal movement:
    1. Find segment (with cached hint)
    2. Calculate local percentage
    3. Lookup pre-computed diff map
    4. Apply interpolation per-parameter
    5. Send to ParameterSetter (handles de-dupe)
    """

    def __init__(
        self,
        interpolation_func: InterpolationFunc,
        stops: list[CollageStop],
        segment_diff_maps: list[EnrichedDiffMap],
        parameter_setter: Any,  # ParameterSetter - avoid circular import
    ) -> None:
        """
        Initialize pedal controller with pre-computed diff maps.

        Args:
            interpolation_func: Per-parameter value transformer
            stops: List of CollageStop (for segment lookup)
            segment_diff_maps: Pre-computed enriched diff maps per segment
            parameter_setter: For sending WebSocket messages
        """
        self.interpolation_func = interpolation_func
        self.stops = stops
        self.segment_diff_maps = segment_diff_maps
        self.parameter_setter = parameter_setter
        self.controlled_pedal: Any = None  # AnalogMidiControl

        # Segment caching (optimization for sequential movements)
        self.current_segment_idx: int = 0

    def attach_to_pedal(self, analog_controls: list[Any], pedal_id: int) -> None:
        """
        Attach collage mode callback to expression pedal.

        Hijacks the value_change_callback on the AnalogMidiControl to intercept
        value changes and route them through the interpolation system.

        Args:
            analog_controls: List of analog controls from hardware
            pedal_id: Expression pedal ID to control
        """
        # Find expression pedal control
        for control in analog_controls:
            if hasattr(control, "id") and control.id == pedal_id:
                self.controlled_pedal = control
                control.value_change_callback = self.handle_value_change
                logging.info(f"Attached collage mode to expression pedal {pedal_id}")
                return

        logging.warning(f"Expression pedal {pedal_id} not found")

    def detach_from_pedal(self) -> None:
        """Remove collage mode callback from expression pedal."""
        if self.controlled_pedal:
            self.controlled_pedal.value_change_callback = None
            self.controlled_pedal = None
            logging.debug("Detached collage mode from expression pedal")

    def reset_tracking(self) -> None:
        """Reset state tracking (call on re-initialization)."""
        self.current_segment_idx = 0

    def handle_value_change(self, raw_value: int, control: Any) -> None:
        """
        Handle expression pedal movement (CRITICAL PATH - optimized for 10ms polling).

        Called by AnalogMidiControl.refresh() when the pedal value changes.
        Implements the core collage mode interpolation loop.

        Args:
            raw_value: Raw ADC value (0-1023)
            control: The AnalogMidiControl instance
        """
        # Convert ADC value to percentage [0.0, 1.0]
        percentage = raw_value / 1023.0  # ADC is 10-bit

        # Find segment (use cached value as hint for optimization)
        segment_idx = self._find_segment(percentage)

        # Calculate local percentage within segment
        lower = self.stops[segment_idx]
        upper = self.stops[segment_idx + 1]
        segment_range = upper.position - lower.position

        if segment_range > 0:
            local_pct = (percentage - lower.position) / segment_range
            local_pct = max(0.0, min(1.0, local_pct))  # Clamp to [0, 1]
        else:
            local_pct = 0.0

        # Get pre-computed diff map for this segment
        diff_map = self.segment_diff_maps[segment_idx]

        # Interpolate and send (ParameterSetter handles de-dupe)
        try:
            for instance_id, params in diff_map.items():
                for symbol, param_data in params.items():
                    # Apply interpolation function
                    float_value = self.interpolation_func(local_pct, param_data)

                    # Send parameter (ParameterSetter handles MIDI de-dupe and backpressure)
                    self.parameter_setter.send_parameter(instance_id, symbol, float_value)
        except Exception as e:
            logging.error(f"Error in collage interpolation: {e}", exc_info=True)
            # Continue operation - don't crash the polling loop

        # Log if segment changed
        if segment_idx != self.current_segment_idx:
            logging.debug(
                f"Segment {self.current_segment_idx} -> {segment_idx} at {percentage:.3f}"
            )
            self.current_segment_idx = segment_idx

    def _find_segment(self, percentage: float) -> int:
        """
        Find segment index for percentage.

        Optimized with cached current segment as hint - checks current segment first
        before doing full search. This is fast for sequential pedal movements.

        Args:
            percentage: Global position [0.0, 1.0]

        Returns:
            Segment index (0 to len(stops)-2)
        """
        # Check current segment first (common case: small sequential movements)
        if self.current_segment_idx < len(self.stops) - 1:
            if (self.stops[self.current_segment_idx].position <= percentage <
                    self.stops[self.current_segment_idx + 1].position):
                return self.current_segment_idx

        # Linear search (could optimize to binary search if many stops)
        for i in range(len(self.stops) - 1):
            if percentage < self.stops[i + 1].position:
                return i

        # At or beyond last stop - use last segment
        return len(self.stops) - 2
