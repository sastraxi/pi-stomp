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

"""MIDI mapping management for collage mode."""

import logging
from typing import Any

from collage.stop import CollageStop
from collage.types import DiffMapDict, ParameterTypeGetter
from modalapi.modhost import ModHostSocket


class MidiMapper:
    """Manages MIDI mappings to mod-host."""

    def __init__(self) -> None:
        """Initialize MIDI mapper."""
        self.mapped_parameters: list[tuple[int, str]] = []  # Track (instance_num, symbol) for cleanup

    def apply_segment_mappings(
        self,
        stop_a: CollageStop,
        stop_b: CollageStop,
        exp_channel: int,
        exp_cc: int,
        param_type_getter: ParameterTypeGetter,
        instance_number_getter: Any  # Callable[[str], int | None]
    ) -> None:
        """
        Apply MIDI mappings for a segment between two stops.

        Maps parameters to expression pedal CC with min/max from segment endpoints.

        Args:
            stop_a: Lower stop of segment
            stop_b: Upper stop of segment
            exp_channel: MIDI channel of expression pedal
            exp_cc: MIDI CC number of expression pedal
            param_type_getter: Function to get parameter type
            instance_number_getter: Function to get instance number from instance_id
        """
        # Calculate parameter diffs for this segment
        diff_map = CollageStop.build_diff_map(
            stop_a.snapshot_state,
            stop_b.snapshot_state,
            param_type_getter
        )

        # Apply binary "on wins" logic
        diff_map = CollageStop.adjust_binary_params(diff_map)

        # Send midi_map commands
        with ModHostSocket() as sock:
            for instance_id, params in diff_map.items():
                # Get instance number
                instance_num = instance_number_getter(instance_id)
                if instance_num is None:
                    logging.warning(f"Plugin {instance_id} not found in pedalboard, skipping")
                    continue

                for symbol, (val_a, val_b, _param_type) in params.items():
                    try:
                        sock.midi_map(instance_num, symbol, exp_channel, exp_cc, val_a, val_b)

                        # Track for cleanup (avoid duplicates)
                        if (instance_num, symbol) not in self.mapped_parameters:
                            self.mapped_parameters.append((instance_num, symbol))

                        logging.debug(f"Mapped {instance_id}/{symbol}: {val_a} -> {val_b}")
                    except Exception as e:
                        logging.warning(f"Failed to map {instance_id}/{symbol}: {e}")

        logging.info(f"Applied MIDI mappings ({len(diff_map)} plugins)")

    def build_virtual_mappings(
        self,
        stops: list[CollageStop],
        virtual_channel: int,
        instance_number_getter: Any  # Callable[[str], int | None]
    ) -> dict[str, int]:
        """
        Build virtual CC mappings for parameter mode.

        Assigns a unique CC number to each parameter that varies across stops,
        then sends midi_map commands to mod-host to map those virtual CCs to parameters.

        Args:
            stops: List of CollageStop objects
            virtual_channel: MIDI channel for virtual CCs
            instance_number_getter: Function to get instance number from instance_id

        Returns:
            Dict mapping "instance_id:symbol" -> CC number

        Raises:
            ValueError: If too many parameters (> 58)
        """
        # Collect all unique parameters across all stops
        all_params: set[str] = set()

        for stop in stops:
            for instance_id, params in stop.snapshot_state.items():
                for symbol in params.keys():
                    param_key = f"{instance_id}:{symbol}"
                    all_params.add(param_key)

        # Assign virtual CC numbers starting at 70
        # (Avoids common controller CCs like 1-31, leaves room for expansion)
        virtual_cc_mappings: dict[str, int] = {}
        next_cc = 70

        for param_key in sorted(all_params):  # Sort for deterministic ordering
            virtual_cc_mappings[param_key] = next_cc
            next_cc += 1

            if next_cc > 127:
                raise ValueError(
                    f"Too many parameters for virtual CC mapping (max 58, need {len(all_params)})"
                )

        logging.debug(f"Assigned virtual CCs 70-{next_cc-1} to {len(virtual_cc_mappings)} parameters")

        # Send midi_map commands to mod-host for all virtual CCs
        # Maps: virtual CC (on virtual channel) -> parameter (full range 0.0-1.0)
        with ModHostSocket() as sock:
            for param_key, cc_num in virtual_cc_mappings.items():
                # Parse param_key: "instance_id:symbol"
                instance_id, symbol = param_key.split(':', 1)

                # Get instance number
                instance_num = instance_number_getter(instance_id)
                if instance_num is None:
                    logging.warning(f"Plugin {instance_id} not found, skipping virtual CC mapping")
                    continue

                # Map virtual CC to parameter (full range 0.0-1.0)
                try:
                    sock.midi_map(instance_num, symbol, virtual_channel, cc_num, 0.0, 1.0)
                    # Track for cleanup
                    self.mapped_parameters.append((instance_num, symbol))
                    logging.debug(f"Mapped virtual CC {cc_num} to {instance_id}/{symbol}")
                except Exception as e:
                    logging.warning(f"Failed to map virtual CC for {param_key}: {e}")

        return virtual_cc_mappings

    def cleanup(self) -> None:
        """
        Remove all MIDI mappings.

        Sends midi_unmap commands for all tracked parameters.
        """
        if not self.mapped_parameters:
            return

        try:
            with ModHostSocket() as sock:
                for instance_num, symbol in self.mapped_parameters:
                    try:
                        sock.midi_unmap(instance_num, symbol)
                        logging.debug(f"Unmapped {instance_num}/{symbol}")
                    except Exception as e:
                        logging.warning(f"Failed to unmap {instance_num}/{symbol}: {e}")
        except Exception as e:
            logging.error(f"Failed to cleanup MIDI mappings: {e}")

        self.mapped_parameters = []
        logging.info("MIDI mappings cleaned up")
