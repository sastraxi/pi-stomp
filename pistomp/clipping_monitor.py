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

import logging
import lilv
import os


class ClippingMonitor:
    """
    Monitors audio output for clipping by listening to tinygain level output via WebSocket.

    Listens for output_set messages from tinygain plugins connected to hardware outputs
    and detects clipping when level values exceed threshold.
    """

    TINYGAIN_MONO_URI = "http://gareus.org/oss/lv2/tinygain#mono"
    TINYGAIN_STEREO_URI = "http://gareus.org/oss/lv2/tinygain#stereo"

    def __init__(self, clip_threshold=1, hold_ticks=2):
        """
        Initialize clipping monitor.

        Args:
            clip_threshold: Level threshold for clipping detection (linear amplitude, 1.0 = full scale/0dBFS)
            hold_ticks: Number of polling cycles to hold clip indicator before clearing
        """
        self.clip_threshold = clip_threshold
        self.hold_ticks = hold_ticks

        # Tinygain plugin instance IDs (set when pedalboard changes)
        # Stores instance_id for left and right output meters
        self.meter_left_id = None
        self.meter_right_id = None

        # Level port symbols to monitor
        self.left_peak_symbol = "level"
        self.right_peak_symbol = "level"

        # Current level values
        self.peak_left = 0.0
        self.peak_right = 0.0

        # Clip detection state
        self.clip_left = False
        self.clip_right = False
        self.clip_left_counter = 0
        self.clip_right_counter = 0

        self.enabled = True

    def _find_output_meters(self, bundle_path):
        """
        Parse pedalboard TTL to find tinygain plugins connected to hardware outputs.

        Args:
            bundle_path: Path to pedalboard bundle directory

        Returns:
            tuple: (left_meter_id, right_meter_id) or (None, None) if not found
        """
        if not bundle_path or not os.path.exists(bundle_path):
            return None, None

        world = lilv.World()
        world.load_specifications()
        world.load_plugin_classes()

        # Load the bundle
        bundle = os.path.abspath(bundle_path)
        if not bundle.endswith(os.sep):
            bundle += os.sep
        bundlenode = world.new_file_uri(None, bundle)
        world.load_bundle(bundlenode)

        # Get the pedalboard plugin
        plugins = world.get_all_plugins()
        if len(plugins) != 1:
            logging.warning(f"ClippingMonitor: Expected 1 plugin in bundle, found {len(plugins)}")
            return None, None

        pedalboard_plugin = None
        for p in plugins:
            pedalboard_plugin = p
            break

        if not pedalboard_plugin:
            return None, None

        # URIs we need
        uri_block = world.new_uri("http://drobilla.net/ns/ingen#block")
        uri_tail = world.new_uri("http://drobilla.net/ns/ingen#tail")
        uri_head = world.new_uri("http://drobilla.net/ns/ingen#head")
        uri_prototype = world.ns.lv2.prototype

        # Build connection map: port_uri -> destination_port_uri
        # In the RDF, connections look like:
        #   _:b6 ingen:tail <mono_1/out> ; ingen:head <playback_1> .
        # We need to find all ports and check what they're connected to
        connections = {}

        # Get all blocks (plugins) first
        blocks = pedalboard_plugin.get_value(uri_block)

        # Collect all ports (both pedalboard-level and plugin-level)
        all_ports = []

        # Add pedalboard-level ports (capture_1, playback_1, etc.)
        pb_ports = pedalboard_plugin.get_value(world.ns.lv2.port)
        for port in pb_ports:
            if port is not None:
                all_ports.append(port)

        # Add plugin ports
        for block in blocks:
            if block is None or block.is_blank():
                continue
            block_ports = world.find_nodes(block, world.ns.lv2.port, None)
            for port in block_ports:
                if port is not None:
                    all_ports.append(port)

        # Now check all ports for connections
        for port in all_ports:
            # Check if this port is the tail of a connection
            # Find the connection node that has this port as its tail
            tail_nodes = world.find_nodes(None, uri_tail, port)
            for tail_node in tail_nodes:
                # Get the head of this connection
                head = world.get(tail_node, uri_head, None)
                if head is not None:
                    port_str = str(port)
                    head_str = str(head)
                    connections[port_str] = head_str

        # Find tinygain plugins connected to playback_1 (left) or playback_2 (right)
        left_meter = None
        right_meter = None

        for block in blocks:
            if block is None or block.is_blank():
                continue

            # Check if this is a tinygain plugin
            prototype = world.find_nodes(block, uri_prototype, None)
            if len(prototype) == 0:
                continue

            plugin_uri = str(prototype[0])
            if plugin_uri not in [self.TINYGAIN_MONO_URI, self.TINYGAIN_STEREO_URI]:
                continue

            # Get instance ID (strip leading slash for suffix matching)
            instance_id = str(block.get_path()).replace(bundle_path, "", 1)
            instance_id_for_graph = instance_id  # Keep the original for /graph prefix
            instance_id_suffix = instance_id.lstrip("/")  # Remove leading slash for suffix matching

            # Check if output is connected to playback_1 or playback_2
            if plugin_uri == self.TINYGAIN_MONO_URI:
                # Mono tinygain has single output "out"
                # Look for connections ending with this plugin's output port
                out_port_suffix = f"{instance_id_suffix}/out"

                for conn_src, conn_dest in connections.items():
                    if conn_src.endswith(out_port_suffix):
                        if conn_dest.endswith("/playback_1"):
                            left_meter = f"/graph{instance_id_for_graph}"
                            logging.info(f"ClippingMonitor: Found mono tinygain {left_meter} connected to playback_1")
                        elif conn_dest.endswith("/playback_2"):
                            right_meter = f"/graph{instance_id_for_graph}"
                            logging.info(f"ClippingMonitor: Found mono tinygain {right_meter} connected to playback_2")

            elif plugin_uri == self.TINYGAIN_STEREO_URI:
                # Stereo tinygain has outL and outR
                out_l_suffix = f"{instance_id_suffix}/outL"
                out_r_suffix = f"{instance_id_suffix}/outR"

                for conn_src, conn_dest in connections.items():
                    if conn_src.endswith(out_l_suffix) and conn_dest.endswith("/playback_1"):
                        left_meter = f"/graph{instance_id_for_graph}"
                        logging.info(f"ClippingMonitor: Found stereo tinygain {left_meter}/outL connected to playback_1")
                    elif conn_src.endswith(out_r_suffix) and conn_dest.endswith("/playback_2"):
                        right_meter = f"/graph{instance_id_for_graph}"
                        logging.info(f"ClippingMonitor: Found stereo tinygain {right_meter}/outR connected to playback_2")

        return left_meter, right_meter

    def update_pedalboard(self, pedalboard):
        """
        Update meter references when pedalboard changes.

        Searches for tinygain plugins connected to hardware outputs and stores
        references to their instance IDs for WebSocket message filtering.

        Args:
            pedalboard: Pedalboard object with bundle path
        """
        self.meter_left_id = None
        self.meter_right_id = None
        self.enabled = False
        self.reset_clip_indicators()

        if not pedalboard:
            logging.debug("ClippingMonitor: No pedalboard")
            return

        # Parse TTL to find tinygain plugins connected to hardware outputs
        left_meter, right_meter = self._find_output_meters(pedalboard.bundle)

        if left_meter:
            self.meter_left_id = left_meter

        if right_meter:
            self.meter_right_id = right_meter
        elif left_meter:
            # Only one meter found - use for both channels
            self.meter_right_id = left_meter
            logging.info("ClippingMonitor: Using single meter for both channels")

        self.enabled = self.meter_left_id is not None
        logging.info(f"ClippingMonitor: {'Enabled' if self.enabled else 'Disabled'}")

    def handle_output_set(self, instance_id, port_symbol, value):
        """
        Handle output_set WebSocket message.

        Called when mod-ui sends parameter updates via WebSocket.
        Tracks maximum level value since last check_clipping() call.

        Args:
            instance_id: Plugin instance ID (e.g., "/graph/mono_1")
            port_symbol: Parameter symbol ("level")
            value: Level value (linear amplitude, 1.0 = 0dBFS)
        """
        if not self.enabled:
            return

        # Track maximum level value (output_set may arrive faster than poll rate)
        if instance_id == self.meter_left_id and port_symbol == self.left_peak_symbol:
            self.peak_left = max(self.peak_left, value)
        elif instance_id == self.meter_right_id and port_symbol == self.right_peak_symbol:
            self.peak_right = max(self.peak_right, value)

    def check_clipping(self):
        """
        Check current level values for clipping.

        Called periodically from poll_indicators() (20ms) to update clip state
        with hold counter logic. Resets level values after checking to prepare
        for next polling interval.

        Returns:
            tuple: (clip_left, clip_right) - clip flags per output
        """
        if not self.enabled:
            return False, False

        # Check if current level values exceed threshold
        clipped_left = self.peak_left >= self.clip_threshold
        clipped_right = self.peak_right >= self.clip_threshold

        # Update clip state with hold counter
        if clipped_left:
            self.clip_left = True
            self.clip_left_counter = self.hold_ticks
        else:
            if self.clip_left_counter > 0:
                self.clip_left_counter -= 1
            else:
                self.clip_left = False

        if clipped_right:
            self.clip_right = True
            self.clip_right_counter = self.hold_ticks
        else:
            if self.clip_right_counter > 0:
                self.clip_right_counter -= 1
            else:
                self.clip_right = False

        # Reset levels for next polling interval
        self.peak_left = 0.0
        self.peak_right = 0.0

        return self.clip_left, self.clip_right

    def reset_clip_indicators(self):
        """Immediately clear clip indicators (e.g., on pedalboard change)."""
        self.clip_left = False
        self.clip_right = False
        self.clip_left_counter = 0
        self.clip_right_counter = 0
        self.peak_left = 0.0
        self.peak_right = 0.0
