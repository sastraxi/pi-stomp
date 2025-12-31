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

"""Main BlendMode coordinator class with pre-computation optimization."""

import logging
from pathlib import Path
from typing import Any

from blend.interpolation import (
    # Spline interpolation
    hermite_interpolation,
    catmull_rom_interpolation,
    # Easing-based interpolation
    linear_interpolation,
    ease_in_quad_interpolation,
    ease_out_quad_interpolation,
    ease_in_out_quad_interpolation,
    ease_in_cubic_interpolation,
    ease_out_cubic_interpolation,
    ease_in_out_cubic_interpolation,
    exponential_easing_interpolation,
    sine_easing_interpolation,
)
from blend.parameter_setter import ParameterSetter
from blend.input_controller import InputController
from blend.snapshot import SnapshotManager
from blend.stop import BlendStop
from blend.types import (
    BlendSnapshotConfig,
    EnrichedDiffMap,
    InterpolationFunc,
    MidiBoundParams,
    NormalizedStops,
    StopData,
)
from modalapi.parameter import Type as ParameterType


# Mapping of all interpolation function names
INTERPOLATION_FUNCTIONS: dict[str, InterpolationFunc] = {
    # Spline interpolation (uses neighbor context)
    "hermite": hermite_interpolation,
    "catmull_rom": catmull_rom_interpolation,
    # Easing-based interpolation (segment-local only)
    "linear": linear_interpolation,
    "ease_in_quad": ease_in_quad_interpolation,
    "ease_out_quad": ease_out_quad_interpolation,
    "ease_in_out_quad": ease_in_out_quad_interpolation,
    "ease_in_cubic": ease_in_cubic_interpolation,
    "ease_out_cubic": ease_out_cubic_interpolation,
    "ease_in_out_cubic": ease_in_out_cubic_interpolation,
    "exponential": exponential_easing_interpolation,
    "sine": sine_easing_interpolation,
}


