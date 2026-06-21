"""Full-screen LCD panel for the NAM Capture pedalboard marker.

Layout (320×240):

    ┌─────────────────────────────┐
    │       NAM CAPTURE           │ y=8
    │  ─────────────────────────  │
    │  Name: [my-amp           ]  │ y=50
    │                             │
    │         2:34                │ y=100  countdown (large, centred)
    │      Ready / Failed…        │ y=148  status
    │   /Audio Recordings/…       │ y=168  path or error
    │                             │
    │  [ Start ] [ Abort ] [Dismiss] │ y=210  chrome row (plugin-panel style)
    └─────────────────────────────┘

Buttons cycle with the encoder; pressing the selected button fires its action.
The name field opens a TextEditor when pressed.
"""

from __future__ import annotations

import math
import time
from pathlib import Path
from typing import Callable

from uilib.box import Box
from uilib.config import Config
from uilib.label import Label
from uilib.text import Button

from pistomp.fullscreen_panel import FullscreenPanel
from pistomp.input.event import ControllerEvent, EncoderEvent
from pistomp.nam.engine import CaptureState, NamCaptureEngine
from pistomp.nam.wavio import wav_duration

_W = 320
_H = 240

# Chrome row — identical constants to plugins/base.py
_BTN_GAP = 2
_BTN_H = 28
_BTN_Y = _H - _BTN_H - _BTN_GAP  # 210
_BTN_W = (_W - 4 * _BTN_GAP) // 3  # 104

_REAMP_WAV = Path(__file__).resolve().parents[2] / "setup" / "nam" / "v3_0_0.wav"

_STATUS_TEXT: dict[CaptureState, str] = {
    CaptureState.IDLE: "Ready",
    CaptureState.CAPTURING: "Capturing…",
    CaptureState.DONE: "Done",
    CaptureState.FAILED: "Failed",
    CaptureState.ABORTED: "Aborted",
}

_STATUS_COLOR: dict[CaptureState, tuple[int, int, int]] = {
    CaptureState.IDLE: (180, 180, 180),
    CaptureState.CAPTURING: (0, 200, 80),
    CaptureState.DONE: (0, 200, 0),
    CaptureState.FAILED: (220, 40, 40),
    CaptureState.ABORTED: (180, 100, 0),
}


def _fmt_time(seconds: float) -> str:
    s = max(0, int(seconds))
    return f"{s // 60}:{s % 60:02d}"


