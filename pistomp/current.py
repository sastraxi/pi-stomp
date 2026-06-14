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

from dataclasses import dataclass, field

import common.token as Token
from pistomp.controller import AssignmentSource, ControlAssignment, ControlKind
from modalapi.pedalboard import Pedalboard


@dataclass
class Current:
    """Mutable per-pedalboard state for the active ("current") pedalboard."""

    pedalboard: Pedalboard
    presets: dict[int, str] = field(default_factory=dict)
    preset_index: int = 0  # Assumes pedalboard loads at snapshot 0 (default behavior)
    assignments: dict[int, ControlAssignment] = field(default_factory=dict)

    @property
    def analog_controllers(self) -> dict:
        """Read-only adapter for legacy code (v1 LCD / mod.py) that still
        expects the old stringly-keyed AnalogDisplayInfo format."""
        result = {}
        for slot_id, a in self.assignments.items():
            kind_token = Token.EXPRESSION if a.kind == ControlKind.EXPRESSION else Token.KNOB
            if a.source == AssignmentSource.VOLUME:
                key = Token.VOLUME
                value = {Token.TYPE: Token.VOLUME, Token.ID: slot_id, Token.CATEGORY: None}
            elif a.source == AssignmentSource.EXTERNAL:
                key = f"{a.port_name or slot_id}:{a.midi_cc}"
                value = {Token.TYPE: kind_token, Token.ID: slot_id,
                         Token.CATEGORY: a.category,
                         "port_name": a.port_name, "midi_cc": a.midi_cc}
            else:
                key = f"plugin:{a.label or 'none'}"
                value = {Token.TYPE: kind_token, Token.ID: slot_id, Token.CATEGORY: a.category}
            result[key] = value
        return result
