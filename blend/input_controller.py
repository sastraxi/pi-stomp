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

"""Analog input hijacking and control for blend mode."""

import logging
from typing import Any

from blend.stop import BlendStop
from blend.types import BlendInputProtocol, EnrichedDiffMap, InterpolationFunc


class InputController:
    """
    Manages analog input callback for blend mode with optimized critical path.

    Supports both expression pedals and tweak encoders. Implements the critical
    path (10ms polling loop) for input movement:
    1. Find segment (with cached hint)
    2. Calculate local percentage
    3. Lookup pre-computed diff map
    4. Apply interpolation per-parameter
    5. Send to ParameterSetter (handles de-dupe)
    """

    def __init__(
        self,
        interpolation_func: InterpolationFunc,
        stops: list[BlendStop],
        segment_diff_maps: list[EnrichedDiffMap],
        parameter_setter: Any,  # ParameterSetter - avoid circular import
    ) -> None:
        """
        Initialize input controller with pre-computed diff maps.

        Args:
            interpolation_func: Per-parameter value transformer
            stops: List of BlendStop (for segment lookup)
            segment_diff_maps: Pre-computed enriched diff maps per segment
            parameter_setter: For sending WebSocket messages
        """
        self.interpolation_func = interpolation_func
        self.stops = stops
        self.segment_diff_maps = segment_diff_maps
        self.parameter_setter = parameter_setter
        self.controlled_input: BlendInputProtocol | None = None

        # Segment caching (optimization for sequential movements)
        self.current_segment_idx: int = 0

    def attach_to_input(self, analog_controls: list, encoders: list, input_id: int) -> None:
        """
        Attach blend mode callback to analog input (expression pedal or encoder).

        Hijacks the value_change_callback to intercept value changes and route
        them through the interpolation system.

        Args:
            analog_controls: List of analog controls from hardware
            encoders: List of encoders from hardware
            input_id: Input ID to control (searches both lists)

        Raises:
            ValueError: If input_id not found or encoder doesn't support MIDI
        """
        # Search analog_controls first (expression pedals)
        for control in analog_controls:
            if hasattr(control, "id") and control.id == input_id:
                self.controlled_input = control
                control.value_change_callback = self.handle_value_change
                logging.info(f"Attached blend mode to analog control {input_id}")
                return

        # Search encoders (tweak encoders)
        for encoder in encoders:
            if hasattr(encoder, "id") and encoder.id == input_id:
                from pistomp.encoder_controller import EncoderController

                if not isinstance(encoder, EncoderController):
                    raise ValueError(f"Encoder {input_id} must be EncoderController for blend mode")
                self.controlled_input = encoder
                encoder.value_change_callback = self.handle_value_change
                logging.info(f"Attached blend mode to encoder {input_id}")
                return

        raise ValueError(f"Input {input_id} not found in analog_controls or encoders")

    def detach_from_input(self) -> None:
        """Remove blend mode callback from input."""
        if self.controlled_input:
            input_type = type(self.controlled_input).__name__
            input_id = getattr(self.controlled_input, "id", "?")
            logging.info(f"Detaching blend mode from {input_type} (id={input_id})")
            self.controlled_input.value_change_callback = None
            self.controlled_input = None
        else:
            logging.warning("detach_from_input called but no controlled_input attached")

    def reset_tracking(self) -> None:
        """Reset state tracking (call on re-initialization)."""
        self.current_segment_idx = 0

    def sync_current_position(self) -> None:
        """
        Force update of ALL parameters based on current input position.

        Called on activation to establish initial parameter state when
        blend snapshot is empty. Sends ALL parameters (differing + non-differing)
        to overwrite any stale values from the previous snapshot.

        For differing parameters: interpolates based on current pedal position
        For non-differing parameters: uses constant value from first stop
        """
        if not self.controlled_input:
            logging.warning("Cannot sync - no controlled input attached")
            return

        # Get normalized position from control
        percentage = self.controlled_input.get_normalized_value()

        # Find segment for interpolation
        segment_idx = self._find_segment(percentage)
        lower = self.stops[segment_idx]
        upper = self.stops[segment_idx + 1]
        segment_range = upper.position - lower.position

        if segment_range > 0:
            local_pct = (percentage - lower.position) / segment_range
            local_pct = max(0.0, min(1.0, local_pct))
        else:
            local_pct = 0.0

        # Send differing parameters (interpolated)
        diff_map = self.segment_diff_maps[segment_idx]
        diff_sent = 0
        for instance_id, params in diff_map.items():
            for symbol, param_data in params.items():
                float_value = self.interpolation_func(local_pct, param_data)
                if self.parameter_setter.send_parameter(instance_id, symbol, float_value):
                    diff_sent += 1
                else:
                    logging.debug(f"Skipped differing param {instance_id}/{symbol} = {float_value:.3f}")

        # Send non-differing parameters (constant from first stop)
        # These are in first stop but NOT in any diff map
        first_stop_state = self.stops[0].snapshot_state
        const_sent = 0
        for instance_id, params in first_stop_state.items():
            for symbol, value in params.items():
                # Skip if already sent as differing parameter
                if instance_id in diff_map and symbol in diff_map[instance_id]:
                    continue
                # Send constant value
                if self.parameter_setter.send_parameter(instance_id, symbol, value):
                    const_sent += 1
                else:
                    logging.debug(f"Skipped constant param {instance_id}/{symbol} = {value:.3f}")

        logging.info(
            f"Synced blend mode to position {percentage:.3f} (segment {segment_idx}): sent {diff_sent} differing + {const_sent} constant = {diff_sent + const_sent} total parameters"
        )

    def handle_value_change(self, raw_value: int, control: BlendInputProtocol) -> None:
        """
        Handle analog input movement (CRITICAL PATH - optimized for 10ms polling).

        Called by control.refresh() when the input value changes.
        Implements the core blend mode interpolation loop.

        Args:
            raw_value: Input value (0-1023 for expression pedal ADC, 0-127 for encoder MIDI) - ignored, read directly from control
            control: The input control that triggered the callback
        """
        # Get normalized position from control (isinstance check done once)
        percentage = control.get_normalized_value()

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
            logging.error(f"Error in blend interpolation: {e}", exc_info=True)
            # Continue operation - don't crash the polling loop

        # Log if segment changed
        if segment_idx != self.current_segment_idx:
            logging.debug(f"Segment {self.current_segment_idx} -> {segment_idx} at {percentage:.3f}")
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
            if (
                self.stops[self.current_segment_idx].position
                <= percentage
                < self.stops[self.current_segment_idx + 1].position
            ):
                return self.current_segment_idx

        # Linear search (could optimize to binary search if many stops)
        for i in range(len(self.stops) - 1):
            if percentage < self.stops[i + 1].position:
                return i

        # At or beyond last stop - use last segment
        return len(self.stops) - 2
