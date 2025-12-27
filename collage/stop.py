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

"""CollageStop class and related utilities for collage mode."""

from collage.types import (
    DiffMapDict,
    EnrichedDiffMap,
    ParamData,
    ParameterTypeGetter,
    SnapshotStateDict,
)
from modalapi.parameter import Type as ParameterType


class CollageStop:
    """
    Represents a gradient stop in the collage interpolation space.

    A stop defines a point along the expression pedal's range (0.0-1.0)
    and the parameter state (snapshot) that should be active at that point.
    """

    def __init__(self, position: float, snapshot_index: int, snapshot_state: SnapshotStateDict) -> None:
        """
        Initialize a collage stop.

        Args:
            position: float (0.0-1.0), position in global interpolation space
            snapshot_index: int, which snapshot this represents
            snapshot_state: dict, captured parameter states
                Format: {instance_id: {symbol: value}}
                Example: {"/BigMuffPi": {"Tone": 0.35, "Level": 0.72}}
        """
        self.position: float = position
        self.snapshot_index: int = snapshot_index
        self.snapshot_state: SnapshotStateDict = snapshot_state

    def __repr__(self) -> str:
        param_count = sum(len(params) for params in self.snapshot_state.values())
        return f"CollageStop(pos={self.position:.2f}, snap={self.snapshot_index}, params={param_count})"

    @staticmethod
    def build_diff_map(
        state_a: SnapshotStateDict, state_b: SnapshotStateDict, param_type_getter: ParameterTypeGetter
    ) -> DiffMapDict:
        """
        Build map of parameters that differ between two states.

        Args:
            state_a: State dict from snapshot A {instance_id: {symbol: value}}
            state_b: State dict from snapshot B {instance_id: {symbol: value}}
            param_type_getter: Function(instance_id, symbol) -> ParameterType

        Returns:
            Dict: {instance_id: {symbol: (val_a, val_b, param_type)}}
        """
        diff_map = {}

        # Get all instance_ids from both states
        all_instances = set(state_a.keys()) | set(state_b.keys())

        for instance_id in all_instances:
            params_a = state_a.get(instance_id, {})
            params_b = state_b.get(instance_id, {})

            # Get all parameter symbols
            all_symbols = set(params_a.keys()) | set(params_b.keys())

            for symbol in all_symbols:
                val_a = params_a.get(symbol, 0.0)
                val_b = params_b.get(symbol, 0.0)

                # Only include if values differ
                if val_a != val_b:
                    # Get parameter type
                    param_type = param_type_getter(instance_id, symbol)

                    if instance_id not in diff_map:
                        diff_map[instance_id] = {}

                    diff_map[instance_id][symbol] = (val_a, val_b, param_type)

        return diff_map

    @staticmethod
    def adjust_binary_params(diff_map: DiffMapDict) -> DiffMapDict:
        """
        Apply "on wins" logic to binary parameters.

        If either value is 1.0 (on), set both to 1.0.
        If both are 0.0 (off), set both to 0.0.

        Args:
            diff_map: Parameter diff map {instance_id: {symbol: (val_a, val_b, param_type)}}

        Returns:
            Adjusted diff map with same structure
        """
        adjusted: DiffMapDict = {}

        for instance_id, params in diff_map.items():
            adjusted[instance_id] = {}

            for symbol, (val_a, val_b, param_type) in params.items():
                # Check if binary parameter (TOGGLED type or :bypass)
                is_binary = param_type == ParameterType.TOGGLED or symbol == ":bypass"

                if is_binary:
                    val_a = val_b = max(val_a, val_b)

                adjusted[instance_id][symbol] = (val_a, val_b, param_type)

        return adjusted

    @staticmethod
    def build_enriched_diff_map(
        lower_stop: 'CollageStop',
        upper_stop: 'CollageStop',
        stops: list['CollageStop'],
        segment_idx: int,
        param_type_getter: ParameterTypeGetter,
    ) -> EnrichedDiffMap:
        """
        Build enriched diff map with pre-computed neighbor data.

        For each parameter that differs between lower and upper stops,
        includes values from neighboring stops for hermite/catmull-rom interpolation.
        Pre-computes all data needed for interpolation to optimize the critical path.

        Args:
            lower_stop: Lower stop of segment
            upper_stop: Upper stop of segment
            stops: Complete list of all stops (for neighbor lookup)
            segment_idx: Index of this segment in stops list
            param_type_getter: Function to get parameter type

        Returns:
            Enriched diff map: {instance_id: {symbol: ParamData}}
        """
        # Build basic diff map (only parameters that differ)
        diff_map = CollageStop.build_diff_map(
            lower_stop.snapshot_state,
            upper_stop.snapshot_state,
            param_type_getter
        )

        # Apply binary parameter adjustment ("on wins" logic)
        diff_map = CollageStop.adjust_binary_params(diff_map)

        # Enrich with neighbor data for hermite/catmull-rom interpolation
        enriched: EnrichedDiffMap = {}
        segment_range = upper_stop.position - lower_stop.position

        for instance_id, params in diff_map.items():
            enriched[instance_id] = {}

            for symbol, (val_a, val_b, param_type) in params.items():
                # Get neighbor values for hermite/catmull-rom tangent calculation
                prev_val: float | None = None
                if segment_idx > 0:
                    prev_val = stops[segment_idx - 1].snapshot_state.get(
                        instance_id, {}
                    ).get(symbol, val_a)

                next_val: float | None = None
                if segment_idx < len(stops) - 2:
                    next_val = stops[segment_idx + 2].snapshot_state.get(
                        instance_id, {}
                    ).get(symbol, val_b)

                # Create ParamData with all pre-computed values
                enriched[instance_id][symbol] = ParamData(
                    val_a=val_a,
                    val_b=val_b,
                    prev_val=prev_val,
                    next_val=next_val,
                    segment_range=segment_range,
                    param_type=param_type,
                )

        return enriched
