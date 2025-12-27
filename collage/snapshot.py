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

"""Snapshot file operations for collage mode."""

import json
import logging
from pathlib import Path

from collage.stop import CollageStop
from collage.types import (
    ParameterTypeGetter,
    PluginData,
    SnapshotData,
    SnapshotsJson,
    SnapshotStateDict,
)


class SnapshotManager:
    """Handles reading, parsing, and creating snapshots."""

    @staticmethod
    def read_snapshots_file(bundle_path: Path) -> SnapshotsJson:
        """
        Read and parse snapshots.json file.

        Args:
            bundle_path: Path to pedalboard bundle directory

        Returns:
            Parsed JSON dict

        Raises:
            FileNotFoundError: If snapshots.json doesn't exist
            ValueError: If JSON is malformed
        """
        snapshots_file = bundle_path / "snapshots.json"

        if not snapshots_file.exists():
            raise FileNotFoundError(f"snapshots.json not found: {snapshots_file}")

        try:
            with open(snapshots_file, 'r') as f:
                data = json.load(f)
            logging.debug(f"Read snapshots.json with {len(data.get('snapshots', []))} snapshots")
            return data
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON in snapshots.json: {e}")

    @staticmethod
    def resolve_snapshot_identifier(snapshots_json: SnapshotsJson, identifier: int | str) -> int:
        """
        Resolve snapshot identifier (index or name) to index.

        Supports:
        - Integer index (0-based)
        - String name with case-insensitive prefix matching

        Args:
            snapshots_json: Parsed snapshots.json dict
            identifier: Snapshot index or name (or prefix)

        Returns:
            Snapshot index (0-based)

        Raises:
            ValueError: If identifier cannot be resolved
        """
        snapshots = snapshots_json.get('snapshots', [])

        # If integer, validate and return
        if isinstance(identifier, int):
            if identifier < 0 or identifier >= len(snapshots):
                raise ValueError(f"Snapshot index {identifier} out of range (0-{len(snapshots)-1})")
            return identifier

        # If string, do case-insensitive prefix match
        identifier_lower = identifier.lower()
        matches = []

        for i, snapshot in enumerate(snapshots):
            name = snapshot.get('name', '')
            if name.lower().startswith(identifier_lower):
                matches.append((i, name))

        if len(matches) == 0:
            # Show available snapshots for helpful error message
            available = [f"{i}: {s.get('name', '')}" for i, s in enumerate(snapshots)]
            raise ValueError(
                f"No snapshot found matching '{identifier}'. "
                f"Available: {', '.join(available)}"
            )

        if len(matches) > 1:
            # Multiple matches - show them for disambiguation
            match_list = [f"{i}: {name}" for i, name in matches]
            raise ValueError(
                f"Ambiguous snapshot name '{identifier}' matches multiple snapshots: "
                f"{', '.join(match_list)}"
            )

        # Exactly one match
        index, name = matches[0]
        logging.debug(f"Resolved snapshot '{identifier}' to index {index} ('{name}')")
        return index

    @staticmethod
    def parse_snapshot_data(snapshots_json: SnapshotsJson, snapshot_index: int) -> SnapshotStateDict:
        """
        Parse snapshot data and extract parameter values.

        Args:
            snapshots_json: Parsed snapshots.json dict
            snapshot_index: Index of snapshot to extract

        Returns:
            Dict of parameter states: {instance_id: {symbol: value}}

        Raises:
            IndexError: If snapshot_index is out of range
        """
        snapshots = snapshots_json.get('snapshots', [])

        if snapshot_index >= len(snapshots):
            raise IndexError(f"Snapshot index {snapshot_index} out of range (max: {len(snapshots) - 1})")

        snapshot = snapshots[snapshot_index]
        snapshot_data = snapshot.get('data', {})
        state = {}

        # Iterate through plugins in snapshot
        for plugin_symbol, plugin_data in snapshot_data.items():
            instance_id = SnapshotManager.map_key_to_instance(plugin_symbol)

            # Extract parameter values from ports
            ports = plugin_data.get('ports', {})
            bypassed = plugin_data.get('bypassed', False)

            params = {}
            for param_symbol, value in ports.items():
                params[param_symbol] = value

            # Add bypass state as :bypass parameter
            params[':bypass'] = 0.0 if bypassed else 1.0

            state[instance_id] = params

        logging.debug(f"Parsed snapshot {snapshot_index}: {len(state)} plugins")
        return state

    @staticmethod
    def create_sparse_snapshot(
        snapshots_data: SnapshotsJson,
        first_stop_index: int,
        second_stop_index: int,
        param_type_getter: ParameterTypeGetter,
        snapshot_name: str = 'Collage Mode'
    ) -> SnapshotData:
        """
        Create sparse snapshot with only non-interpolated parameters.

        This prevents parameter drift when users edit the stop snapshots. Only
        parameters that DON'T differ between stops are included. Interpolated
        parameters are omitted and will use current/default values (immediately
        overridden by midi_map).

        Args:
            snapshots_data: Parsed snapshots.json dict
            first_stop_index: Index of first stop snapshot
            second_stop_index: Index of second stop snapshot
            param_type_getter: Function(instance_id, symbol) -> ParameterType
            snapshot_name: Name for the created snapshot

        Returns:
            Snapshot dict with sparse data
        """
        # Parse snapshot states
        state_a = SnapshotManager.parse_snapshot_data(snapshots_data, first_stop_index)
        state_b = SnapshotManager.parse_snapshot_data(snapshots_data, second_stop_index)

        # Build diff map to identify interpolated parameters
        diff_map = CollageStop.build_diff_map(state_a, state_b, param_type_getter)
        diff_map = CollageStop.adjust_binary_params(diff_map)

        # Get first stop snapshot as base
        base_snapshot = snapshots_data['snapshots'][first_stop_index]
        collage_data: dict[str, PluginData] = {}

        # Build sparse snapshot
        for plugin_symbol, plugin_data in base_snapshot['data'].items():
            instance_id = SnapshotManager.map_key_to_instance(plugin_symbol)

            # Copy plugin structure
            collage_plugin: PluginData = {
                'bypassed': plugin_data.get('bypassed', False),
                'parameters': {},
                'ports': {},
                'preset': plugin_data.get('preset', '')
            }

            # Add optional bpm/bpb if present
            if 'bpm' in plugin_data:
                collage_plugin['bpm'] = plugin_data['bpm']
            if 'bpb' in plugin_data:
                collage_plugin['bpb'] = plugin_data['bpb']

            # Include only NON-interpolated parameters
            for param_symbol, value in plugin_data.get('ports', {}).items():
                # Check if this parameter is interpolated (in diff_map)
                is_interpolated = (
                    instance_id in diff_map and
                    param_symbol in diff_map[instance_id]
                )

                if not is_interpolated:
                    # Not interpolated - include in sparse snapshot
                    collage_plugin['ports'][param_symbol] = value
                # else: Interpolated - omit from snapshot (midi_map will handle it)

            collage_data[plugin_symbol] = collage_plugin

        collage_snapshot: SnapshotData = {
            'name': snapshot_name,
            'data': collage_data
        }

        logging.debug(f"Created sparse collage snapshot with {len(collage_data)} plugins")
        return collage_snapshot

    @staticmethod
    def map_instance_to_key(instance_id: str) -> str:
        """Convert instance_id to snapshot key by stripping leading '/'."""
        return instance_id.lstrip('/')

    @staticmethod
    def map_key_to_instance(key: str) -> str:
        """Convert snapshot key to instance_id by adding leading '/'."""
        return f"/{key}"
