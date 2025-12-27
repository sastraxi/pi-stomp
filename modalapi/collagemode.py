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

import json
import logging
import requests as req
import socket
from pathlib import Path
from typing import Any, NotRequired, TypedDict
from modalapi.collagestop import CollageStop, SnapshotStateDict, DiffMapDict
from modalapi.parameter import Type as ParameterType


# Config TypedDicts
class StopConfig(TypedDict):
    """Configuration for a single collage stop."""
    snapshot: int
    position: NotRequired[float]


class CollageConfig(TypedDict):
    """Complete collage mode configuration from YAML."""
    enabled: bool
    expression_pedal_id: NotRequired[int]
    stops: list[StopConfig]
    throttle_ms: NotRequired[int]


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


class ModHostSocket:
    """Client for communicating with mod-host via TCP socket."""

    def __init__(self, host: str = 'localhost', port: int = 5555) -> None:
        """
        Initialize mod-host socket client.

        Args:
            host: mod-host hostname (default: localhost)
            port: mod-host socket port (default: 5555)
        """
        self.host: str = host
        self.port: int = port
        self.sock: socket.socket | None = None

    def connect(self) -> None:
        """Establish connection to mod-host socket."""
        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.sock.connect((self.host, self.port))
            logging.info(f"Connected to mod-host at {self.host}:{self.port}")
        except Exception as e:
            logging.error(f"Failed to connect to mod-host: {e}")
            raise

    def close(self) -> None:
        """Close connection to mod-host socket."""
        if self.sock:
            self.sock.close()
            self.sock = None
            logging.debug("Closed mod-host socket connection")

    def send_command(self, cmd: str) -> str:
        """
        Send command to mod-host and return response.

        Args:
            cmd: Command string to send

        Returns:
            Response string from mod-host

        Raises:
            RuntimeError: If not connected or command fails
        """
        if not self.sock:
            raise RuntimeError("Not connected to mod-host")

        try:
            self.sock.sendall(f"{cmd}\n".encode())
            response = self.sock.recv(4096).decode().strip()
            logging.debug(f"mod-host command: {cmd} -> {response}")
            return response
        except Exception as e:
            logging.error(f"mod-host command failed: {cmd} ({e})")
            raise

    def midi_map(self, instance: int, symbol: str, channel: int, cc: int,
                 minimum: float, maximum: float) -> str:
        """
        Map MIDI CC to parameter.

        Args:
            instance: Plugin instance number (e.g., 0)
            symbol: Parameter symbol (e.g., "gain")
            channel: MIDI channel (0-15)
            cc: MIDI CC number (0-127)
            minimum: Minimum parameter value
            maximum: Maximum parameter value

        Returns:
            Response from mod-host
        """
        cmd = f'midi_map {instance} {symbol} {channel} {cc} {minimum} {maximum}'
        return self.send_command(cmd)

    def midi_unmap(self, instance: int, symbol: str) -> str:
        """
        Remove MIDI CC mapping from parameter.

        Args:
            instance: Plugin instance number
            symbol: Parameter symbol

        Returns:
            Response from mod-host
        """
        cmd = f'midi_unmap {instance} {symbol}'
        return self.send_command(cmd)

    def __enter__(self) -> 'ModHostSocket':
        """Context manager entry."""
        self.connect()
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Context manager exit."""
        self.close()


class CollageMode:
    """Manages collage mode: interpolating between snapshots via MIDI."""

    def __init__(self, handler: Any, config: CollageConfig) -> None:
        """
        Initialize collage mode manager.

        Args:
            handler: Reference to Modhandler instance
            config: Collage mode configuration dict from YAML
        """
        self.handler: Any = handler  # Modhandler - avoiding circular import
        self.config: CollageConfig = config
        self.enabled: bool = False
        self.stops: list[CollageStop] = []
        self.mapped_parameters: list[tuple[int, str]] = []  # Track what we've mapped for cleanup

    def initialize(self) -> None:
        """
        Initialize collage mode:
        1. Read snapshots.json
        2. Parse snapshot states
        3. Calculate parameter diffs
        4. Apply binary "on wins" logic
        5. Send midi_map commands to mod-host
        """
        logging.info("Initializing collage mode...")

        try:
            # Get configuration
            stop_configs = self.config.get('stops', [])
            if len(stop_configs) < 2:
                raise ValueError(f"Collage mode requires at least 2 stops, got {len(stop_configs)}")

            # Read snapshots file
            bundle_path = Path(self.handler.current.pedalboard.bundle)
            snapshots_data = self.read_snapshots_file(bundle_path)

            # Parse snapshot states for each stop
            for stop_config in stop_configs:
                snapshot_index = stop_config.get('snapshot')
                position = stop_config.get('position', None)

                # Auto-calculate position if not specified (evenly distributed)
                if position is None:
                    position = stop_configs.index(stop_config) / (len(stop_configs) - 1)

                # Parse snapshot state
                state = self.parse_snapshot_data(snapshots_data, snapshot_index)
                stop = CollageStop(position, snapshot_index, state)
                self.stops.append(stop)
                logging.debug(f"Created {stop}")

            # Validate we have 2 stops for initial implementation
            if len(self.stops) != 2:
                logging.warning(f"Multi-stop support not yet implemented, using first 2 stops only")
                self.stops = self.stops[:2]

            # Calculate parameter differences and apply MIDI mapping
            self.apply_midi_mappings()

            self.enabled = True
            logging.info(f"Collage mode initialized with {len(self.stops)} stops")

        except Exception as e:
            logging.error(f"Failed to initialize collage mode: {e}")
            self.enabled = False
            raise

    def read_snapshots_file(self, bundle_path: Path) -> SnapshotsJson:
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

    def parse_snapshot_data(self, snapshots_json: SnapshotsJson, snapshot_index: int) -> SnapshotStateDict:
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
            instance_id = self.map_key_to_instance(plugin_symbol)

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

    def map_instance_to_key(self, instance_id: str) -> str:
        """Convert instance_id to snapshot key by stripping leading '/'."""
        return instance_id.lstrip('/')

    def map_key_to_instance(self, key: str) -> str:
        """Convert snapshot key to instance_id by adding leading '/'."""
        return f"/{key}"

    def apply_midi_mappings(self) -> None:
        """
        Calculate parameter diffs and send midi_map commands to mod-host.

        For 2-stop mode, maps all differing parameters to the expression pedal's
        MIDI CC with min/max values from the two snapshots.
        """
        if len(self.stops) != 2:
            raise NotImplementedError("Only 2-stop mode currently supported")

        stop_a, stop_b = self.stops[0], self.stops[1]

        # Get expression pedal config from hardware
        exp_pedal_id = self.config.get('expression_pedal_id', 0)
        exp_channel, exp_cc = self.get_expression_pedal_config(exp_pedal_id)

        # Calculate parameter diffs
        diff_map = CollageStop.build_diff_map(
            stop_a.snapshot_state,
            stop_b.snapshot_state,
            self.get_parameter_type
        )

        # Apply binary "on wins" logic
        diff_map = CollageStop.adjust_binary_params(diff_map)

        # Send midi_map commands
        with ModHostSocket() as sock:
            for instance_id, params in diff_map.items():
                # Map instance_id to instance number (index in plugins list)
                instance_num = self.get_instance_number(instance_id)
                if instance_num is None:
                    logging.warning(f"Plugin {instance_id} not found in pedalboard, skipping")
                    continue

                for symbol, (val_a, val_b, param_type) in params.items():
                    try:
                        sock.midi_map(instance_num, symbol, exp_channel, exp_cc, val_a, val_b)
                        self.mapped_parameters.append((instance_num, symbol))
                        logging.debug(f"Mapped {instance_id}/{symbol}: {val_a} -> {val_b}")
                    except Exception as e:
                        logging.warning(f"Failed to map {instance_id}/{symbol}: {e}")

        logging.info(f"Applied {len(self.mapped_parameters)} MIDI mappings")

    def get_instance_number(self, instance_id: str) -> int | None:
        """
        Get mod-host instance number for a plugin.

        The instance number is the index in the pedalboard's plugins list.

        Args:
            instance_id: Plugin instance ID (e.g., "/BigMuffPi")

        Returns:
            Instance number (0-based index) or None if not found
        """
        for index, plugin in enumerate(self.handler.current.pedalboard.plugins):
            if plugin.instance_id == instance_id:
                return index
        return None

    def get_expression_pedal_config(self, pedal_id: int) -> tuple[int, int]:
        """
        Get MIDI channel and CC number for an expression pedal.

        Args:
            pedal_id: Expression pedal ID from config

        Returns:
            Tuple of (midi_channel, midi_cc)

        Raises:
            ValueError: If expression pedal not found
        """
        # Search analog controls for matching ID
        for control in self.handler.hardware.analog_controls:
            if hasattr(control, 'id') and control.id == pedal_id:
                return (control.midi_channel, control.midi_CC)

        raise ValueError(f"Expression pedal with ID {pedal_id} not found in hardware config")

    def get_parameter_type(self, instance_id: str, symbol: str) -> ParameterType:
        """
        Get parameter type from pedalboard data.

        Args:
            instance_id: Plugin instance ID
            symbol: Parameter symbol

        Returns:
            ParameterType enum value
        """
        # Find plugin by instance_id
        for plugin in self.handler.current.pedalboard.plugins:
            if plugin.instance_id == instance_id:
                param = plugin.parameters.get(symbol)
                if param:
                    return param.type

        # Default to DEFAULT type
        return ParameterType.DEFAULT

    def create_collage_snapshot(self, snapshots_data: SnapshotsJson) -> SnapshotData:
        """
        Create sparse "Collage Mode" snapshot with only non-interpolated parameters.

        This prevents parameter drift when users edit the stop snapshots. Only
        parameters that DON'T differ between stops are included. Interpolated
        parameters are omitted and will use current/default values (immediately
        overridden by midi_map).

        Args:
            snapshots_data: Parsed snapshots.json dict

        Returns:
            Snapshot dict with sparse data

        Raises:
            ValueError: If < 2 stops configured
        """
        stop_configs = self.config.get('stops', [])
        if len(stop_configs) < 2:
            raise ValueError(f"Need at least 2 stops, got {len(stop_configs)}")

        # Parse the two stop snapshots
        first_stop_index = stop_configs[0]['snapshot']
        second_stop_index = stop_configs[1]['snapshot']

        state_a = self.parse_snapshot_data(snapshots_data, first_stop_index)
        state_b = self.parse_snapshot_data(snapshots_data, second_stop_index)

        # Build diff map to identify interpolated parameters
        diff_map = CollageStop.build_diff_map(state_a, state_b, self.get_parameter_type)
        diff_map = CollageStop.adjust_binary_params(diff_map)

        # Get first stop snapshot as base
        base_snapshot = snapshots_data['snapshots'][first_stop_index]
        collage_data: dict[str, PluginData] = {}

        # Build sparse snapshot
        for plugin_symbol, plugin_data in base_snapshot['data'].items():
            instance_id = self.map_key_to_instance(plugin_symbol)

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

        snapshot_name = self.config.get('snapshot_name', 'Collage Mode')
        collage_snapshot: SnapshotData = {
            'name': snapshot_name,
            'data': collage_data
        }

        logging.debug(f"Created sparse collage snapshot with {len(collage_data)} plugins")
        return collage_snapshot

    def ensure_collage_snapshot(self) -> None:
        """
        Ensure "Collage Mode" snapshot exists in snapshots.json.

        Creates the snapshot if it doesn't exist, using sparse snapshot approach
        (only non-interpolated parameters). If it already exists, does nothing.

        Raises:
            FileNotFoundError: If snapshots.json doesn't exist
            ValueError: If JSON is malformed or config invalid
        """
        # Check if creation is enabled in config
        if not self.config.get('create_snapshot', True):
            logging.debug("Snapshot auto-creation disabled in config")
            return

        bundle_path = Path(self.handler.current.pedalboard.bundle)
        snapshots_file = bundle_path / "snapshots.json"

        # Read current snapshots.json
        snapshots_data = self.read_snapshots_file(bundle_path)
        snapshot_name = self.config.get('snapshot_name', 'Collage Mode')

        # Check if snapshot already exists
        for snapshot in snapshots_data.get('snapshots', []):
            if snapshot.get('name') == snapshot_name:
                logging.debug(f"'{snapshot_name}' snapshot already exists, skipping creation")
                return

        # Create sparse collage snapshot
        logging.info(f"Creating '{snapshot_name}' snapshot...")
        collage_snapshot = self.create_collage_snapshot(snapshots_data)

        # Append to snapshots list
        snapshots_data['snapshots'].append(collage_snapshot)

        # Write back to file
        try:
            with open(snapshots_file, 'w') as f:
                json.dump(snapshots_data, f, indent=4)
            logging.info(f"Created '{snapshot_name}' snapshot in snapshots.json")
        except Exception as e:
            raise IOError(f"Failed to write snapshots.json: {e}")

        # Notify MOD-UI to reload snapshots
        try:
            url = self.handler.root_uri + "snapshot/list"
            resp = req.get(url)
            if resp.status_code != 200:
                logging.warning(f"Failed to reload snapshots in MOD-UI: status {resp.status_code}")
            else:
                logging.debug("MOD-UI snapshots reloaded")
        except Exception as e:
            logging.warning(f"Failed to notify MOD-UI: {e}")

    def cleanup(self) -> None:
        """
        Clean up collage mode:
        - Unmap all MIDI mappings
        - Reset state
        """
        if not self.enabled:
            return

        logging.info("Cleaning up collage mode...")

        try:
            with ModHostSocket() as sock:
                for instance_num, symbol in self.mapped_parameters:
                    try:
                        sock.midi_unmap(instance_num, symbol)
                    except Exception as e:
                        logging.warning(f"Failed to unmap {instance_num}/{symbol}: {e}")
        except Exception as e:
            logging.error(f"Failed to cleanup MIDI mappings: {e}")

        self.mapped_parameters = []
        self.stops = []
        self.enabled = False
        logging.info("Collage mode cleaned up")
