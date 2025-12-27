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
Collage mode package - Expression pedal-driven snapshot interpolation.

This package provides functionality for smoothly interpolating between snapshots
based on expression pedal position, with per-parameter interpolation and
pre-computed diff maps for optimized performance.
"""

# Main public API
from collage.manager import CollageMode
from collage.stop import CollageStop
from collage.types import CollageConfig

# Easing functions
from collage.easing import (
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
from collage.interpolation import (
    hermite_interpolation,
    catmull_rom_interpolation,
)

# Easing-based interpolation functions
from collage.interpolation import (
    linear_interpolation,
)

__all__ = [
    # Core classes
    'CollageMode',
    'CollageStop',
    'CollageConfig',

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

    # Easing-based interpolation functions
    'linear_interpolation',
]
