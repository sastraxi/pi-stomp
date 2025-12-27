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

"""Type definitions for collage mode."""

from typing import Any, Callable, Literal, NotRequired, TypedDict

from modalapi.parameter import Type as ParameterType


# Config TypedDicts
class CollageConfig(TypedDict):
    """Complete collage mode configuration from YAML."""
    enabled: bool
    mode: NotRequired[Literal['segment', 'parameter']]
    expression_pedal_id: NotRequired[int]
    snapshot_stops: dict[str, int | str]  # "position" -> snapshot (index or name)
    throttle_ms: NotRequired[int]

    # Segment mode options
    easing: NotRequired[str]

    # Parameter mode options
    interpolation: NotRequired[str]
    virtual_midi_channel: NotRequired[int]


# Snapshots.json TypedDicts
class PluginData(TypedDict):
    """Plugin data from snapshots.json."""
    bypassed: bool
    parameters: dict[str, Any]
    ports: dict[str, float]
    preset: str
    bpm: NotRequired[float]
    bpb: NotRequired[float]


class SnapshotData(TypedDict):
    """Single snapshot entry from snapshots.json."""
    name: str
    data: dict[str, PluginData]


class SnapshotsJson(TypedDict):
    """Complete snapshots.json file structure."""
    current: int
    snapshots: list[SnapshotData]


# State TypedDicts
class ParameterState(TypedDict):
    """Parameter values for a plugin: {symbol: value}"""
    pass  # Dict[str, float] - dynamic keys


class SnapshotState(TypedDict):
    """Complete snapshot state: {instance_id: {symbol: value}}"""
    pass  # Dict[str, Dict[str, float]] - dynamic keys


class DiffMapEntry(TypedDict):
    """Single parameter diff entry: (val_a, val_b, param_type)"""
    pass  # Tuple[float, float, ParameterType] - but TypedDict doesn't support tuples


# Type aliases for complex nested structures
ParameterStateDict = dict[str, float]
SnapshotStateDict = dict[str, ParameterStateDict]
DiffMapDict = dict[str, dict[str, tuple[float, float, ParameterType]]]
ParameterTypeGetter = Callable[[str, str], ParameterType]

# Function type aliases
EasingFunc = Callable[[float], float]
InterpolationFunc = Callable[[float, list[Any]], SnapshotStateDict]  # list[CollageStop] - avoid circular import
