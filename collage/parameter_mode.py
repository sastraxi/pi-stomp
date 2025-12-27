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

"""Parameter mode handler for collage mode."""

import logging
from typing import Any

from collage.stop import CollageStop
from collage.types import InterpolationFunc, ParameterTypeGetter


class ParameterMode:
    """Handles full parameter interpolation with WebSocket parameter setting."""

    def __init__(
        self,
        stops: list[CollageStop],
        interpolation_func: InterpolationFunc,
        parameter_setter: Any,  # ParameterSetter
        param_type_getter: ParameterTypeGetter,
        instance_number_getter: Any  # Callable[[str], int | None]
    ) -> None:
        """
        Initialize parameter mode handler.

        Args:
            stops: List of CollageStop objects (sorted by position)
            interpolation_func: Interpolation function to use
            parameter_setter: ParameterSetter instance for setting parameters
            param_type_getter: Function to get parameter type
            instance_number_getter: Function to get instance number from instance_id
        """
        self.stops = stops
        self.interpolation_func = interpolation_func
        self.parameter_setter = parameter_setter
        self.param_type_getter = param_type_getter
        self.instance_number_getter = instance_number_getter

    def handle_pedal_change(self, percentage: float, midiout: Any) -> None:
        """
        Handle expression pedal movement in parameter mode.

        Computes interpolated state across all stops and sets parameters
        via WebSocket (non-blocking).

        Args:
            percentage: Global position (0.0-1.0)
            midiout: Unused (kept for compatibility)
        """
        # Call interpolation function to get complete interpolated state
        interpolated_state = self.interpolation_func(percentage, self.stops)

        # Set all parameters via WebSocket batch (non-blocking)
        self.parameter_setter.apply_parameter_mode_batch(
            interpolated_state,
            self.instance_number_getter
        )

        logging.debug(f"Queued interpolated state for {len(interpolated_state)} plugins at position {percentage:.3f}")
