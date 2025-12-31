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

"""
Blend mode package - Analog input-driven snapshot interpolation.

This package provides functionality for smoothly interpolating between snapshots
based on analog input position (expression pedals or tweak encoders), with
per-parameter interpolation and pre-computed diff maps for optimized performance.
"""

# Main public API
from blend.manager import BlendMode
from blend.stop import BlendStop
from blend.input_controller import InputController
from blend.snapshot import SnapshotManager
from blend.parameter_setter import ParameterSetter
from blend.types import BlendSnapshotConfig, PedalboardBlendConfig, NormalizedStops

# Easing functions
from blend.easing import (
    linear_easing,
    ease_in_quad,
    ease_out_quad,
    ease_in_out_quad,
    ease_in_cubic,
    ease_out_cubic,
    ease_in_out_cubic,
    exponential_easing,
    sine_easing,
)

# Spline interpolation functions
from blend.interpolation import (
    hermite_interpolation,
    catmull_rom_interpolation,
    linear_interpolation,
)

__all__ = [
    # Core classes
    'BlendMode',
    'BlendStop',
    'InputController',
    'SnapshotManager',
    'ParameterSetter',
    'BlendSnapshotConfig',
    'PedalboardBlendConfig',
    'NormalizedStops',

    # Easing functions (for standalone use)
    'linear_easing',
    'ease_in_quad',
    'ease_out_quad',
    'ease_in_out_quad',
    'ease_in_cubic',
    'ease_out_cubic',
    'ease_in_out_cubic',
    'exponential_easing',
    'sine_easing',

    # Spline interpolation functions
    'hermite_interpolation',
    'catmull_rom_interpolation',
    'linear_interpolation',
]