class BlendMode:
    """
    Coordinates blend mode components with pre-computation optimization.

    Pre-computes enriched diff maps for each segment at initialization to
    minimize work in the critical path (10ms polling loop).
    """

    def __init__(self, handler: Any, config: BlendSnapshotConfig) -> None:
        """
        Initialize blend mode coordinator.

        Args:
            handler: Reference to Modhandler instance
            config: Single blend snapshot configuration dict from YAML
        """
        self.handler: Any = handler  # Modhandler - avoiding circular import
        self.config: BlendSnapshotConfig = config
        self.enabled: bool = False
        self.stops: list[BlendStop] = []
        self.segment_diff_maps: list[EnrichedDiffMap] = []

        # Components (initialized in initialize())
        self.parameter_setter: ParameterSetter | None = None
        self.input_controller: InputController | None = None

        # Snapshot file monitoring (for detecting stop modifications)
        self.snapshots_file_timestamp: float = 0

    def initialize(self) -> None:
        """
        Initialize blend mode with pre-computation optimization.

        Pre-computes enriched diff maps for each segment at initialization
        to minimize work in the critical path (pedal movement).

        Orchestrates initialization:
        1. Validate config and resolve interpolation function
        2. Load and parse snapshots
        3. Create stops
        4. PRE-COMPUTE: Build enriched diff maps for each segment
        5. Initialize parameter setter
        6. Initialize and attach pedal controller
        """
        logging.info("Initializing blend mode...")

        try:
            # Validate configuration and resolve interpolation function
            interpolation_func = self._validate_config()

            # Load snapshots and create stops
            self.stops = self._create_stops()

            # Extract MIDI-bound parameters to exclude from interpolation
            midi_bound_params = self._extract_midi_bound_parameters()

            # PRE-COMPUTE: Build enriched diff maps for each segment
            logging.info(f"Pre-computing diff maps for {len(self.stops) - 1} segments...")
            self.segment_diff_maps = []

            for segment_idx in range(len(self.stops) - 1):
                lower = self.stops[segment_idx]
                upper = self.stops[segment_idx + 1]

                # Build enriched diff map with neighbor data
                diff_map = BlendStop.build_enriched_diff_map(
                    lower,
                    upper,
                    self.stops,
                    segment_idx,
                    self._get_parameter_type,
                    midi_bound_params,
                )

                self.segment_diff_maps.append(diff_map)

                # Log summary
                param_count = sum(len(params) for params in diff_map.values())
                logging.debug(
                    f"  Segment {segment_idx} ({lower.position:.2f} -> {upper.position:.2f}): "
                    f"{param_count} differing parameters"
                )

            logging.info("Diff map pre-computation complete")

            # Initialize parameter setter (uses shared WebSocket bridge from handler)
            self.parameter_setter = ParameterSetter(self.handler.ws_bridge)

            # Initialize and attach input controller
            input_id = self.config.get("input_id")
            if input_id is None:
                raise ValueError("Blend mode requires 'input_id' config")

            self.input_controller = InputController(
                interpolation_func,
                self.stops,
                self.segment_diff_maps,  # Pre-computed!
                self.parameter_setter,
            )

            # Attach to analog input (expression pedal or encoder)
            self.input_controller.attach_to_input(
                self.handler.hardware.analog_controls, self.handler.hardware.encoders, input_id
            )

            # Sync current pedal position to trigger initial interpolation
            self.handler.hardware.sync_analog_controls()

            self.enabled = True
            logging.info(f"Blend mode initialized with {len(self.stops)} stops")

        except Exception as e:
            logging.error(f"Failed to initialize blend mode: {e}")
            self.enabled = False
            raise

    def _normalize_stops_config(self, stops_config: dict[str, int | str] | list[str | int]) -> NormalizedStops:
        """
        Normalize stops configuration to dict format.

        Converts list format to dict with evenly spaced positions.
        Example: ["A", "B", "C"] → {"0.0": "A", "0.5": "B", "1.0": "C"}

        Args:
            stops_config: Stops in dict or list format

        Returns:
            Normalized stops as dict {"position": snapshot_id}

        Raises:
            ValueError: If list has less than 2 entries or invalid format
        """
        if isinstance(stops_config, dict):
            return stops_config

        if isinstance(stops_config, list):
            if len(stops_config) < 2:
                raise ValueError("Stops list must have at least 2 entries")

            # Auto-space evenly across [0.0, 1.0]
            count = len(stops_config)
            step = 1.0 / (count - 1) if count > 1 else 0.0

            normalized_stops: NormalizedStops = {}
            for i, snapshot_id in enumerate(stops_config):
                position = i * step
                # Use 6 decimal places for precision
                normalized_stops[f"{position:.6f}"] = snapshot_id

            logging.debug(f"Normalized list stops to: {normalized_stops}")
            return normalized_stops

        raise ValueError(f"Stops must be dict or list, got {type(stops_config)}")

    def _extract_midi_bound_parameters(self) -> MidiBoundParams:
        """
        Extract all MIDI-bound parameters from current pedalboard.

        Scans all plugins in the pedalboard and collects parameters that have
        MIDI bindings. These parameters should be excluded from interpolation
        to avoid conflicts with the blend mode input.

        Returns:
            Set of (instance_id, symbol) tuples for MIDI-bound parameters
        """
        midi_params: set[tuple[str, str]] = set()
        pedalboard = self.handler.current.pedalboard

        for plugin in pedalboard.plugins:
            for symbol, param in plugin.parameters.items():
                if param.binding is not None:  # Format: "channel:CC"
                    midi_params.add((plugin.instance_id, symbol))
                    logging.debug(f"Found MIDI binding: {plugin.instance_id}/{symbol} -> {param.binding}")

        if midi_params:
            logging.info(f"Excluding {len(midi_params)} MIDI-bound parameters from blend interpolation")

        return midi_params

    def _validate_config(self) -> InterpolationFunc:
        """
        Validate config and resolve interpolation function.

        Supports spline interpolation (hermite, catmull_rom) and
        easing-based interpolation (linear, ease_in_quad, etc.).

        Returns:
            Per-parameter interpolation function

        Raises:
            ValueError: If config is invalid
        """
        # Parse interpolation function name
        interp_name = self.config.get("interpolation", "linear")
        interpolation_func = INTERPOLATION_FUNCTIONS.get(interp_name)

        if not interpolation_func:
            raise ValueError(
                f"Invalid interpolation '{interp_name}', must be one of: {', '.join(INTERPOLATION_FUNCTIONS.keys())}"
            )

        logging.debug(f"Config validated: interpolation={interp_name}")
        return interpolation_func

    def _create_stops(self) -> list[BlendStop]:
        """
        Load snapshots and create BlendStop objects.

        Returns:
            List of BlendStop objects (sorted by position)

        Raises:
            ValueError: If config is invalid or stops cannot be created
        """
        # Get and normalize stops configuration
        stops_config = self.config.get("stops")
        if not stops_config:
            raise ValueError("Blend mode requires 'stops' config")

        # Normalize to dict format (handles both dict and list)
        snapshot_stops = self._normalize_stops_config(stops_config)

        if len(snapshot_stops) < 2:
            raise ValueError(f"Blend mode requires at least 2 stops, got {len(snapshot_stops)}")

        # Read snapshots file
        bundle_path = Path(self.handler.current.pedalboard.bundle)
        snapshots_data = SnapshotManager.read_snapshots_file(bundle_path)

        # Parse and validate snapshot_stops entries
        stops_data: list[StopData] = []

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
            snapshot_index = SnapshotManager.resolve_snapshot_identifier(snapshots_data, snapshot_identifier)

            stops_data.append(StopData(position, snapshot_index))

        # Sort by position
        stops_data.sort(key=lambda x: x.position)

        # Create BlendStop objects
        stops = []
        for stop_data in stops_data:
            state = SnapshotManager.parse_snapshot_data(snapshots_data, stop_data.snapshot_index)
            stop = BlendStop(stop_data.position, stop_data.snapshot_index, state)
            stops.append(stop)
            logging.debug(f"Created {stop}")

        # Validate we have at least 2 stops
        if len(stops) < 2:
            raise ValueError(f"Need at least 2 stops, got {len(stops)}")

        # Limit to 4 stops for practical reasons
        # (hermite/catmull-rom look 2 stops back/forward for context)
        if len(stops) > 4:
            logging.warning(f"Limiting to 4 stops (got {len(stops)})")
            stops = stops[:4]

        # Sort stops by position
        stops.sort(key=lambda s: s.position)

        # Validate stops are monotonic and distinguishable at MIDI CC resolution
        for i in range(len(stops) - 1):
            pos_a = stops[i].position
            pos_b = stops[i + 1].position

            # Check positions are strictly increasing
            if pos_a >= pos_b:
                raise ValueError(
                    f"Stop positions must be strictly increasing: stop {i} at {pos_a}, stop {i + 1} at {pos_b}"
                )

            # Check positions map to different CC values (MIDI resolution check)
            cc_a = int(pos_a * 127)
            cc_b = int(pos_b * 127)
            if cc_a == cc_b:
                raise ValueError(
                    f"Stop positions too close - both map to CC {cc_a}: "
                    f"stop {i} at {pos_a}, stop {i + 1} at {pos_b}. "
                    f"Minimum separation is {1.0 / 127:.6f}"
                )

        return stops

    def _get_parameter_type(self, instance_id: str, symbol: str) -> ParameterType:
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

    def handle_snapshot_change(self, new_snapshot_name: str) -> None:
        """
        Handle snapshot changes and activate/deactivate blend mode accordingly.

        Args:
            new_snapshot_name: Name of the new snapshot being loaded
        """
        blend_snapshot_name = self.config.get("snapshot_name", "Blend Mode")

        if new_snapshot_name == blend_snapshot_name:
            # Switching TO "Blend Mode" snapshot
            if not self.enabled:
                logging.info(f"Activating blend mode (switched to '{blend_snapshot_name}' snapshot)")
                try:
                    self.initialize()
                    # Redraw analog assignments to use BlendMode object for expression pedal
                    self.handler.lcd.draw_analog_assignments(self.handler.current.analog_controllers)
                except Exception as e:
                    logging.error(f"Failed to activate blend mode: {e}")
        else:
            # Switching AWAY from "Blend Mode" snapshot
            if self.enabled:
                logging.info(f"Deactivating blend mode (switched to '{new_snapshot_name}' snapshot)")
                self.cleanup()
                # Redraw analog assignments to revert to normal AnalogMidiControl
                self.handler.lcd.draw_analog_assignments(self.handler.current.analog_controllers)

    def cleanup(self) -> None:
        """
        Clean up blend mode:
        - Detach from expression pedal
        - Reset tracking state
        - Close parameter setter
        - Reset state
        """
        if not self.enabled:
            return

        logging.info("Cleaning up blend mode...")

        # Clear any pending parameter updates to prevent stale messages
        if self.handler.ws_bridge:
            cleared = self.handler.ws_bridge.clear_queue()
            if cleared > 0:
                logging.info(f"Cleared {cleared} pending websocket messages")

        # Detach from input and reset tracking
        if self.input_controller:
            self.input_controller.detach_from_input()
            self.input_controller.reset_tracking()  # Reset segment cache

        # Clean up parameter setter and reset MIDI tracking
        if self.parameter_setter:
            self.parameter_setter.reset_tracking()  # Clear MIDI de-dupe tracking
            self.parameter_setter.cleanup()

        # Reset state
        self.stops = []
        self.segment_diff_maps = []
        self.enabled = False
        logging.info("Blend mode cleaned up")

    def check_for_snapshot_changes(self) -> None:
        """
        Check if snapshots.json has been modified and reinitialize if needed.

        This detects when stop snapshots are edited in MOD-UI, allowing
        blend mode to pick up the new parameter values without requiring
        a full pedalboard reload. Note that this file is only modified when
        the pedalboard itself is saved in MOD-UI.

        Called periodically from modhandler's poll_modui_changes().
        """
        if not self.enabled:
            return

        from pathlib import Path
        from blend.snapshot import SnapshotManager

        bundle_path = Path(self.handler.current.pedalboard.bundle)
        current_timestamp = SnapshotManager.get_snapshots_file_timestamp(bundle_path)

        # First check - just store timestamp
        if self.snapshots_file_timestamp == 0:
            self.snapshots_file_timestamp = current_timestamp
            return

        # Check if file was modified
        if current_timestamp != self.snapshots_file_timestamp:
            logging.info("Snapshots file modified, resyncing blend snapshot and reloading...")

            try:
                # Re-sync the blend snapshot (recreates from updated stops)
                SnapshotManager.sync_blend_snapshot(bundle_path, self.config, self.handler.root_uri)

                # Reinitialize: cleanup then initialize again
                self.cleanup()
                self.initialize()

                # Update timestamp AFTER sync (sync writes the file, changing timestamp)
                self.snapshots_file_timestamp = SnapshotManager.get_snapshots_file_timestamp(bundle_path)

                logging.info("Blend mode reloaded successfully")
            except Exception as e:
                logging.error(f"Failed to reload blend mode: {e}")
                self.enabled = False
