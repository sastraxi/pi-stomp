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


def clamp(value, min_value, max_value):
    return max(min_value, min(max_value, value))


class VelocityTracker:
    """Tracks rotation timing and calculates velocity-based step multipliers."""

    WINDOW_MS = 900
    MIN_SAMPLES = 5
    DECAY_FACTOR = 0.9
    VELOCITY_DEAD_ZONE = 3.8

    def __init__(self, max_velocity=12):
        self.samples = []
        self.last_direction = 0
        self.max_velocity: int = max_velocity

    def add_rotation(self, direction: int) -> int:
        """Return step multiplier (1-32) based on rotation velocity."""
        if self.last_direction != 0 and direction != self.last_direction:
            self.samples = []

        now = time.monotonic()
        self.samples.append((now, direction))
        self.last_direction = direction
        self._prune_old_samples(now)

        velocity = self._calculate_velocity()
        return clamp(self._velocity_to_multiplier(velocity), 1, self.max_velocity)

    def _prune_old_samples(self, current_time: float):
        window_seconds = self.WINDOW_MS / 1000.0
        cutoff_time = current_time - window_seconds
        self.samples = [(ts, d) for ts, d in self.samples if ts >= cutoff_time]

    def _calculate_velocity(self) -> float:
        if len(self.samples) < self.MIN_SAMPLES:
            return 0.0

        timestamps = np.array([ts for ts, _ in self.samples])
        diffs = np.diff(timestamps)

        if len(diffs) == 0:
            return 0.0

        n = len(diffs)
        weights = np.array([self.DECAY_FACTOR ** (n - 1 - i) for i in range(n)])
        weights /= weights.sum()

        avg_interval = np.dot(diffs, weights)

        if avg_interval < 0.001:
            return 100.0

        return 1.0 / avg_interval

    def _velocity_to_multiplier(self, velocity: float) -> int:
        velocity = max(0, velocity - self.VELOCITY_DEAD_ZONE)
        multiplier = int((velocity**0.9) * 2)
        return multiplier