class NamCapturePanel(FullscreenPanel):
    """Full-screen panel for NAM capture.  Owns the engine lifecycle."""

    def __init__(
        self,
        output_dir: str | Path,
        on_dismiss: Callable[[], None],
        reamp_wav: Path = _REAMP_WAV,
        handler=None,
    ) -> None:
        super().__init__()
        self._on_dismiss = on_dismiss
        self._handler = handler
        self._engine = self._create_engine(output_dir, reamp_wav)
        self._last_state = CaptureState.IDLE
        self._last_countdown: str = ""
        self._last_level_update: float = 0.0
        self._prev_diff_none: bool = True

        # Pre-read duration from WAV header (fast — no sample loading).
        try:
            self._duration = wav_duration(reamp_wav)
        except Exception:
            self._duration = 0.0

        font = Config().get_font("default")

        # Title
        title = Label(0, 8, font, parent=self)
        title.set_text("NAM CAPTURE", (255, 200, 0), x=_W // 2 - 50)

        # Capture name field
        name_lbl = Label(8, 53, font, parent=self)
        name_lbl.set_text("Name:", (160, 160, 160))
        self._name_btn = Button(
            box=Box.xywh(58, 46, _W - 66, _BTN_H),
            text="capture",
            font=font,
            outline_radius=3,
            edit_message="Capture name:",
            parent=self,
        )
        self.add_sel_widget(self._name_btn)

        # Countdown clock — large, centred
        self._countdown_lbl = Label(0, 100, font, parent=self)

        # Level difference label — shown during capture
        self._level_lbl = Label(0, 128, font, parent=self)

        # Status and info labels
        self._status_lbl = Label(8, 148, font, parent=self)
        self._info_lbl = Label(8, 168, font, parent=self)

        # Chrome buttons — same geometry as plugins/base.py
        self._btn_start = Button(
            box=Box.xywh(_BTN_GAP, _BTN_Y, _BTN_W, _BTN_H),
            text="Start",
            font=font,
            outline_radius=4,
            parent=self,
            action=lambda *_: self._on_start(),
        )
        self._btn_abort = Button(
            box=Box.xywh(_BTN_GAP * 2 + _BTN_W, _BTN_Y, _BTN_W, _BTN_H),
            text="Abort",
            font=font,
            outline_radius=4,
            parent=self,
            action=lambda *_: self._on_abort(),
        )
        self._btn_dismiss = Button(
            box=Box.xywh(_BTN_GAP * 3 + _BTN_W * 2, _BTN_Y, _BTN_W, _BTN_H),
            text="Dismiss",
            font=font,
            outline_radius=4,
            parent=self,
            action=lambda *_: on_dismiss(),
        )
        self.add_sel_widget(self._btn_start)
        self.add_sel_widget(self._btn_abort)
        self.add_sel_widget(self._btn_dismiss)

        self._apply_state(CaptureState.IDLE)

    def handle(self, event: ControllerEvent) -> bool:
        if isinstance(event, EncoderEvent) and self._handler is not None:
            cid = getattr(event.controller, "id", None)
            if cid == 3:
                self._handler.system_menu_input_gain(event.rotations)
                return True
            if cid == 2:
                self._handler.system_menu_headphone_volume(event.rotations)
                return True
        return False

    def _create_engine(self, output_dir: str | Path, reamp_wav: Path) -> NamCaptureEngine:
        return NamCaptureEngine(output_dir, reamp_wav=reamp_wav)

    # ── Panel lifecycle ───────────────────────────────────────────────────────

    def destroy(self) -> None:
        self._engine.stop()
        super().destroy()

    # ── poll ─────────────────────────────────────────────────────────────────

    def tick(self) -> None:
        state = self._engine.state
        if state != self._last_state:
            self._apply_state(state)
            self._last_state = state

        if state == CaptureState.CAPTURING and self._duration > 0:
            remaining = self._duration * (1.0 - self._engine.progress())
            countdown = _fmt_time(remaining)
            if countdown != self._last_countdown:
                self._countdown_lbl.set_text(countdown, (255, 200, 0), x=_W // 2 - 20)
                self._last_countdown = countdown

            now = time.monotonic()
            if now - self._last_level_update >= 0.5:
                self._last_level_update = now
                diff = self._engine.level_diff_db()
                prev_none = self._prev_diff_none
                self._prev_diff_none = diff is None
                if diff is not None:
                    sign = "+" if diff >= 0 else ""
                    self._level_lbl.set_text(f"Δ {sign}{diff:.1f} dB", (160, 160, 200), x=_W // 2 - 30)
                    if prev_none:
                        # First reading after silence may be skewed — replace it quickly
                        self._last_level_update = now - 0.4
                else:
                    self._level_lbl.set_text("---", (80, 80, 80), x=_W // 2 - 10)

    # ── private ───────────────────────────────────────────────────────────────

    def _on_start(self) -> None:
        if self._engine.state not in (
            CaptureState.IDLE,
            CaptureState.DONE,
            CaptureState.FAILED,
            CaptureState.ABORTED,
        ):
            return
        name = self._name_btn.text or "capture"
        self._last_countdown = ""
        self._engine.start(name)

    def _on_abort(self) -> None:
        if self._engine.state == CaptureState.CAPTURING:
            self._engine.stop()

    def _apply_state(self, state: CaptureState) -> None:
        color = _STATUS_COLOR[state]
        self._status_lbl.set_text(_STATUS_TEXT[state], color)

        _dim = (80, 80, 80)
        _level_x = _W // 2 - 10
        if state == CaptureState.IDLE:
            self._set_countdown(_fmt_time(self._duration), (100, 100, 100))
            self._level_lbl.set_text("---", _dim, x=_level_x)
            self._info_lbl.set_text("", (0, 0, 0))
        elif state == CaptureState.CAPTURING:
            self._set_countdown(_fmt_time(self._duration), (255, 200, 0))
            self._last_countdown = _fmt_time(self._duration)
            self._prev_diff_none = True
            self._level_lbl.set_text("---", _dim, x=_level_x)
            self._info_lbl.set_text("", (0, 0, 0))
        elif state == CaptureState.DONE:
            self._set_countdown("0:00", (0, 200, 0))
            self._level_lbl.set_text("---", _dim, x=_level_x)
            path = self._engine.output_path
            if path is not None:
                self._info_lbl.set_text(path.name, (140, 200, 140))
        elif state == CaptureState.FAILED:
            self._set_countdown("--:--", (220, 40, 40))
            self._level_lbl.set_text("---", _dim, x=_level_x)
            err = self._engine.error or "Unknown error"
            self._info_lbl.set_text(err[:40], (220, 80, 80))
        else:  # ABORTED
            self._set_countdown(_fmt_time(self._duration), (100, 100, 100))
            self._level_lbl.set_text("---", _dim, x=_level_x)
            self._info_lbl.set_text("", (0, 0, 0))

    def _set_countdown(self, text: str, color: tuple[int, int, int]) -> None:
        self._countdown_lbl.set_text(text, color, x=_W // 2 - 20)
        self._last_countdown = text
