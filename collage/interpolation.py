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

"""Interpolation functions for collage mode parameter interpolation."""

from typing import TYPE_CHECKING

from collage.types import SnapshotStateDict

if TYPE_CHECKING:
    from collage.stop import CollageStop


# Interpolation Function Framework
# ==================================
# Interpolation functions compute parameter values across ALL stops simultaneously,
# providing smooth transitions while guaranteeing exact values at stop positions.
#
# Type signature: (percentage, stops) -> interpolated_state
# - percentage: 0.0-1.0, global position across all stops
# - stops: List[CollageStop], sorted by position
# - Returns: SnapshotStateDict with interpolated parameter values


def linear_interpolation(percentage: float, stops: list['CollageStop']) -> SnapshotStateDict:
    """
    Piecewise linear interpolation between stops.

    Finds bracketing stops and linearly interpolates within the segment.
    Simple and predictable - parameters change at constant rate between stops.

    Args:
        percentage: Global position (0.0-1.0)
        stops: List of CollageStop, sorted by position

    Returns:
        Interpolated parameter state
    """
    # Handle edge cases
    if percentage <= stops[0].position:
        return stops[0].snapshot_state
    if percentage >= stops[-1].position:
        return stops[-1].snapshot_state

    # Find bracketing stops
    for i in range(len(stops) - 1):
        if stops[i].position <= percentage < stops[i + 1].position:
            lower, upper = stops[i], stops[i + 1]

            # Calculate local percentage within this segment
            segment_range = upper.position - lower.position
            local_pct = (percentage - lower.position) / segment_range

            # Interpolate all parameters
            result: SnapshotStateDict = {}
            for instance_id in set(lower.snapshot_state.keys()) | set(upper.snapshot_state.keys()):
                result[instance_id] = {}
                lower_params = lower.snapshot_state.get(instance_id, {})
                upper_params = upper.snapshot_state.get(instance_id, {})

                for symbol in set(lower_params.keys()) | set(upper_params.keys()):
                    val_lower = lower_params.get(symbol, 0.0)
                    val_upper = upper_params.get(symbol, 0.0)

                    # Linear interpolation
                    result[instance_id][symbol] = val_lower + (val_upper - val_lower) * local_pct

            return result

    # Fallback (should never reach)
    return stops[-1].snapshot_state


def hermite_interpolation(percentage: float, stops: list['CollageStop']) -> SnapshotStateDict:
    """
    Cubic Hermite interpolation with automatic tangent calculation.

    Uses finite differences to estimate tangents at each stop, providing smooth
    C1-continuous curves (continuous first derivative). Guarantees passing through
    each stop point exactly while smoothing transitions between segments.

    Tangents are calculated using centered differences (Catmull-Rom style):
    - tangent[i] = (stops[i+1].value - stops[i-1].value) / (stops[i+1].pos - stops[i-1].pos)
    - At endpoints, use one-sided differences

    Args:
        percentage: Global position (0.0-1.0)
        stops: List of CollageStop, sorted by position

    Returns:
        Interpolated parameter state

    Math: H(t) = (2t³ - 3t² + 1)p₀ + (t³ - 2t² + t)m₀ + (-2t³ + 3t²)p₁ + (t³ - t²)m₁
    where t ∈ [0,1], p = position values, m = tangent values
    """
    # Handle edge cases
    if percentage <= stops[0].position:
        return stops[0].snapshot_state
    if percentage >= stops[-1].position:
        return stops[-1].snapshot_state

    # Find bracketing stops
    for i in range(len(stops) - 1):
        if stops[i].position <= percentage < stops[i + 1].position:
            lower, upper = stops[i], stops[i + 1]

            # Calculate normalized t in [0, 1] for this segment
            segment_range = upper.position - lower.position
            t = (percentage - lower.position) / segment_range

            # Hermite basis functions
            h00 = 2*t**3 - 3*t**2 + 1  # p0 coefficient
            h10 = t**3 - 2*t**2 + t     # m0 coefficient
            h01 = -2*t**3 + 3*t**2      # p1 coefficient
            h11 = t**3 - t**2           # m1 coefficient

            # Interpolate all parameters
            result: SnapshotStateDict = {}
            for instance_id in set(lower.snapshot_state.keys()) | set(upper.snapshot_state.keys()):
                result[instance_id] = {}
                lower_params = lower.snapshot_state.get(instance_id, {})
                upper_params = upper.snapshot_state.get(instance_id, {})

                for symbol in set(lower_params.keys()) | set(upper_params.keys()):
                    p0 = lower_params.get(symbol, 0.0)
                    p1 = upper_params.get(symbol, 0.0)

                    # Calculate tangents using finite differences
                    # m0: tangent at lower stop
                    if i == 0:
                        # First stop: forward difference
                        m0 = (p1 - p0) / segment_range
                    else:
                        # Centered difference
                        prev_val = stops[i-1].snapshot_state.get(instance_id, {}).get(symbol, 0.0)
                        prev_pos = stops[i-1].position
                        m0 = (p1 - prev_val) / (upper.position - prev_pos)

                    # m1: tangent at upper stop
                    if i + 1 == len(stops) - 1:
                        # Last stop: backward difference
                        m1 = (p1 - p0) / segment_range
                    else:
                        # Centered difference
                        next_val = stops[i+2].snapshot_state.get(instance_id, {}).get(symbol, 0.0)
                        next_pos = stops[i+2].position
                        m1 = (next_val - p0) / (next_pos - lower.position)

                    # Apply Hermite interpolation
                    value = h00 * p0 + h10 * m0 * segment_range + h01 * p1 + h11 * m1 * segment_range
                    result[instance_id][symbol] = value

            return result

    # Fallback
    return stops[-1].snapshot_state


