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

"""
ModSink: hand off to handler hooks that update the LCD and push parameter
commits to mod-host (REST). Today's equivalent is ``handler.encoder_value_changed``.
"""

from __future__ import annotations

from pistomp.hardware import Hardware
from pistomp.input_router import EncoderEvent, InputSink


class ModSink(InputSink):
    def __init__(self, handler, hardware: Hardware):
        # `handler` is typed loosely to avoid a circular import with modalapi.modhandler.
        self.handler = handler
        self.hardware = hardware

    def on_encoder(self, event: EncoderEvent) -> None:
        controller = event.controller
        param = controller.parameter
        if param is None:
            return
        is_external = self.hardware.external_port_name(controller) is not None
        self.handler.encoder_value_changed(param, param.value, is_external=is_external)
