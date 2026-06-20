"""Full-screen LCD panel for the NAM Capture pedalboard marker.

Layout (320×240):

    ┌─────────────────────────────┐
    │       NAM CAPTURE           │ y=8
    │  ─────────────────────────  │
    │  Name: [my-amp           ]  │ y=50  (editable button)
    │  ──── ████████░░░░░░░░░░ ── │ y=96  (progress bar)
    │  Status: Idle / Capturing…  │ y=116
    │  /Audio Recordings/out.wav  │ y=136
    │                             │
    │     [  Start  ] [Abort]     │ y=182
    │        [ Dismiss ]          │ y=210
    └─────────────────────────────┘

Buttons navigate with the encoder; pressing the selected button fires its
action.  The name field opens a TextEditor when pressed.
"""

from __future__ import annotations

from typing import Callable

from uilib.box import Box
from uilib.config import Config
from uilib.icon import Icon
from uilib.label import Label
from uilib.panel import Panel
from uilib.text import Button

from pistomp.input.event import ControllerEvent
from pistomp.input.sink import InputSink
from pistomp.nam.engine import CaptureState, NamCaptureEngine

_W = 320
_H = 240
_BTN_H = 28
_BTN_GAP = 4


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


class NamCapturePanel(Panel, InputSink):
    """Full-screen panel for NAM capture. Mounted when a board has nam_capture: true."""

    def __init__(
        self,
        engine: NamCaptureEngine,
        on_dismiss: Callable[[], None],
    ) -> None:
        super().__init__(box=Box.xywh(0, 0, _W, _H), auto_destroy=True, no_dim=True)
        self._engine = engine
        self._on_dismiss = on_dismiss
        self._last_state = CaptureState.IDLE
        self._last_progress: float = -1.0

        font = Config().get_font("default")

        # Title
        self._title = Label(0, 8, font, parent=self)
        self._title.set_text("NAM CAPTURE", (255, 200, 0), x=_W // 2 - 50)

        # Capture name field (editable Button)
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

        # Progress bar
        self._progress_bar = Icon(
            box=Box.xywh(8, 90, _W - 16, 18),
            text="",
            parent=self,
        )

        # Status label
        self._status_lbl = Label(8, 116, font, parent=self)
        self._status_lbl.set_text("Ready", _STATUS_COLOR[CaptureState.IDLE])

        # Output path / error hint
        self._info_lbl = Label(8, 136, font, parent=self)

        # Action buttons
        half = (_W - _BTN_GAP * 3) // 2
        self._btn_start = Button(
            box=Box.xywh(_BTN_GAP, 178, half, _BTN_H),
            text="Start",
            font=font,
            outline_radius=4,
            parent=self,
            action=lambda *_: self._on_start(),
        )
        self._btn_abort = Button(
            box=Box.xywh(_BTN_GAP * 2 + half, 178, half, _BTN_H),
            text="Abort",
            font=font,
            outline_radius=4,
            parent=self,
            action=lambda *_: self._on_abort(),
        )
        dismiss_w = _W - _BTN_GAP * 2
        self._btn_dismiss = Button(
            box=Box.xywh(_BTN_GAP, 210, dismiss_w, _BTN_H),
            text="Dismiss",
            font=font,
            outline_radius=4,
            parent=self,
            action=lambda *_: on_dismiss(),
        )

        self.add_sel_widget(self._btn_start)
        self.add_sel_widget(self._btn_abort)
        self.add_sel_widget(self._btn_dismiss)

        # Initialise to IDLE visual state
        self._apply_state(CaptureState.IDLE)

    # ── InputSink ────────────────────────────────────────────────────────────

    def handle(self, event: ControllerEvent) -> bool:
        return False

    # ── poll ─────────────────────────────────────────────────────────────────

    def tick(self) -> None:
        state = self._engine.state
        progress = self._engine.progress()

        if state != self._last_state:
            self._apply_state(state)
            self._last_state = state

        if state == CaptureState.CAPTURING and abs(progress - self._last_progress) > 0.005:
            self._progress_bar.set_progress(progress)
            self._last_progress = progress

    # ── private ───────────────────────────────────────────────────────────────

    def _on_start(self) -> None:
        state = self._engine.state
        if state not in (CaptureState.IDLE, CaptureState.DONE, CaptureState.FAILED, CaptureState.ABORTED):
            return
        name = self._name_btn.text or "capture"
        self._last_progress = -1.0
        self._progress_bar.set_progress(0.0)
        self._engine.start(name)

    def _on_abort(self) -> None:
        if self._engine.state == CaptureState.CAPTURING:
            self._engine.stop()

    def _apply_state(self, state: CaptureState) -> None:
        color = _STATUS_COLOR[state]
        self._status_lbl.set_text(_STATUS_TEXT[state], color)

        if state == CaptureState.CAPTURING:
            self._progress_bar.set_progress(0.0)
            self._info_lbl.set_text("", (0, 0, 0))
        elif state == CaptureState.DONE:
            self._progress_bar.set_progress(1.0)
            path = self._engine.output_path
            if path is not None:
                self._info_lbl.set_text(path.name, (140, 200, 140))
        elif state == CaptureState.FAILED:
            self._progress_bar.set_progress(None)
            err = self._engine.error or "Unknown error"
            self._info_lbl.set_text(err[:40], (220, 80, 80))
        else:
            self._progress_bar.set_progress(None)
            self._info_lbl.set_text("", (0, 0, 0))
