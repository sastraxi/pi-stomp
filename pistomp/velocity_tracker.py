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

import time
import numpy as np


class VelocityTracker:
    """Tracks rotation timing and calculates velocity-based step multipliers."""

    WINDOW_MS = 200
    MIN_SAMPLES = 2

    def __init__(self):
        self.samples = []

    def add_rotation(self, direction: int) -> int:
        """Return step multiplier (1-32) based on rotation velocity."""
        now = time.monotonic()
        self.samples.append((now, direction))
        self._prune_old_samples(now)

        velocity = self._calculate_velocity()
        return self._velocity_to_multiplier(velocity)

    def _prune_old_samples(self, current_time: float):
        window_seconds = self.WINDOW_MS / 1000.0
        cutoff_time = current_time - window_seconds
        self.samples = [(ts, d) for ts, d in self.samples if ts >= cutoff_time]

    def _calculate_velocity(self) -> float:
        if len(self.samples) < self.MIN_SAMPLES:
            return 0.0

        timestamps = np.array([ts for ts, _ in self.samples])
        directions = np.array([d for _, d in self.samples])
        cumulative_rotations = np.abs(np.cumsum(directions))
        time_deltas = timestamps - timestamps[0]

        if time_deltas[-1] < 0.001:
            return float(len(self.samples) * 10)

        coeffs = np.polyfit(time_deltas, cumulative_rotations, 1)
        return abs(coeffs[0])

    def _velocity_to_multiplier(self, velocity: float) -> int:
        if velocity < 0.1:
            return 1

        multiplier = int(velocity**2)
        return max(1, min(multiplier, 8))
