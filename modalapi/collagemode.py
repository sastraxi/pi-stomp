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
from typing import Any, Literal, NotRequired, TypedDict
from rtmidi.midiconstants import CONTROL_CHANGE

from modalapi.collagestop import (
    CollageStop,
    SnapshotStateDict,
    DiffMapDict,
    EasingFunc,
    InterpolationFunc,
    linear_easing,
    ease_in_quad,
    ease_out_quad,
    ease_in_out_quad,
    ease_in_cubic,
    ease_out_cubic,
    ease_in_out_cubic,
    exponential_easing,
    sine_easing,
    linear_interpolation,
    hermite_interpolation,
    catmull_rom_interpolation,
)
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
        self.current_segment: int = 0  # Current segment index

        # Mode selection
        self.mode: Literal['segment', 'parameter'] = 'segment'  # Default to segment mode

        # Segment mode: easing function
        self.easing_func: EasingFunc = linear_easing  # Default to linear

        # Parameter mode: interpolation function and virtual CCs
        self.interpolation_func: InterpolationFunc = linear_interpolation  # Default to linear
        self.virtual_cc_mappings: dict[str, int] = {}  # Maps "instance_id:symbol" -> CC number
        self.virtual_midi_channel: int = 15  # Default to channel 15

        # Expression pedal hijacking (always hijacked now)
        self.hijacked_control: Any = None  # AnalogMidiControl
        self.original_refresh: Any = None  # Original refresh method

    def validate_config(self) -> None:
        """
        Validate config and set mode-specific attributes.

        Parses mode, easing, interpolation, and virtual_midi_channel from config.
        Sets self.mode, self.easing_func, self.interpolation_func, and self.virtual_midi_channel.

        Raises:
            ValueError: If config is invalid
        """
        # Easing function name -> function mapping
        easing_funcs: dict[str, EasingFunc] = {
            'linear': linear_easing,
            'ease_in_quad': ease_in_quad,
            'ease_out_quad': ease_out_quad,
            'ease_in_out_quad': ease_in_out_quad,
            'ease_in_cubic': ease_in_cubic,
            'ease_out_cubic': ease_out_cubic,
            'ease_in_out_cubic': ease_in_out_cubic,
            'exponential': exponential_easing,
            'sine': sine_easing,
        }

        # Interpolation function name -> function mapping
        interp_funcs: dict[str, InterpolationFunc] = {
            'linear': linear_interpolation,
            'hermite': hermite_interpolation,
            'catmull_rom': catmull_rom_interpolation,
        }

        # Parse mode (defaults to 'segment')
        mode_str = self.config.get('mode', 'segment')
        if mode_str not in ('segment', 'parameter'):
            raise ValueError(f"Invalid mode '{mode_str}', must be 'segment' or 'parameter'")
        self.mode = mode_str  # type: ignore

        # Segment mode: parse easing function
        if self.mode == 'segment':
            easing_name = self.config.get('easing', 'linear')
            if easing_name not in easing_funcs:
                raise ValueError(
                    f"Invalid easing function '{easing_name}', "
                    f"must be one of: {', '.join(easing_funcs.keys())}"
                )
            self.easing_func = easing_funcs[easing_name]
            logging.debug(f"Segment mode: using {easing_name} easing")

        # Parameter mode: parse interpolation function and virtual channel
        elif self.mode == 'parameter':
            # Parse interpolation function
            interp_name = self.config.get('interpolation', 'linear')
            if interp_name not in interp_funcs:
                raise ValueError(
                    f"Invalid interpolation function '{interp_name}', "
                    f"must be one of: {', '.join(interp_funcs.keys())}"
                )
            self.interpolation_func = interp_funcs[interp_name]

            # Parse virtual MIDI channel (required for parameter mode)
            virtual_channel = self.config.get('virtual_midi_channel', 15)
            if not isinstance(virtual_channel, int) or virtual_channel < 0 or virtual_channel > 15:
                raise ValueError(
                    f"Invalid virtual_midi_channel {virtual_channel}, must be integer 0-15"
                )
            self.virtual_midi_channel = virtual_channel

            logging.debug(
                f"Parameter mode: using {interp_name} interpolation on MIDI channel {virtual_channel}"
            )

    def initialize(self) -> None:
        """
        Initialize collage mode.

        Segment mode:
            1. Validate config and parse easing function
            2. Parse stops and snapshots
            3. Apply MIDI mappings for initial segment
            4. Hijack expression pedal

        Parameter mode:
            1. Validate config and parse interpolation function
            2. Parse stops and snapshots
            3. Build virtual CC mappings
            4. Hijack expression pedal
        """
        logging.info("Initializing collage mode...")

        try:
            # Validate configuration and set mode-specific attributes
            self.validate_config()

            # Get snapshot_stops configuration
            snapshot_stops = self.config.get('snapshot_stops', {})
            if len(snapshot_stops) < 2:
                raise ValueError(f"Collage mode requires at least 2 stops, got {len(snapshot_stops)}")

            # Read snapshots file
            bundle_path = Path(self.handler.current.pedalboard.bundle)
            snapshots_data = self.read_snapshots_file(bundle_path)

            # Parse and validate snapshot_stops entries
            stops_data: list[tuple[float, int]] = []  # [(position, snapshot_index), ...]

            for position_str, snapshot_identifier in snapshot_stops.items():
                # Validate position is a stringified float
                try:
                    position = float(position_str)
                except ValueError:
                    raise ValueError(
                        f"Invalid position key '{position_str}': must be a stringified float (e.g., '0.0', '0.5')"
                    )

                # Validate position is in range [0.0, 1.0]
                if position < 0.0 or position > 1.0:
                    raise ValueError(f"Position {position} out of range: must be between 0.0 and 1.0")

                # Resolve snapshot identifier (index or name) to index
                snapshot_index = self.resolve_snapshot_identifier(snapshots_data, snapshot_identifier)

                stops_data.append((position, snapshot_index))

            # Sort by position
            stops_data.sort(key=lambda x: x[0])

            # Create CollageStop objects
            for position, snapshot_index in stops_data:
                state = self.parse_snapshot_data(snapshots_data, snapshot_index)
                stop = CollageStop(position, snapshot_index, state)
                self.stops.append(stop)
                logging.debug(f"Created {stop}")

            # Validate we have at least 2 stops
            if len(self.stops) < 2:
                raise ValueError(f"Need at least 2 stops, got {len(self.stops)}")

            # Limit to 4 stops for practical reasons
            if len(self.stops) > 4:
                logging.warning(f"Limiting to 4 stops (got {len(self.stops)})")
                self.stops = self.stops[:4]

            # Sort stops by position
            self.stops.sort(key=lambda s: s.position)

            # Validate stops are monotonic and distinguishable at MIDI CC resolution
            for i in range(len(self.stops) - 1):
                pos_a = self.stops[i].position
                pos_b = self.stops[i + 1].position

                # Check positions are strictly increasing
                if pos_a >= pos_b:
                    raise ValueError(
                        f"Stop positions must be strictly increasing: "
                        f"stop {i} at {pos_a}, stop {i+1} at {pos_b}"
                    )

                # Check positions map to different CC values (MIDI resolution check)
                cc_a = int(pos_a * 127)
                cc_b = int(pos_b * 127)
                if cc_a == cc_b:
                    raise ValueError(
                        f"Stop positions too close - both map to CC {cc_a}: "
                        f"stop {i} at {pos_a}, stop {i+1} at {pos_b}. "
                        f"Minimum separation is {1.0/127:.6f}"
                    )

            # Mode-specific initialization
            if self.mode == 'segment':
                # Segment mode: apply MIDI mappings for initial segment
                self.current_segment = 0
                self.apply_midi_mappings()
                logging.info("Segment mode: applied mappings for initial segment")

            elif self.mode == 'parameter':
                # Parameter mode: build virtual CC mappings
                self.build_virtual_cc_mappings()
                logging.info(f"Parameter mode: created {len(self.virtual_cc_mappings)} virtual CC mappings")

            # Always hijack expression pedal (both modes, all stop counts)
            self.hijack_expression_pedal()

            self.enabled = True
            logging.info(f"Collage mode initialized with {len(self.stops)} stops in {self.mode} mode")

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

    def resolve_snapshot_identifier(self, snapshots_json: SnapshotsJson, identifier: int | str) -> int:
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

    def apply_midi_mappings(self, segment_index: int | None = None) -> None:
        """
        Calculate parameter diffs and send midi_map commands to mod-host.

        Maps parameters to expression pedal CC with min/max from current segment.
        For multi-stop mode, this is called initially and whenever segment changes.

        Args:
            segment_index: Segment to map (defaults to self.current_segment)
        """
        if segment_index is None:
            segment_index = self.current_segment

        # Get stops for this segment
        stop_a = self.stops[segment_index]
        stop_b = self.stops[segment_index + 1]

        # Get expression pedal config from hardware
        exp_pedal_id = self.config.get('expression_pedal_id', 0)
        exp_channel, exp_cc = self.get_expression_pedal_config(exp_pedal_id)

        # Calculate parameter diffs for this segment
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

                for symbol, (val_a, val_b, _param_type) in params.items():
                    try:
                        sock.midi_map(instance_num, symbol, exp_channel, exp_cc, val_a, val_b)

                        # Only track on initial mapping (segment 0)
                        if segment_index == 0 and (instance_num, symbol) not in self.mapped_parameters:
                            self.mapped_parameters.append((instance_num, symbol))

                        logging.debug(f"Mapped {instance_id}/{symbol}: {val_a} -> {val_b} (segment {segment_index})")
                    except Exception as e:
                        logging.warning(f"Failed to map {instance_id}/{symbol}: {e}")

        logging.info(f"Applied MIDI mappings for segment {segment_index} ({len(diff_map)} plugins)")

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

    def get_segment_from_cc(self, cc_value: int) -> int:
        """
        Determine which segment the CC value falls into.

        Segments change at exact stop positions. CC range is 0-127.

        Args:
            cc_value: MIDI CC value (0-127)

        Returns:
            Segment index (0 to len(stops)-2)
        """
        # Convert CC (0-127) to percentage (0.0-1.0)
        percentage = cc_value / 127.0

        # Find which segment this percentage falls into
        for i in range(len(self.stops) - 1):
            if percentage < self.stops[i + 1].position:
                return i

        # At or beyond last stop - use last segment
        return len(self.stops) - 2

    def build_virtual_cc_mappings(self) -> None:
        """
        Build virtual CC mappings for parameter mode.

        Assigns a unique CC number to each parameter that varies across stops,
        then sends midi_map commands to mod-host to map those virtual CCs to parameters.

        Virtual CCs start at 70 and increment for each parameter.
        Populates self.virtual_cc_mappings dict: "instance_id:symbol" -> CC number
        """
        # Collect all unique parameters across all stops
        all_params: set[str] = set()

        for stop in self.stops:
            for instance_id, params in stop.snapshot_state.items():
                for symbol in params.keys():
                    param_key = f"{instance_id}:{symbol}"
                    all_params.add(param_key)

        # Assign virtual CC numbers starting at 70
        # (Avoids common controller CCs like 1-31, leaves room for expansion)
        next_cc = 70
        for param_key in sorted(all_params):  # Sort for deterministic ordering
            self.virtual_cc_mappings[param_key] = next_cc
            next_cc += 1

            if next_cc > 127:
                raise ValueError(
                    f"Too many parameters for virtual CC mapping (max 58, need {len(all_params)})"
                )

        logging.debug(f"Assigned virtual CCs 70-{next_cc-1} to {len(self.virtual_cc_mappings)} parameters")

        # Send midi_map commands to mod-host for all virtual CCs
        # Maps: virtual CC (on virtual channel) -> parameter (full range 0.0-1.0)
        with ModHostSocket() as sock:
            for param_key, cc_num in self.virtual_cc_mappings.items():
                # Parse param_key: "instance_id:symbol"
                instance_id, symbol = param_key.split(':', 1)

                # Get instance number
                instance_num = self.get_instance_number(instance_id)
                if instance_num is None:
                    logging.warning(f"Plugin {instance_id} not found, skipping virtual CC mapping")
                    continue

                # Map virtual CC to parameter (full range 0.0-1.0)
                try:
                    sock.midi_map(instance_num, symbol, self.virtual_midi_channel, cc_num, 0.0, 1.0)
                    # Track for cleanup
                    self.mapped_parameters.append((instance_num, symbol))
                    logging.debug(f"Mapped virtual CC {cc_num} to {instance_id}/{symbol}")
                except Exception as e:
                    logging.warning(f"Failed to map virtual CC for {param_key}: {e}")

    def hijack_expression_pedal(self) -> None:
        """
        Hijack expression pedal refresh() for monitoring and transforming CC values.

        Stores original refresh method and replaces it with hijacked_refresh.
        Used in both segment mode (easing + segment switching) and parameter mode (interpolation).
        """
        exp_pedal_id = self.config.get('expression_pedal_id', 0)

        # Find expression pedal control
        for control in self.handler.hardware.analog_controls:
            if hasattr(control, 'id') and control.id == exp_pedal_id:
                self.hijacked_control = control
                self.original_refresh = control.refresh
                control.refresh = self.hijacked_refresh
                logging.info(f"Hijacked expression pedal {exp_pedal_id} for {self.mode} mode")
                return

        raise ValueError(f"Expression pedal {exp_pedal_id} not found for hijacking")

    def send_virtual_midi_cc(self, cc_num: int, value: int) -> None:
        """
        Send virtual MIDI CC message on the virtual channel.

        Used in parameter mode to send interpolated parameter values as MIDI CCs
        that mod-host will receive and apply to mapped parameters.

        Args:
            cc_num: MIDI CC number (70-127)
            value: MIDI CC value (0-127)
        """
        if not self.handler.hardware or not self.handler.hardware.midiout:
            logging.warning("send_virtual_midi_cc: midiout not available")
            return

        # Build MIDI CC message: [channel | CONTROL_CHANGE, cc_number, value]
        midi_msg = [self.virtual_midi_channel | CONTROL_CHANGE, cc_num, value]
        self.handler.hardware.midiout.send_message(midi_msg)
        logging.debug(f"Sent virtual MIDI CC: channel={self.virtual_midi_channel}, cc={cc_num}, value={value}")

    def hijacked_refresh(self) -> None:
        """
        Replacement for AnalogMidiControl.refresh().

        Handles both segment mode (easing + segment switching) and parameter mode
        (full interpolation with virtual CCs).

        Does NOT call original refresh - we send transformed MIDI ourselves.
        """
        # Read raw ADC value (but don't send MIDI yet)
        raw_value = self.hijacked_control.readChannel()
        value_changed = abs(raw_value - self.hijacked_control.last_read) > self.hijacked_control.tolerance

        if not value_changed:
            return

        # Convert ADC value to percentage (0.0-1.0)
        percentage = raw_value / 1023.0  # ADC is 10-bit (0-1023)

        if self.mode == 'segment':
            self._handle_segment_mode(percentage)
        elif self.mode == 'parameter':
            self._handle_parameter_mode(percentage)

        # Update last_read to prevent duplicate sends
        self.hijacked_control.last_read = raw_value

    def _handle_segment_mode(self, percentage: float) -> None:
        """
        Handle segment mode with easing.

        Applies easing function to transform the expression pedal value,
        then sends the eased CC. Also handles segment switching for multi-stop mode.

        Args:
            percentage: Global position (0.0-1.0)
        """
        # Determine current segment
        new_segment = self.get_segment_from_cc(int(percentage * 127))

        # Get segment boundaries
        lower_stop = self.stops[new_segment]
        upper_stop = self.stops[new_segment + 1]

        # Calculate local percentage within current segment
        segment_range = upper_stop.position - lower_stop.position
        if segment_range > 0:
            local_pct = (percentage - lower_stop.position) / segment_range
            # Clamp to [0, 1]
            local_pct = max(0.0, min(1.0, local_pct))
        else:
            local_pct = 0.0

        # Apply easing to local percentage
        eased_pct = self.easing_func(local_pct)

        # Convert eased percentage back to global percentage within segment
        eased_global_pct = lower_stop.position + (eased_pct * segment_range)

        # Convert to CC value (0-127)
        eased_cc_value = int(eased_global_pct * 127)
        eased_cc_value = max(0, min(127, eased_cc_value))  # Clamp

        # Send the eased CC value on the expression pedal's channel/CC
        exp_channel = self.hijacked_control.midi_channel
        exp_cc = self.hijacked_control.midi_CC
        midi_msg = [exp_channel | CONTROL_CHANGE, exp_cc, eased_cc_value]
        self.handler.hardware.midiout.send_message(midi_msg)

        # If segment changed, update MIDI mappings
        if new_segment != self.current_segment:
            logging.debug(f"Segment change: {self.current_segment} -> {new_segment}")
            self.current_segment = new_segment
            self.apply_midi_mappings(new_segment)

    def _handle_parameter_mode(self, percentage: float) -> None:
        """
        Handle parameter mode with full interpolation.

        Computes interpolated state across all stops and sends virtual MIDI CCs
        for each parameter.

        Args:
            percentage: Global position (0.0-1.0)
        """
        # Call interpolation function to get complete interpolated state
        interpolated_state = self.interpolation_func(percentage, self.stops)

        # Send virtual MIDI CC for each parameter
        for instance_id, params in interpolated_state.items():
            for symbol, value in params.items():
                param_key = f"{instance_id}:{symbol}"

                # Get virtual CC number for this parameter
                cc_num = self.virtual_cc_mappings.get(param_key)
                if cc_num is None:
                    logging.warning(f"No virtual CC mapping for {param_key}, skipping")
                    continue

                # Scale parameter value (0.0-1.0) to MIDI CC value (0-127)
                cc_value = int(value * 127)
                cc_value = max(0, min(127, cc_value))  # Clamp

                # Send virtual MIDI CC
                self.send_virtual_midi_cc(cc_num, cc_value)

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
        snapshot_stops = self.config.get('snapshot_stops', {})
        if len(snapshot_stops) < 2:
            raise ValueError(f"Need at least 2 stops, got {len(snapshot_stops)}")

        # Get first two snapshot identifiers (sorted by position)
        sorted_stops = sorted(snapshot_stops.items(), key=lambda x: float(x[0]))
        first_identifier = sorted_stops[0][1]
        second_identifier = sorted_stops[1][1]

        # Read snapshots to resolve identifiers
        bundle_path = Path(self.handler.current.pedalboard.bundle)
        snapshots_data = self.read_snapshots_file(bundle_path)

        # Resolve snapshot identifiers to indices
        first_stop_index = self.resolve_snapshot_identifier(snapshots_data, first_identifier)
        second_stop_index = self.resolve_snapshot_identifier(snapshots_data, second_identifier)

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
        - Restore hijacked expression pedal
        - Unmap all MIDI mappings
        - Reset state
        """
        if not self.enabled:
            return

        logging.info("Cleaning up collage mode...")

        # Restore hijacked expression pedal (multi-stop mode)
        if hasattr(self, 'hijacked_control') and hasattr(self, 'original_refresh'):
            self.hijacked_control.refresh = self.original_refresh
            logging.debug("Restored expression pedal refresh method")

        # Unmap MIDI mappings
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
