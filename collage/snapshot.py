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

import copy
import json
import logging
import requests as req
from pathlib import Path

from collage.stop import CollageStop
from collage.types import (
    CollageConfig,
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
    def map_instance_to_key(instance_id: str) -> str:
        """Convert instance_id to snapshot key by stripping leading '/'."""
        return instance_id.lstrip('/')

    @staticmethod
    def map_key_to_instance(key: str) -> str:
        """Convert snapshot key to instance_id by adding leading '/'."""
        return f"/{key}"

    @staticmethod
    def sync_collage_snapshot(
        bundle_path: Path,
        collage_config: CollageConfig | None,
        root_uri: str
    ) -> int | None:
        """
        Sync Collage Mode snapshot with current configuration.

        If collage mode enabled: Recreate snapshot from first stop (prevents drift)
        If collage mode disabled/missing: Remove snapshot if it exists

        Args:
            bundle_path: Path to pedalboard bundle directory
            collage_config: Collage mode config dict, or None if not configured
            root_uri: MOD-UI root URI for snapshot reload notifications

        Returns:
            Snapshot index if created, None if removed or not created

        Raises:
            FileNotFoundError: If snapshots.json doesn't exist
            ValueError: If config is invalid
        """
        snapshots_file = bundle_path / "snapshots.json"
        snapshots_data = SnapshotManager.read_snapshots_file(bundle_path)

        # Determine snapshot name
        snapshot_name = 'Collage Mode'
        if collage_config:
            snapshot_name = collage_config.get('snapshot_name', 'Collage Mode')

        # Find existing Collage Mode snapshot
        existing_idx = None
        for i, snapshot in enumerate(snapshots_data.get('snapshots', [])):
            if snapshot.get('name') == snapshot_name:
                existing_idx = i
                break

        # Check if collage mode is enabled
        enabled = collage_config.get('enabled', False) if collage_config else False

        if not enabled:
            # Remove snapshot if it exists
            if existing_idx is not None:
                logging.info(f"Removing '{snapshot_name}' snapshot (collage mode disabled)")
                snapshots_data['snapshots'].pop(existing_idx)

                # Write updated snapshots
                with open(snapshots_file, 'w') as f:
                    json.dump(snapshots_data, f, indent=4)

                # Notify MOD-UI
                SnapshotManager._notify_mod_ui(root_uri)

            return None

        # Collage mode enabled - recreate snapshot

        # Remove old snapshot if exists
        if existing_idx is not None:
            logging.debug(f"Removing old '{snapshot_name}' snapshot for recreation")
            snapshots_data['snapshots'].pop(existing_idx)

        # Get first stop snapshot to copy
        snapshot_stops = collage_config.get('snapshot_stops', {})
        if len(snapshot_stops) < 2:
            raise ValueError(f"Collage mode requires at least 2 stops, got {len(snapshot_stops)}")

        sorted_stops = sorted(snapshot_stops.items(), key=lambda x: float(x[0]))
        first_identifier = sorted_stops[0][1]

        first_stop_index = SnapshotManager.resolve_snapshot_identifier(snapshots_data, first_identifier)
        first_stop_snapshot = snapshots_data['snapshots'][first_stop_index]

        # Create new snapshot by deep copying first stop
        logging.info(f"Creating '{snapshot_name}' snapshot from '{first_stop_snapshot['name']}'")
        collage_snapshot: SnapshotData = {
            'name': snapshot_name,
            'data': copy.deepcopy(first_stop_snapshot['data'])
        }

        # Append new snapshot
        snapshots_data['snapshots'].append(collage_snapshot)
        new_idx = len(snapshots_data['snapshots']) - 1

        # Write updated snapshots
        with open(snapshots_file, 'w') as f:
            json.dump(snapshots_data, f, indent=4)

        logging.info(f"Created '{snapshot_name}' snapshot at index {new_idx}")

        # Notify MOD-UI
        SnapshotManager._notify_mod_ui(root_uri)

        return new_idx

    @staticmethod
    def get_snapshots_file_timestamp(bundle_path: Path) -> float:
        """
        Get modification timestamp of snapshots.json file.

        Args:
            bundle_path: Path to pedalboard bundle directory

        Returns:
            Modification timestamp (Unix epoch), or 0 if file doesn't exist
        """
        import os
        snapshots_file = bundle_path / "snapshots.json"
        try:
            return os.path.getmtime(snapshots_file)
        except FileNotFoundError:
            return 0.0

    @staticmethod
    def _notify_mod_ui(root_uri: str) -> None:
        """Notify MOD-UI to reload snapshots."""
        try:
            url = root_uri + "snapshot/list"
            resp = req.get(url)
            if resp.status_code != 200:
                logging.warning(f"Failed to reload snapshots in MOD-UI: status {resp.status_code}")
            else:
                logging.debug("MOD-UI snapshots reloaded")
        except Exception as e:
            logging.warning(f"Failed to notify MOD-UI: {e}")
