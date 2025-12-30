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

import logging


class ClippingMonitor:
    """
    Monitors audio output for clipping by listening to LV2 meter plugin output via WebSocket.

    Listens for output_set messages from modmeter plugins and detects clipping
    when peak values exceed threshold.
    """

    MODMETER_URI = "http://gareus.org/oss/lv2/modmeter"

    def __init__(self, clip_threshold=0.5, hold_ticks=5):
        """
        Initialize clipping monitor.

        Args:
            clip_threshold: Peak threshold for clipping detection (linear amplitude, 1.0 = full scale/0dBFS)
            hold_ticks: Number of polling cycles to hold clip indicator before clearing
        """
        self.clip_threshold = clip_threshold
        self.hold_ticks = hold_ticks

        # Meter plugin instance IDs (set when pedalboard changes)
        # Stores instance_id for left and right meters
        self.meter_left_id = None
        self.meter_right_id = None

        # Peak port symbols to monitor
        self.left_peak_symbol = "peak"
        self.right_peak_symbol = "peak"

        # Current peak values
        self.peak_left = 0.0
        self.peak_right = 0.0

        # Clip detection state
        self.clip_left = False
        self.clip_right = False
        self.clip_left_counter = 0
        self.clip_right_counter = 0

        self.enabled = False

    def update_pedalboard(self, pedalboard):
        """
        Update meter references when pedalboard changes.

        Searches for modmeter plugins in the pedalboard and stores
        references to their instance IDs for WebSocket message filtering.

        Args:
            pedalboard: Pedalboard object with plugins list
        """
        self.meter_left_id = None
        self.meter_right_id = None
        self.enabled = False
        self.reset_clip_indicators()

        if not pedalboard or not pedalboard.plugins:
            logging.debug("ClippingMonitor: No pedalboard or plugins")
            return

        # Find modmeter plugins by instance_id (contains "modmeter" or "meter")
        meters = [p for p in pedalboard.plugins if "modmeter" in p.instance_id.lower() or "meter" in p.instance_id.lower()]

        if not meters:
            logging.debug("ClippingMonitor: No modmeter plugins found in pedalboard")
            return

        # Use first meter for left channel
        if len(meters) >= 1:
            self.meter_left_id = meters[0].instance_id  # Keep /graph/ prefix to match websocket format
            logging.info(f"ClippingMonitor: Monitoring {self.meter_left_id} for left channel")

        # Use second meter for right channel if available
        if len(meters) >= 2:
            self.meter_right_id = meters[1].instance_id
            logging.info(f"ClippingMonitor: Monitoring {self.meter_right_id} for right channel")
        elif len(meters) >= 1:
            # Only one meter - use for both channels
            self.meter_right_id = self.meter_left_id
            logging.info("ClippingMonitor: Using single meter for both channels")

        self.enabled = self.meter_left_id is not None
        logging.info(f"ClippingMonitor: {'Enabled' if self.enabled else 'Disabled'}")

    def handle_output_set(self, instance_id, port_symbol, value):
        """
        Handle output_set WebSocket message.

        Called when mod-ui sends parameter updates via WebSocket.
        Tracks maximum peak value since last check_clipping() call.

        Args:
            instance_id: Plugin instance ID (e.g., "modmeter:0")
            port_symbol: Parameter symbol (e.g., "peak", "peak_L", "peak_R")
            value: Parameter value in dB
        """
        if not self.enabled:
            return

        # Track maximum peak value (output_set may arrive faster than poll rate)
        if instance_id == self.meter_left_id and port_symbol == self.left_peak_symbol:
            self.peak_left = max(self.peak_left, value)
        elif instance_id == self.meter_right_id and port_symbol == self.right_peak_symbol:
            self.peak_right = max(self.peak_right, value)

    def check_clipping(self):
        """
        Check current peak values for clipping.

        Called periodically from poll_indicators() (20ms) to update clip state
        with hold counter logic. Resets peak values after checking to prepare
        for next polling interval.

        Returns:
            tuple: (clip_left, clip_right, enabled) - clip flags and monitor status
        """
        if not self.enabled:
            return False, False, False

        # Check if current peak values exceed threshold
        clipped_left = self.peak_left >= self.clip_threshold
        clipped_right = self.peak_right >= self.clip_threshold

        # Reset peaks for next polling interval (handle_output_set uses max())
        self.peak_left = 0.0
        self.peak_right = 0.0

        # Update clip state with hold counter
        if clipped_left:
            self.clip_left = True
            self.clip_left_counter = self.hold_ticks
        else:
            if self.clip_left_counter > 0:
                self.clip_left_counter -= 1
            else:
                self.clip_left = False

        if clipped_right:
            self.clip_right = True
            self.clip_right_counter = self.hold_ticks
        else:
            if self.clip_right_counter > 0:
                self.clip_right_counter -= 1
            else:
                self.clip_right = False

        return self.clip_left, self.clip_right, True

    def reset_clip_indicators(self):
        """Immediately clear clip indicators (e.g., on pedalboard change)."""
        self.clip_left = False
        self.clip_right = False
        self.clip_left_counter = 0
        self.clip_right_counter = 0
