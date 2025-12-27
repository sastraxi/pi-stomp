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

"""WebSocket-based parameter setting for collage mode."""

import logging
from typing import Any

from collage.stop import CollageStop
from collage.types import ParameterTypeGetter
from modalapi.websocket_bridge import AsyncWebSocketBridge


class ParameterSetter:
    """Sets plugin parameters via WebSocket (async, fire-and-forget)."""

    def __init__(self, ws_url: str = 'ws://localhost:80/websocket') -> None:
        """
        Initialize parameter setter.

        Args:
            ws_url: WebSocket URL for MOD API
        """
        self.ws_url = ws_url
        self.bridge = AsyncWebSocketBridge(ws_url=ws_url, max_queue_size=100)
        self.bridge.start()
        logging.info(f"ParameterSetter initialized with WebSocket: {ws_url}")

    def apply_segment_parameters(
        self,
        stop_a: CollageStop,
        stop_b: CollageStop,
        percentage: float,
        param_type_getter: ParameterTypeGetter,
        instance_number_getter: Any  # Callable[[str], int | None]
    ) -> None:
        """
        Set parameters for current position between two stops.

        Calculates interpolated values and sends them via WebSocket.
        Non-blocking - queues messages and returns immediately.

        Args:
            stop_a: Lower stop of segment
            stop_b: Upper stop of segment
            percentage: Position within segment (0.0-1.0)
            param_type_getter: Function to get parameter type
            instance_number_getter: Function to get instance number from instance_id
        """
        # Calculate parameter diffs for this segment
        diff_map = CollageStop.build_diff_map(
            stop_a.snapshot_state,
            stop_b.snapshot_state,
            param_type_getter
        )

        # Apply binary "on wins" logic
        diff_map = CollageStop.adjust_binary_params(diff_map)

        # Send parameters via WebSocket (non-blocking)
        params_sent = 0
        params_dropped = 0

        for instance_id, params in diff_map.items():
            for symbol, (val_a, val_b, _param_type) in params.items():
                # Interpolate value
                value = val_a + (val_b - val_a) * percentage

                # Queue parameter (non-blocking)
                # Use instance_id directly (e.g., "xfade"), not instance number
                if self.bridge.send_parameter(instance_id, symbol, value):
                    params_sent += 1
                    logging.debug(f"Queued {instance_id}/{symbol} = {value:.3f}")
                else:
                    params_dropped += 1

        # Log summary
        logging.debug(
            f"Segment parameters: {params_sent} queued, {params_dropped} dropped "
            f"at position {percentage:.3f}"
        )

        # Warn if significant backpressure
        if params_dropped > 0:
            stats = self.bridge.get_stats()
            logging.warning(
                f"Dropped {params_dropped} parameters due to backpressure! "
                f"Queue depth: {stats['queue_depth']}"
            )

    def apply_parameter_mode_batch(
        self,
        interpolated_state: dict,
        instance_number_getter: Any  # Callable[[str], int | None] - UNUSED, kept for compatibility
    ) -> None:
        """
        Set all parameters for parameter mode (full state interpolation).

        Non-blocking - queues all messages and returns immediately.

        Args:
            interpolated_state: Dict of {instance_id: {symbol: value}}
            instance_number_getter: Unused (kept for compatibility)
        """
        params_sent = 0
        params_dropped = 0

        for instance_id, params in interpolated_state.items():
            for symbol, value in params.items():
                # Queue parameter (non-blocking)
                # Use instance_id directly (e.g., "CollisionDrive"), not instance number
                if self.bridge.send_parameter(instance_id, symbol, value):
                    params_sent += 1
                    logging.debug(f"Queued {instance_id}/{symbol} = {value:.3f}")
                else:
                    params_dropped += 1

        # Log summary
        logging.debug(
            f"Parameter mode: {params_sent} queued, {params_dropped} dropped"
        )

        # Warn if significant backpressure
        if params_dropped > 0:
            stats = self.bridge.get_stats()
            logging.warning(
                f"Dropped {params_dropped} parameters due to backpressure! "
                f"Queue depth: {stats['queue_depth']}, total dropped: {stats['messages_dropped']}"
            )

    def get_stats(self) -> dict:
        """
        Get WebSocket performance statistics.

        Returns:
            Dict with queue_depth, messages_sent, messages_dropped, etc.
        """
        return self.bridge.get_stats()

    def cleanup(self) -> None:
        """Clean up resources."""
        logging.info("Cleaning up ParameterSetter...")
        self.bridge.stop()
        logging.info("ParameterSetter cleaned up")
