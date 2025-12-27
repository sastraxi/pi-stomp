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

"""Main CollageMode coordinator class with pre-computation optimization."""

import json
import logging
import requests as req
from pathlib import Path
from typing import Any

from collage.interpolation import (
    # Complex interpolation
    linear_interpolation,
    hermite_interpolation,
    catmull_rom_interpolation,
    # Easing-based interpolation
    ease_in_quad_interpolation,
    ease_out_quad_interpolation,
    ease_in_out_quad_interpolation,
    ease_in_cubic_interpolation,
    ease_out_cubic_interpolation,
    ease_in_out_cubic_interpolation,
    exponential_easing_interpolation,
    sine_easing_interpolation,
)
from collage.parameter_setter import ParameterSetter
from collage.pedal_controller import PedalController
from collage.snapshot import SnapshotManager
from collage.stop import CollageStop
from collage.types import (
    CollageConfig,
    EnrichedDiffMap,
    InterpolationFunc,
)
from modalapi.parameter import Type as ParameterType


class CollageMode:
    """
    Coordinates collage mode components with pre-computation optimization.

    Pre-computes enriched diff maps for each segment at initialization to
    minimize work in the critical path (10ms polling loop).
    """

    def __init__(self, handler: Any, config: CollageConfig) -> None:
        """
        Initialize collage mode coordinator.

        Args:
            handler: Reference to Modhandler instance
            config: Collage mode configuration dict from YAML
        """
        self.handler: Any = handler  # Modhandler - avoiding circular import
        self.config: CollageConfig = config
        self.enabled: bool = False
        self.stops: list[CollageStop] = []
        self.segment_diff_maps: list[EnrichedDiffMap] = []

        # Components (initialized in initialize())
        self.parameter_setter: ParameterSetter | None = None
        self.pedal_controller: PedalController | None = None

    def initialize(self) -> None:
        """
        Initialize collage mode with pre-computation optimization.

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
        logging.info("Initializing collage mode...")

        try:
            # Validate configuration and resolve interpolation function
            interpolation_func = self._validate_config()

            # Load snapshots and create stops
            self.stops = self._create_stops()

            # PRE-COMPUTE: Build enriched diff maps for each segment
            logging.info(f"Pre-computing diff maps for {len(self.stops) - 1} segments...")
            self.segment_diff_maps = []

            for segment_idx in range(len(self.stops) - 1):
                lower = self.stops[segment_idx]
                upper = self.stops[segment_idx + 1]

                # Build enriched diff map with neighbor data
                diff_map = CollageStop.build_enriched_diff_map(
                    lower,
                    upper,
                    self.stops,
                    segment_idx,
                    self._get_parameter_type,
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

            # Initialize and attach pedal controller
            exp_pedal_id = self.config.get('expression_pedal_id', 0)

            self.pedal_controller = PedalController(
                interpolation_func,
                self.stops,
                self.segment_diff_maps,  # Pre-computed!
                self.parameter_setter,
            )

            # Attach to expression pedal
            self.pedal_controller.attach_to_pedal(
                self.handler.hardware.analog_controls,
                exp_pedal_id
            )

            self.enabled = True
            logging.info(f"Collage mode initialized with {len(self.stops)} stops")

        except Exception as e:
            logging.error(f"Failed to initialize collage mode: {e}")
            self.enabled = False
            raise

    def _validate_config(self) -> InterpolationFunc:
        """
        Validate config and resolve interpolation function.

        Supports both complex interpolation (linear, hermite, catmull_rom)
        and easing-based interpolation (ease_in_quad, etc.).

        Returns:
            Per-parameter interpolation function

        Raises:
            ValueError: If config is invalid
        """
        # Single mapping of all interpolation function names
        INTERPOLATION_FUNCTIONS: dict[str, InterpolationFunc] = {
            # Complex interpolation
            'linear': linear_interpolation,
            'hermite': hermite_interpolation,
            'catmull_rom': catmull_rom_interpolation,

            # Easing-based interpolation
            'ease_in_quad': ease_in_quad_interpolation,
            'ease_out_quad': ease_out_quad_interpolation,
            'ease_in_out_quad': ease_in_out_quad_interpolation,
            'ease_in_cubic': ease_in_cubic_interpolation,
            'ease_out_cubic': ease_out_cubic_interpolation,
            'ease_in_out_cubic': ease_in_out_cubic_interpolation,
            'exponential': exponential_easing_interpolation,
            'sine': sine_easing_interpolation,
        }

        # Parse interpolation function name
        interp_name = self.config.get('interpolation', 'linear')
        interpolation_func = INTERPOLATION_FUNCTIONS.get(interp_name)

        if not interpolation_func:
            raise ValueError(
                f"Invalid interpolation '{interp_name}', "
                f"must be one of: {', '.join(INTERPOLATION_FUNCTIONS.keys())}"
            )

        logging.debug(f"Config validated: interpolation={interp_name}")
        return interpolation_func

    def _create_stops(self) -> list[CollageStop]:
        """
        Load snapshots and create CollageStop objects.

        Returns:
            List of CollageStop objects (sorted by position)

        Raises:
            ValueError: If config is invalid or stops cannot be created
        """
        # Get snapshot_stops configuration
        snapshot_stops = self.config.get('snapshot_stops', {})
        if len(snapshot_stops) < 2:
            raise ValueError(f"Collage mode requires at least 2 stops, got {len(snapshot_stops)}")

        # Read snapshots file
        bundle_path = Path(self.handler.current.pedalboard.bundle)
        snapshots_data = SnapshotManager.read_snapshots_file(bundle_path)

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
            snapshot_index = SnapshotManager.resolve_snapshot_identifier(snapshots_data, snapshot_identifier)

            stops_data.append((position, snapshot_index))

        # Sort by position
        stops_data.sort(key=lambda x: x[0])

        # Create CollageStop objects
        stops = []
        for position, snapshot_index in stops_data:
            state = SnapshotManager.parse_snapshot_data(snapshots_data, snapshot_index)
            stop = CollageStop(position, snapshot_index, state)
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
        snapshots_data = SnapshotManager.read_snapshots_file(bundle_path)
        snapshot_name = self.config.get('snapshot_name', 'Collage Mode')

        # Check if snapshot already exists
        for snapshot in snapshots_data.get('snapshots', []):
            if snapshot.get('name') == snapshot_name:
                logging.debug(f"'{snapshot_name}' snapshot already exists, skipping creation")
                return

        # Get first two stop indices
        snapshot_stops = self.config.get('snapshot_stops', {})
        sorted_stops = sorted(snapshot_stops.items(), key=lambda x: float(x[0]))
        first_identifier = sorted_stops[0][1]
        second_identifier = sorted_stops[1][1]

        first_stop_index = SnapshotManager.resolve_snapshot_identifier(snapshots_data, first_identifier)
        second_stop_index = SnapshotManager.resolve_snapshot_identifier(snapshots_data, second_identifier)

        # Create sparse collage snapshot
        logging.info(f"Creating '{snapshot_name}' snapshot...")
        collage_snapshot = SnapshotManager.create_sparse_snapshot(
            snapshots_data,
            first_stop_index,
            second_stop_index,
            self._get_parameter_type,
            snapshot_name
        )

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
        - Detach from expression pedal
        - Reset tracking state
        - Close parameter setter
        - Reset state
        """
        if not self.enabled:
            return

        logging.info("Cleaning up collage mode...")

        # Clear any pending parameter updates to prevent stale messages
        if self.handler.ws_bridge:
            cleared = self.handler.ws_bridge.clear_queue()
            if cleared > 0:
                logging.info(f"Cleared {cleared} pending websocket messages")

        # Detach from expression pedal and reset tracking
        if self.pedal_controller:
            self.pedal_controller.detach_from_pedal()
            self.pedal_controller.reset_tracking()  # Reset segment cache

        # Clean up parameter setter and reset MIDI tracking
        if self.parameter_setter:
            self.parameter_setter.reset_tracking()  # Clear MIDI de-dupe tracking
            self.parameter_setter.cleanup()

        # Reset state
        self.stops = []
        self.segment_diff_maps = []
        self.enabled = False
        logging.info("Collage mode cleaned up")
