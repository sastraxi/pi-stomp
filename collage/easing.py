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

"""Easing functions for collage mode segment interpolation."""

import math


# Easing Function Framework
# ==========================
# Easing functions transform local percentage within a segment (segment mode only).
# They provide non-linear transitions while staying within the current segment.
#
# Type signature: (t) -> eased_t
# - t: float in [0.0, 1.0], local position within current segment
# - Returns: float in [0.0, 1.0], transformed position
#
# Used in segment mode to shape the interpolation curve between two stops.
# The eased value is converted back to a CC value before being sent to mod-host.


def linear_easing(t: float) -> float:
    """
    Linear easing - no transformation.

    Simple 1:1 mapping. Parameters change at constant rate.

    Args:
        t: Local position in segment [0.0, 1.0]

    Returns:
        Same as input (identity function)
    """
    return t


def ease_in_quad(t: float) -> float:
    """
    Quadratic ease-in - slow start, accelerating finish.

    Starts slowly and accelerates. Good for fade-ins or building intensity.
    Formula: f(t) = t²

    Args:
        t: Local position in segment [0.0, 1.0]

    Returns:
        Eased position [0.0, 1.0]
    """
    return t * t


def ease_out_quad(t: float) -> float:
    """
    Quadratic ease-out - fast start, decelerating finish.

    Starts quickly and decelerates. Good for fade-outs or settling effects.
    Formula: f(t) = 1 - (1-t)²

    Args:
        t: Local position in segment [0.0, 1.0]

    Returns:
        Eased position [0.0, 1.0]
    """
    return 1.0 - (1.0 - t) * (1.0 - t)


def ease_in_out_quad(t: float) -> float:
    """
    Quadratic ease-in-out - slow start and finish, fast middle.

    Combines ease-in and ease-out for smooth S-curve transitions.
    Accelerates in first half, decelerates in second half.

    Args:
        t: Local position in segment [0.0, 1.0]

    Returns:
        Eased position [0.0, 1.0]
    """
    if t < 0.5:
        return 2.0 * t * t
    else:
        return 1.0 - 2.0 * (1.0 - t) * (1.0 - t)


def ease_in_cubic(t: float) -> float:
    """
    Cubic ease-in - very slow start, strong acceleration.

    More dramatic than quadratic. Starts very slowly and accelerates strongly.
    Formula: f(t) = t³

    Args:
        t: Local position in segment [0.0, 1.0]

    Returns:
        Eased position [0.0, 1.0]
    """
    return t * t * t


def ease_out_cubic(t: float) -> float:
    """
    Cubic ease-out - fast start, strong deceleration.

    More dramatic than quadratic. Starts quickly and decelerates strongly.
    Formula: f(t) = 1 - (1-t)³

    Args:
        t: Local position in segment [0.0, 1.0]

    Returns:
        Eased position [0.0, 1.0]
    """
    return 1.0 - (1.0 - t) * (1.0 - t) * (1.0 - t)


def ease_in_out_cubic(t: float) -> float:
    """
    Cubic ease-in-out - very slow start/finish, very fast middle.

    More dramatic S-curve than quadratic version.
    Strongly accelerates in first half, strongly decelerates in second half.

    Args:
        t: Local position in segment [0.0, 1.0]

    Returns:
        Eased position [0.0, 1.0]
    """
    if t < 0.5:
        return 4.0 * t * t * t
    else:
        return 1.0 - 4.0 * (1.0 - t) * (1.0 - t) * (1.0 - t)


def exponential_easing(t: float) -> float:
    """
    Exponential easing - extreme slow start, explosive finish.

    Very slow initial movement with dramatic acceleration at the end.
    Formula: f(t) = 2^(10(t-1))

    Note: Returns 0.0 at t=0.0 for clean starting point.

    Args:
        t: Local position in segment [0.0, 1.0]

    Returns:
        Eased position [0.0, 1.0]
    """
    if t <= 0.0:
        return 0.0
    if t >= 1.0:
        return 1.0
    return 2.0 ** (10.0 * (t - 1.0))


def sine_easing(t: float) -> float:
    """
    Sinusoidal easing - smooth, natural-feeling curve.

    Gentle acceleration and deceleration using quarter sine wave.
    More gradual than quadratic, feels very natural and smooth.
    Formula: f(t) = sin((t * π) / 2)

    Args:
        t: Local position in segment [0.0, 1.0]

    Returns:
        Eased position [0.0, 1.0]
    """
    return math.sin((t * math.pi) / 2.0)
