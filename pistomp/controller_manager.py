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

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import common.token as Token
from common.parameter import Parameter, TTL_PROPERTIES, TTL_INTEGER
from modalapi.external_midi import EXTERNAL_INSTANCE_ID
from pistomp.analogmidicontrol import AnalogMidiControl
from pistomp.controller import RoutingDestination
from pistomp.current import Current
from pistomp.footswitch import Footswitch

if TYPE_CHECKING:
    from pistomp.hardware import Hardware


class ControllerManager:
    """
    Manages controller/parameter bindings on the current pedalboard,
    overlaying per-pedalboard config on top of the base.
    The one genuine version difference is passed as a flag rather than subclassed:

      reorder_footswitch_plugins  v1 moves footswitch-controlled plugins to the
                                  tail of the chain; v3 leaves order untouched.
    """

    def __init__(self, hardware: "Hardware", *, reorder_footswitch_plugins: bool = False):
        self._hw = hardware
        self._reorder_footswitch_plugins = reorder_footswitch_plugins

    def bind(self, current: Current | None) -> None:
        """Rebind all controllers for the active pedalboard state."""
        if current is None:
            return

        # Clear previous parameter bindings from all controllers except volume.
        for controller in self._hw.controllers.values():
            if controller.type != Token.VOLUME:
                controller.parameter = None

        current.analog_controllers = {}

        if current.pedalboard:
            footswitch_plugins = self._bind_plugin_parameters(current)
            self._bind_volume_encoders(current)
            if self._reorder_footswitch_plugins:
                self._move_footswitch_plugins_to_end(current, footswitch_plugins)

        self._bind_external_controllers(current)

    def _bind_plugin_parameters(self, current) -> list:
        """Bind controllers referenced by plugin parameters; return the plugins
        that gained a footswitch."""
        footswitch_plugins = []
        for plugin in current.pedalboard.plugins:
            if plugin is None or plugin.parameters is None:
                continue
            for param in plugin.parameters.values():
                if param.binding is None:
                    continue
                controller = self._hw.controllers.get(param.binding)
                if controller is None:
                    continue

                routing = controller.get_routing_info()
                # External controllers aren't bound to plugin parameters.
                if routing.destination == RoutingDestination.EXTERNAL:
                    logging.warning(
                        f"Plugin parameter {plugin.name}:{param.name} is bound to external controller "
                        f"{param.binding} (routed to {routing.port_name}) - ignoring plugin binding"
                    )
                    continue

                controller.bind_to_parameter(param)
                plugin.controllers.append(controller)

                if isinstance(controller, Footswitch):
                    plugin.has_footswitch = True
                    footswitch_plugins.append(plugin)
                    controller.set_category(plugin.category)
                else:
                    key = "%s:%s" % (plugin.instance_id, param.name)
                    display_info = controller.get_display_info()
                    display_info["category"] = plugin.category
                    current.analog_controllers[key] = display_info
        return footswitch_plugins

    def _bind_volume_encoders(self, current) -> None:
        """Surface VOLUME-type encoders in the assignment display (v3 only in
        practice — v1 has no VOLUME-typed encoder)."""
        for e in self._hw.encoders:
            if e.type == Token.VOLUME:
                current.analog_controllers[Token.VOLUME] = e.get_display_info()

    @staticmethod
    def _move_footswitch_plugins_to_end(current, footswitch_plugins) -> None:
        plugins = current.pedalboard.plugins
        current.pedalboard.plugins = [p for p in plugins if p.has_footswitch is False] + footswitch_plugins

    def _bind_external_controllers(self, current) -> None:
        """Externally-routed controllers: bind a synthetic parameter and show
        them under an "External" category."""
        for controller in self._hw.controllers.values():
            routing = controller.get_routing_info()
            if routing.destination != RoutingDestination.EXTERNAL or controller.midi_CC is None:
                continue

            # Create a synthetic parameter if not already bound.
            # AnalogMidiControl uses EXTERNAL_INSTANCE_ID so parameter_value_commit can guard it;
            # EncoderController routes through encoder_value_changed instead.
            if controller.parameter is None:
                if isinstance(controller, AnalogMidiControl):
                    controller.parameter = self._hw.create_external_parameter(
                        controller, routing.port_name, controller.midi_channel, controller.midi_CC
                    )
                else:
                    ext_info = {
                        Token.NAME: f"{routing.port_name} CC{controller.midi_CC}",
                        Token.SYMBOL: f"external_{controller.midi_CC}",
                        Token.RANGES: {Token.MINIMUM: 0, Token.MAXIMUM: 127},
                        TTL_PROPERTIES: [TTL_INTEGER],
                    }
                    controller.bind_to_parameter(
                        Parameter(ext_info, controller.midi_value, None, EXTERNAL_INSTANCE_ID)
                    )

            key = f"{controller.midi_channel}:{controller.midi_CC}"
            display_info = controller.get_display_info()
            display_info["category"] = "External"
            current.analog_controllers[key] = display_info
