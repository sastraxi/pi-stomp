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

"""WebSocket-based parameter setting for blend mode with MIDI de-duplication."""

import logging

from blend.types import ParameterKey, WebSocketBridgeProtocol


class ParameterSetter:
    """
    Sets plugin parameters via WebSocket with MIDI-level de-duplication.

    Tracks last-sent MIDI values per parameter to avoid redundant sends
    when smooth pedal movement produces consecutive identical MIDI values.
    This dramatically reduces WebSocket traffic during expression pedal movement.
    """

    TOLERANCE = float(1 / 1024.0)

    def __init__(self, bridge: WebSocketBridgeProtocol) -> None:
        """
        Initialize parameter setter with shared WebSocket bridge.

        Args:
            bridge: Shared WebSocket bridge instance from handler
        """
        self.bridge = bridge

        # Value change tracking (raw float values, not MIDI)
        self.last_sent_midi_values: dict[ParameterKey, float] = {}

        logging.info("ParameterSetter initialized")

    def send_parameter(self, instance_id: str, symbol: str, value: float) -> bool:
        """
        Send single parameter via WebSocket with de-duplication (non-blocking).

        Skips send if value hasn't changed (within 0.01 tolerance) since last send.
        This prevents flooding the WebSocket with redundant messages during smooth
        pedal movements.

        Args:
            instance_id: Plugin instance ID (e.g., "xfade", "CollisionDrive")
            symbol: Parameter symbol (e.g., "Gain", ":bypass")
            value: Parameter value in native units (NOT normalized)

        Returns:
            True if sent, False if skipped (duplicate) or dropped (backpressure)
        """
        key = ParameterKey(instance_id, symbol)
        last_value = self.last_sent_midi_values.get(key)
        if last_value is not None and abs(last_value - value) < self.TOLERANCE:
            return False  # Skip - value unchanged within tolerance

        # Send via WebSocket
        if self.bridge.send_parameter(instance_id, symbol, value):
            # Update tracking on successful queue (store raw float value)
            self.last_sent_midi_values[key] = value
            return True

        # Dropped due to backpressure
        logging.warning(f"Dropped (backpressure): {instance_id}/{symbol} value={value:.3f}")
        return False

    def reset_tracking(self) -> None:
        """
        Reset value change tracking (call on re-initialization).

        Clears all tracked values so next pedal movement will send all parameters.
        """
        self.last_sent_midi_values.clear()
        logging.debug("ParameterSetter tracking reset")

    def get_stats(self) -> dict:
        """
        Get WebSocket performance statistics.

        Returns:
            Dict with queue_depth, messages_sent, messages_dropped, etc.
        """
        return self.bridge.get_stats()

    def cleanup(self) -> None:
        """Clean up resources."""
        self.last_sent_midi_values.clear()
        logging.info("ParameterSetter cleaned up")
