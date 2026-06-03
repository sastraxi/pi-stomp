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

import bisect
import logging
import threading
import time
from typing import Callable, List, Optional

import common.util as util
import pistomp.controller as controller
import pistomp.gpioswitch as gpioswitch
import pistomp.switchstate as switchstate
from common.parameter import Parameter, Type
from pistomp.input.event import EncoderEvent, SwitchEvent, SwitchEventKind


def _clamp(value, min_value, max_value):
    return max(min_value, min(max_value, value))


class Encoder(controller.Controller):
    """Quadrature rotary encoder with optional parameter quantization
    and optional absorbed button.

    Two operating modes, selected by constructor args:

    - **Nav mode** (``callback`` provided, no ``midi_channel``): direct
      callback on each detent; no quantizer, no event dispatch. Used by
      the navigation encoder.
    - **Param mode** (``midi_channel`` provided): owns a quantizer
      (``step_values`` / ``current_step``). On rotation, advances state,
      writes ``parameter.value`` if bound, then dispatches an
      ``EncoderEvent`` via ``self.sink``. Sink is assigned later by
      ``Hardware.register_sink``; if still None at dispatch time, the
      event is dropped.

    Button (param mode only): if ``sw_pin`` is provided, owns a private
    ``GpioSwitch`` and emits ``SwitchEvent`` via ``self.sink``.
    """

    # Speed amplification: at this per-detent interval, multiplier = 1×.
    # 80 ms ≈ 12.5 detents/sec — steady cruising stays at 1×.
    REFERENCE_DT_MS = 80.0
    MAX_MULTIPLIER = 16.0
    MIN_MULTIPLIER = 1.0

    def __init__(
        self,
        d_pin: int,
        clk_pin: int,
        *,
        callback: Optional[Callable[[int], None]] = None,
        midi_channel: int = 0,
        midi_CC: Optional[int] = None,
        type: Optional[str] = None,
        id: Optional[int] = None,
        sw_pin: Optional[int] = None,
        shortpress: Optional[Callable] = None,
        longpress: Optional[Callable] = None,
    ):
        controller.Controller.__init__(self, midi_channel, midi_CC)
        self.d_pin = d_pin
        self.clk_pin = clk_pin
        self.callback = callback
        self.type = type
        self.id = id

        self._lock = threading.Lock()
        self.data = None
        self.clk = None
        if d_pin is not None:
            from gpiozero import Button   # TODO consider using Encoder class instead
            self.data = Button(d_pin)
            self.data.when_pressed = self._gpio_callback
            self.data.when_released = self._gpio_callback
            self.clk = Button(clk_pin)
            self.clk.when_pressed = self._gpio_callback
            self.clk.when_released = self._gpio_callback

        self.prevNextCode = 0
        self.store = 0
        self.direction = 0
        # 16 grey codes; 1 = valid transition, 0 = bounce.
        self.rot_enc_table = [0, 1, 1, 0, 1, 0, 0, 1, 1, 0, 0, 1, 0, 1, 1, 0]

        # Param-mode state (inert until bound or used)
        self.step_values: List[float] = []
        self.current_step: int = 0
        self.num_steps: int = 128
        self._last_detent_time: Optional[float] = None
        self._last_direction: int = 0
        if midi_channel is not None and midi_CC is not None or type is not None:
            self._recalculate_steps()
            self.set_value(64)

        # Absorbed button (GPIO; ADC-switch absorption is not used today)
        self._button: Optional[gpioswitch.GpioSwitch] = None
        self._shortpress = shortpress
        self._longpress = longpress
        if sw_pin is not None:
            self._button = gpioswitch.GpioSwitch(
                sw_pin,
                callback=self._on_button,
                longpress_callback=self._on_button_longpress,
            )

        logging.debug(f"Encoder init: id={id}, midi_CC={midi_CC}, sw_pin={sw_pin}")

    def __del__(self):
        if self.data is not None:
            self.data.close()
        if self.clk is not None:
            self.clk.close()

    # ── Raw GPIO decode ──────────────────────────────────────────────

    def _process_gpios(self) -> int:
        # Algorithm adapted from
        # https://www.best-microcontroller-projects.com/rotary-encoder.html
        self.prevNextCode <<= 2
        if self.data.value:
            self.prevNextCode |= 0x02
        if self.clk.value:
            self.prevNextCode |= 0x01
        self.prevNextCode &= 0x0f

        direction = 0
        if self.rot_enc_table[self.prevNextCode]:
            self.store <<= 4
            self.store |= self.prevNextCode
            if (self.store & 0xff) == 0x2b:
                direction = 1
            if (self.store & 0xff) == 0x17:
                direction = -1
        if direction != 0:
            self.store = self.prevNextCode
        return direction

    def _gpio_callback(self, channel):
        d = self._process_gpios()
        if d != 0:
            with self._lock:
                self.direction += d

    def read_rotary(self) -> None:
        """Called from the poll loop; resolves any queued direction and dispatches."""
        d = 0
        if self.direction != 0:
            with self._lock:
                if self.direction > 0:
                    d = 1
                elif self.direction < 0:
                    d = -1
                self.direction -= d
        else:
            d = self._process_gpios()
        if d != 0:
            self.refresh(d)

    def poll(self) -> None:
        """Called from the poll loop; polls the absorbed button (if any)."""
        if self._button is not None:
            self._button.poll()

    # ── Param-mode quantizer ─────────────────────────────────────────

    @property
    def taper(self) -> float:
        return self.parameter.get_taper() if self.parameter is not None else 1.0

    @property
    def min_val(self) -> float:
        return self.parameter.minimum if self.parameter is not None else self.midi_min

    @property
    def max_val(self) -> float:
        return self.parameter.maximum if self.parameter is not None else self.midi_max

    def _calculate_parameter_resolution(self) -> int:
        if self.midi_CC is not None or self.parameter is None:
            return 128
        if self.parameter.type == Type.INTEGER:
            return int(self.parameter.maximum - self.parameter.minimum) + 1
        if self.parameter.type == Type.ENUMERATION:
            return len(self.parameter.get_enum_value_list())
        if self.parameter.type == Type.TOGGLED:
            return 2
        return 256

    def _recalculate_steps(self) -> None:
        self.step_values = []
        self.num_steps = self._calculate_parameter_resolution()
        if self.num_steps <= 1:
            self.step_values = [self.min_val]
            return
        _taper = self.taper
        rng = self.max_val - self.min_val
        for i in range(self.num_steps):
            pos = i / (self.num_steps - 1)
            tapered_pos = pos ** _taper
            self.step_values.append(self.min_val + (rng * tapered_pos))

    def bind_to_parameter(self, parameter: Parameter) -> None:
        self.parameter = parameter
        self._recalculate_steps()
        self.set_value(parameter.value)
        logging.debug(
            f"Encoder bound: id={self.id}, param={parameter.name}, "
            f"midi_CC={self.midi_CC}, num_steps={self.num_steps}, value={parameter.value}"
        )

    def set_value(self, value: float) -> None:
        idx = bisect.bisect_left(self.step_values, value)
        if idx == 0:
            self.current_step = 0
        elif idx == len(self.step_values):
            self.current_step = len(self.step_values) - 1
        else:
            if abs(self.step_values[idx - 1] - value) <= abs(self.step_values[idx] - value):
                self.current_step = idx - 1
            else:
                self.current_step = idx
        self.midi_value = self._value_to_midi(self.step_values[self.current_step])

    def _move_steps(self, delta_steps: int) -> float:
        self.current_step = _clamp(self.current_step + delta_steps, 0, len(self.step_values) - 1)
        return self.step_values[self.current_step]

    def _compute_multiplier(self, rotations: int) -> float:
        now = time.monotonic()
        last = self._last_detent_time
        last_dir = self._last_direction
        direction = 1 if rotations > 0 else -1 if rotations < 0 else 0
        self._last_detent_time = now
        self._last_direction = direction

        if rotations == 0 or last is None or direction != last_dir:
            return self.MIN_MULTIPLIER
        dt = now - last
        if dt <= 0:
            return self.MAX_MULTIPLIER
        dt_per_detent_ms = (dt * 1000.0) / abs(rotations)
        return _clamp(self.REFERENCE_DT_MS / dt_per_detent_ms, self.MIN_MULTIPLIER, self.MAX_MULTIPLIER)

    def _value_to_midi(self, value: float) -> int:
        if self.parameter is None:
            midi_value = value
        else:
            midi_value = util.renormalize(
                value, self.parameter.minimum, self.parameter.maximum,
                self.midi_min, self.midi_max,
            )
        return int(_clamp(midi_value, 0, 127))

    def get_normalized_value(self) -> float:
        if self.num_steps <= 1:
            return 0.0
        return self.current_step / (self.num_steps - 1)

    def get_display_info(self) -> controller.AnalogDisplayInfo:
        info: controller.AnalogDisplayInfo = {"category": None}
        if self.type is not None:
            info["type"] = self.type
        if self.id is not None:
            info["id"] = self.id
        return info

    # ── Dispatch ─────────────────────────────────────────────────────

    def refresh(self, rotations: int) -> None:
        """Handle a tick's worth of detents."""
        # Nav mode: caller registered a raw callback; no quantizer.
        if self.callback is not None:
            self.callback(rotations)
            return

        # Param mode: advance our own state, then dispatch a fact.
        multiplier = self._compute_multiplier(rotations)
        delta = int(round(rotations * multiplier))
        new_value = self._move_steps(delta)
        self.midi_value = self._value_to_midi(new_value)
        if self.parameter is not None:
            self.parameter.value = new_value

        if self.sink is not None:
            self.sink.handle(EncoderEvent(
                controller=self,
                rotations=rotations,
                multiplier=multiplier,
                new_value=new_value,
                new_midi_value=self.midi_value,
            ))

    # ── Button ───────────────────────────────────────────────────────

    def set_longpress(self, callback: Optional[Callable]) -> None:
        """Update the absorbed button's longpress action. Called on pedalboard
        load from Hardware.__init_encoders to overlay per-pedalboard config."""
        self._longpress = callback

    def _on_button(self, state) -> None:
        # GpioSwitch fires its `callback` for short press / release events.
        # Treat anything that's not LONGPRESSED as a press.
        if state == switchstate.Value.LONGPRESSED:
            return
        if self.sink is not None:
            self.sink.handle(SwitchEvent(controller=self, kind=SwitchEventKind.PRESS))
            return
        # Legacy fallback for callers that don't set a sink.
        if self._shortpress is not None:
            self._shortpress(state)

    def _on_button_longpress(self, state) -> None:
        if self.sink is not None:
            self.sink.handle(SwitchEvent(controller=self, kind=SwitchEventKind.LONGPRESS))
            return
        if self._longpress is not None:
            self._longpress(state)
