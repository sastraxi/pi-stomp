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
Input router: single dispatch seam for all hardware input events.

Controllers (Encoder, Footswitch, AnalogMidiControl) build events and call
``router.dispatch(event)``. Sinks pushed onto the router act on those events:
write parameter values, emit MIDI, notify the handler, etc. The router walks
sinks top-down (last pushed first) until a sink sets ``event.consumed``.

See INPUT_ROUTER.md for the full architecture.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass
from enum import Enum, auto
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pistomp.controller import Controller


class SwitchEventKind(Enum):
    PRESS = auto()
    RELEASE = auto()
    LONGPRESS = auto()


@dataclass
class ControllerEvent:
    controller: "Controller"
    consumed: bool = False


@dataclass
class EncoderEvent(ControllerEvent):
    # Raw detents this tick; positive = clockwise, negative = counter-clockwise.
    rotations: int = 0
    # Speed amplification computed by the Encoder; natural motion = rotations * multiplier.
    multiplier: float = 1.0


@dataclass
class AnalogEvent(ControllerEvent):
    # Raw ADC reading (0-1023 for MCP3008).
    raw_value: int = 0


@dataclass
class SwitchEvent(ControllerEvent):
    kind: SwitchEventKind = SwitchEventKind.PRESS


class InputSink(abc.ABC):
    """Contract for anything pushed onto an InputRouter.

    Default methods are no-ops so subclasses only override the event types they
    care about. Inherit from this class to be router-compatible.
    """

    def on_encoder(self, event: EncoderEvent) -> None:
        del event

    def on_analog(self, event: AnalogEvent) -> None:
        del event

    def on_switch(self, event: SwitchEvent) -> None:
        del event


class InputRouter:
    def __init__(self) -> None:
        self._sinks: list[InputSink] = []

    def push(self, sink: InputSink) -> None:
        self._sinks.append(sink)

    def pop(self, sink: InputSink) -> None:
        # Remove by identity, not equality. Raises if absent — mismatched
        # push/pop is a bug we want to see loudly.
        for i in range(len(self._sinks) - 1, -1, -1):
            if self._sinks[i] is sink:
                del self._sinks[i]
                return
        raise ValueError(f"sink {sink!r} not on router stack")

    def dispatch(self, event: ControllerEvent) -> None:
        # Walk top-down: most-recently-pushed sink runs first.
        for sink in reversed(self._sinks):
            if isinstance(event, EncoderEvent):
                sink.on_encoder(event)
            elif isinstance(event, AnalogEvent):
                sink.on_analog(event)
            elif isinstance(event, SwitchEvent):
                sink.on_switch(event)
            if event.consumed:
                return