def catmull_rom_interpolation(percentage: float, stops: list['CollageStop']) -> SnapshotStateDict:
    """
    Catmull-Rom spline interpolation for smooth curves through all stops.

    A special case of cubic Hermite where tangents are automatically calculated
    as: m[i] = (p[i+1] - p[i-1]) / 2

    Provides C1-continuous curves with local control - each segment only depends
    on 4 points (2 bracketing + 2 neighbors). Guarantees passing through each
    stop exactly while providing smoother transitions than linear.

    Characteristics:
    - Passes through all control points exactly
    - Smooth first derivative (C1 continuity)
    - Local control (changing one stop affects at most 4 segments)
    - Tension = 0.5 (standard Catmull-Rom)

    Args:
        percentage: Global position (0.0-1.0)
        stops: List of CollageStop, sorted by position

    Returns:
        Interpolated parameter state

    Math: CR(t) = 0.5 * [(2p₁) + (-p₀+p₂)t + (2p₀-5p₁+4p₂-p₃)t² + (-p₀+3p₁-3p₂+p₃)t³]
    where t ∈ [0,1], p₀...p₃ are the 4 control points
    """
    # Handle edge cases
    if percentage <= stops[0].position:
        return stops[0].snapshot_state
    if percentage >= stops[-1].position:
        return stops[-1].snapshot_state

    # Find bracketing stops
    for i in range(len(stops) - 1):
        if stops[i].position <= percentage < stops[i + 1].position:
            lower, upper = stops[i], stops[i + 1]

            # Calculate normalized t in [0, 1] for this segment
            segment_range = upper.position - lower.position
            t = (percentage - lower.position) / segment_range
            t2 = t * t
            t3 = t2 * t

            # Interpolate all parameters
            result: SnapshotStateDict = {}
            for instance_id in set(lower.snapshot_state.keys()) | set(upper.snapshot_state.keys()):
                result[instance_id] = {}
                lower_params = lower.snapshot_state.get(instance_id, {})
                upper_params = upper.snapshot_state.get(instance_id, {})

                for symbol in set(lower_params.keys()) | set(upper_params.keys()):
                    # Get 4 control points (p0, p1, p2, p3)
                    p1 = lower_params.get(symbol, 0.0)  # Current lower stop
                    p2 = upper_params.get(symbol, 0.0)  # Current upper stop

                    # p0: previous stop (or extrapolate if at start)
                    if i == 0:
                        p0 = 2*p1 - p2  # Extrapolate backward
                    else:
                        p0 = stops[i-1].snapshot_state.get(instance_id, {}).get(symbol, 0.0)

                    # p3: next stop (or extrapolate if at end)
                    if i + 1 == len(stops) - 1:
                        p3 = 2*p2 - p1  # Extrapolate forward
                    else:
                        p3 = stops[i+2].snapshot_state.get(instance_id, {}).get(symbol, 0.0)

                    # Catmull-Rom formula
                    value = 0.5 * (
                        (2 * p1) +
                        (-p0 + p2) * t +
                        (2*p0 - 5*p1 + 4*p2 - p3) * t2 +
                        (-p0 + 3*p1 - 3*p2 + p3) * t3
                    )
                    result[instance_id][symbol] = value

            return result

    # Fallback
    return stops[-1].snapshot_state
