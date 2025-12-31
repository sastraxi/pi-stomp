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

"""Per-parameter interpolation functions for collage mode."""

from blend.easing import (
    ease_in_cubic,
    ease_in_out_cubic,
    ease_in_out_quad,
    ease_in_quad,
    ease_out_cubic,
    ease_out_quad,
    exponential_easing,
    sine_easing,
)
from blend.types import EasingFunc, InterpolationFunc, ParamData


# Interpolation Function Framework
# ==================================
# Per-parameter interpolation functions transform a single parameter value.
# They operate on pre-computed ParamData which includes neighbor values for
# spline interpolation (hermite/catmull-rom).
#
# Type signature: (local_pct, param_data) -> interpolated_value
# - local_pct: 0.0-1.0, position within current segment
# - param_data: ParamData with val_a, val_b, neighbors, segment_range
# - Returns: float, interpolated value [0.0-1.0]
#
# Two categories:
# 1. Spline interpolation - uses neighbor context (prev_val, next_val)
# 2. Easing-based interpolation - segment-local only (val_a, val_b)


def linear_interpolation(local_pct: float, param_data: ParamData) -> float:
    """
    Simple linear interpolation between two values.

    Parameters change at constant rate between stops.
    No neighbor values needed.

    Args:
        local_pct: Position within segment [0.0, 1.0]
        param_data: Pre-computed parameter data

    Returns:
        Interpolated value [0.0, 1.0]
    """
    return param_data.val_a + (param_data.val_b - param_data.val_a) * local_pct


def hermite_interpolation(local_pct: float, param_data: ParamData) -> float:
    """
    Cubic Hermite interpolation with automatic tangent calculation.

    Uses pre-computed neighbor values for smooth C1-continuous curves
    (continuous first derivative). Guarantees passing through each
    stop point exactly while smoothing transitions between segments.

    Tangents calculated using centered differences:
    - m0 = (val_b - prev_val) / (2 * segment_range)
    - m1 = (next_val - val_a) / (2 * segment_range)
    At endpoints, uses one-sided differences.

    Args:
        local_pct: Position within segment [0.0, 1.0]
        param_data: Pre-computed parameter data with neighbor values

    Returns:
        Interpolated value [0.0, 1.0]

    Math: H(t) = h00*p0 + h10*m0*range + h01*p1 + h11*m1*range
    where h = Hermite basis functions, p = positions, m = tangents
    """
    t = local_pct
    p0, p1 = param_data.val_a, param_data.val_b

    # Calculate tangents using neighbor values
    if param_data.prev_val is not None:
        # Centered difference: tangent considers both neighbors
        m0 = (p1 - param_data.prev_val) / (2 * param_data.segment_range)
    else:
        # First segment: forward difference
        m0 = (p1 - p0) / param_data.segment_range

    if param_data.next_val is not None:
        # Centered difference
        m1 = (param_data.next_val - p0) / (2 * param_data.segment_range)
    else:
        # Last segment: backward difference
        m1 = (p1 - p0) / param_data.segment_range

    # Hermite basis functions
    h00 = 2*t**3 - 3*t**2 + 1  # p0 coefficient
    h10 = t**3 - 2*t**2 + t     # m0 coefficient
    h01 = -2*t**3 + 3*t**2      # p1 coefficient
    h11 = t**3 - t**2           # m1 coefficient

    # Apply Hermite formula
    value = h00 * p0 + h10 * m0 * param_data.segment_range + h01 * p1 + h11 * m1 * param_data.segment_range

    return value


def catmull_rom_interpolation(local_pct: float, param_data: ParamData) -> float:
    """
    Catmull-Rom spline interpolation using pre-computed neighbors.

    A special case of cubic Hermite with automatic tangent calculation:
    m[i] = (p[i+1] - p[i-1]) / 2

    Provides C1-continuous curves with local control. Each segment only
    depends on 4 points (2 bracketing + 2 neighbors). Guarantees passing
    through each stop exactly.

    Characteristics:
    - Passes through all control points exactly
    - Smooth first derivative (C1 continuity)
    - Local control (changing one stop affects at most 4 segments)
    - Tension = 0.5 (standard Catmull-Rom)

    Args:
        local_pct: Position within segment [0.0, 1.0]
        param_data: Pre-computed parameter data with neighbor values

    Returns:
        Interpolated value [0.0, 1.0]

    Math: CR(t) = 0.5 * [(2p₁) + (-p₀+p₂)t + (2p₀-5p₁+4p₂-p₃)t² + (-p₀+3p₁-3p₂+p₃)t³]
    where p₀...p₃ are the 4 control points
    """
    t = local_pct
    t2, t3 = t * t, t * t * t

    p1, p2 = param_data.val_a, param_data.val_b

    # Use neighbors or extrapolate at boundaries
    p0 = param_data.prev_val if param_data.prev_val is not None else 2*p1 - p2
    p3 = param_data.next_val if param_data.next_val is not None else 2*p2 - p1

    # Catmull-Rom formula
    value = 0.5 * (
        (2 * p1) +
        (-p0 + p2) * t +
        (2*p0 - 5*p1 + 4*p2 - p3) * t2 +
        (-p0 + 3*p1 - 3*p2 + p3) * t3
    )

    return value


# Easing-Based Interpolation Functions
# ======================================
# Easing functions transform the local percentage before linear interpolation.
# They only use val_a and val_b from param_data (no neighbor values needed).


def create_easing_interpolation(easing_func: EasingFunc) -> InterpolationFunc:
    """
    Convert an easing function to an interpolation function.

    Applies easing transform to local percentage, then does linear interpolation.
    Easing functions only look at the two bracketing stops (no neighbors).

    Args:
        easing_func: Easing function (t: float) -> float

    Returns:
        Interpolation function (local_pct, param_data) -> float
    """
    def interpolation(local_pct: float, param_data: ParamData) -> float:
        # Apply easing transform
        eased = easing_func(local_pct)
        # Linear interpolation with eased percentage
        return param_data.val_a + (param_data.val_b - param_data.val_a) * eased

    return interpolation


# Create easing-based interpolation functions programmatically
ease_in_quad_interpolation = create_easing_interpolation(ease_in_quad)
ease_out_quad_interpolation = create_easing_interpolation(ease_out_quad)
ease_in_out_quad_interpolation = create_easing_interpolation(ease_in_out_quad)
ease_in_cubic_interpolation = create_easing_interpolation(ease_in_cubic)
ease_out_cubic_interpolation = create_easing_interpolation(ease_out_cubic)
ease_in_out_cubic_interpolation = create_easing_interpolation(ease_in_out_cubic)
exponential_easing_interpolation = create_easing_interpolation(exponential_easing)
sine_easing_interpolation = create_easing_interpolation(sine_easing)


# Export all interpolation functions for easy import
__all__ = [
    # Spline interpolation
    'hermite_interpolation',
    'catmull_rom_interpolation',
    # Easing-based interpolation
    'linear_interpolation',
    'ease_in_quad_interpolation',
    'ease_out_quad_interpolation',
    'ease_in_out_quad_interpolation',
    'ease_in_cubic_interpolation',
    'ease_out_cubic_interpolation',
    'ease_in_out_cubic_interpolation',
    'exponential_easing_interpolation',
    'sine_easing_interpolation',
    # Helper
    'create_easing_interpolation',
]
