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

import numpy as np


class ParameterQuantizer:
    """Quantizes continuous parameter ranges into discrete steps."""

    def __init__(self, minimum: float, maximum: float, num_steps: int, taper: float = 1.0):
        self.minimum = minimum
        self.maximum = maximum
        self.num_steps = num_steps
        self.taper = taper
        self.step_values = self._compute_steps()
        self.current_step = 0

    def _compute_steps(self) -> np.ndarray:
        positions = np.linspace(0, 1, self.num_steps)
        tapered_positions = positions**self.taper
        step_values = self.minimum + (self.maximum - self.minimum) * tapered_positions
        return step_values

    def set_value(self, value: float):
        """Set current position to nearest step for the given value."""
        differences = np.abs(self.step_values - value)
        self.current_step = int(np.argmin(differences))

    def move_steps(self, delta_steps: int) -> float:
        """Move by N steps and return the new parameter value."""
        self.current_step = np.clip(self.current_step + delta_steps, 0, self.num_steps - 1)
        return self.step_values[self.current_step]

    def get_value(self) -> float:
        """Get current parameter value."""
        return self.step_values[self.current_step]

    def get_step(self) -> int:
        """Get current step index."""
        return self.current_step

    def get_normalized_position(self) -> float:
        """Get current position normalized to [0, 1]."""
        return self.current_step / (self.num_steps - 1)
