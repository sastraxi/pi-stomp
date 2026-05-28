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


from typing import TYPE_CHECKING

from pistomp.input.event import ControllerEvent
from pistomp.input.sink import InputSink

if TYPE_CHECKING:
    from pistomp.hardware import Hardware


class Handler(InputSink):

    def __init__(self):
        self.homedir = None
        self.lcd = None

    @property
    def hardware(self) -> "Hardware":
        raise NotImplementedError()

    def noop(self):
        pass

    def update_lcd_fs(self, footswitch=None, bypass_change=False):
        raise NotImplementedError()

    def add_lcd(self, lcd):
        raise NotImplementedError()

    def add_hardware(self, hardware):
        raise NotImplementedError()

    def poll_controls(self):
        raise NotImplementedError()

    def poll_modui_changes(self):
        raise NotImplementedError()

    def preset_incr_and_change(self):
        raise NotImplementedError()

    def preset_decr_and_change(self):
        raise NotImplementedError()

    def top_encoder_select(self, direction):
        raise NotImplementedError()

    def top_encoder_sw(self, value):
        raise NotImplementedError()

    def bot_encoder_select(self, direction):
        raise NotImplementedError()

    def bottom_encoder_sw(self, value):
        raise NotImplementedError()

    def universal_encoder_select(self, direction):
        raise NotImplementedError()

    def universal_encoder_sw(self, value):
        raise NotImplementedError()

    def handle(self, event: ControllerEvent) -> bool:
        """Default sink for v3 controllers. v1 leaves controller.sink = None
        and uses the legacy inline path."""
        raise NotImplementedError()

    def cleanup(self):
        raise NotImplementedError()

    def get_num_footswitches(self):
        raise NotImplementedError()

    def get_callback(self, callback_name):
        raise NotImplementedError()

    def set_mod_tap_tempo(self, bpm):
        raise NotImplementedError()

    def load_banks(self):
        raise NotImplementedError()

    def poll_indicators(self):
        raise NotImplementedError()

    def poll_lcd_updates(self):
        raise NotImplementedError()

    def poll_wifi(self):
        raise NotImplementedError()
