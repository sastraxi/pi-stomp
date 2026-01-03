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

    WINDOW_MS = 600
    MIN_SAMPLES = 4
    DECAY_FACTOR = 0.4
    VELOCITY_DEAD_ZONE = 6
    SCALE_EXPONENT_MUTLIPLIER = 2.0

    def __init__(self, max_velocity=12, step_scale: float = 1.0):
        self.samples = []
        self.last_direction = 0
        self.max_velocity: int = max_velocity
        self.step_scale: float = step_scale

    def set_step_scale(self, step_scale: float):
        """Set the step scale for movement sensitivity."""
        self.step_scale = step_scale

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
        if not self.samples:
            return 0.0

        multiplier = len(self.samples) / self.MIN_SAMPLES if len(self.samples) < self.MIN_SAMPLES else 1.0
        timestamps = np.array([ts for ts, _ in self.samples])
        diffs = np.diff(timestamps)

        if len(diffs) == 0:
            return 0.0

        n = len(diffs)
        weights = np.array([self.DECAY_FACTOR ** (n - 1 - i) for i in range(n)])
        # weights /= weights.sum()  # OLD: normalized weighted average

        weighted_sum = np.dot(diffs, weights)

        if weighted_sum < 0.001:
            return 100.0

        return multiplier / weighted_sum

    def _velocity_to_multiplier(self, velocity: float) -> int:
        velocity = max(0, velocity - self.VELOCITY_DEAD_ZONE)
        exponent = 0.9 + self.SCALE_EXPONENT_MUTLIPLIER * (1.0 / self.step_scale)
        multiplier = int((velocity**exponent) * 2)
        return multiplier
