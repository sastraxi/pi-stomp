"""Full-screen LCD panel for the NAM Capture pedalboard marker.

Layout (320×240) — VU-console style:

    ┌─ NAM Capture ─────────────────┐ title bar (house style)
    │ Name: [ capture            ]  │
    │  ┌── TIME ──┐  ┌── LEVEL ──┐   │ two recessed gauges
    │  │   1:44   │  │  +0.3 dB  │   │
    │  └──────────┘  └───────────┘   │
    │ [ Close ]            [ Abort ] │ chrome row
    └───────────────────────────────┘

Exit ("Close") sits in the far-left slot, matching the tuner/plugin panels;
a single contextual action button ("Start" ↔ "Abort") sits far-right. On a
failed capture the gauge pair is swapped for the error message. The name field
opens a TextEditor when pressed.
"""

from __future__ import annotations

import textwrap
import time
from pathlib import Path
from typing import Callable

from uilib.box import Box
from uilib.config import Config
from uilib.label import Label
from uilib.misc import TextHAlign, get_text_bbox, get_text_size
from uilib.pygame_init import font as _make_font
from uilib.text import Button, TextWidget
from uilib.widget import Widget

from pistomp.fullscreen_panel import FullscreenPanel
from pistomp.input.event import ControllerEvent, EncoderEvent
from pistomp.nam.engine import CaptureState, NamCaptureEngine
from pistomp.nam.wavio import wav_duration

_W = 320
_H = 240

_FONTS_DIR = Path(__file__).resolve().parents[2] / "fonts"
_REAMP_WAV = Path(__file__).resolve().parents[2] / "setup" / "nam" / "v3_0_0.wav"

# Title bar
_TITLE_H = 26

# Name row
_NAME_Y = 30
_NAME_H = 28

# Gauges
_G_TOP = 86
_G_H = 104
_G_MARGIN = 8
_G_GAP = 10
_G_W = (_W - 2 * _G_MARGIN - _G_GAP) // 2  # 147
_G_CAPTION_Y = 10  # gauge-local
_G_VALUE_Y = 44  # gauge-local

# Chrome row — same geometry as the tuner/plugin panels
_BTN_GAP = 2
_BTN_H = 28
_BTN_Y = _H - _BTN_H - _BTN_GAP  # 210
_BTN_W = (_W - 4 * _BTN_GAP) // 3  # 104
_BTN_X_CLOSE = _BTN_GAP  # far-left, matches tuner "Close" / plugin "Back"
_BTN_X_ACTION = _BTN_GAP * 3 + _BTN_W * 2  # far-right

# Colours
_GAUGE_BG = (12, 12, 14)
_GAUGE_OUTLINE = (70, 70, 78)
_CAPTION_FG = (110, 110, 118)
_DIM = (90, 90, 96)
_TIMER_CAP = (255, 200, 0)
_TIMER_IDLE = (150, 150, 156)
_TIMER_DONE = (0, 210, 90)
_LEVEL_FG = (150, 180, 220)
_ERR_FG = (230, 90, 90)

_STATUS: dict[CaptureState, tuple[str, tuple[int, int, int]]] = {
    CaptureState.IDLE: ("Ready", (150, 150, 156)),
    CaptureState.CAPTURING: ("Capturing…", (0, 200, 80)),
    CaptureState.DONE: ("Done", (0, 210, 90)),
    CaptureState.FAILED: ("Capture failed", _ERR_FG),
    CaptureState.ABORTED: ("Aborted", (180, 100, 0)),
}


def _fmt_time(seconds: float) -> str:
    s = max(0, int(seconds))
    return f"{s // 60}:{s % 60:02d}"


def _centred_x(text: str, font, width: int) -> int:
    bb = get_text_bbox(text, font)
    return (width - (bb[2] - bb[0])) // 2 - bb[0]


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
        title_font = Config().get_font("default_title")
        self._caption_font = _make_font(str(_FONTS_DIR / "DejaVuSans-Bold.ttf"), 12)
        self._value_font = _make_font(str(_FONTS_DIR / "DejaVuSans-Bold.ttf"), 30)
        self._status_font = _make_font(str(_FONTS_DIR / "DejaVuSans.ttf"), 14)

        # Title bar — centred bold on the house grey/amber strip
        _, title_h = get_text_size("NAM Capture", title_font)
        TextWidget(
            box=Box.xywh(0, 0, _W, _TITLE_H),
            text="NAM Capture",
            font=title_font,
            text_halign=TextHAlign.CENTRE,
            h_margin=0,
            v_margin=max(0, (_TITLE_H - title_h) // 2),
            outline=0,
            bkgnd_color=Config().get_color("default_title_bkgnd"),
            fgnd_color=Config().get_color("default_title_fgnd"),
            parent=self,
        )

        # Capture name field
        Label(10, _NAME_Y + 7, font, parent=self).set_text("Name:", (160, 160, 160))
        self._name_btn = Button(
            box=Box.xywh(64, _NAME_Y, _W - 72, _NAME_H),
            text="capture",
            font=font,
            outline_radius=3,
            edit_message="Capture name:",
            parent=self,
        )
        self.add_sel_widget(self._name_btn)

        # Status line — subtle, centred under the name field
        self._status_lbl = Label(0, 64, self._status_font, parent=self)

        # Two recessed VU-console gauges: TIME (countdown) and LEVEL (dbFS Δ)
        self._gauge_time = self._make_gauge(_G_MARGIN, "TIME")
        self._gauge_level = self._make_gauge(_G_MARGIN + _G_W + _G_GAP, "LEVEL")
        self._timer_value = Label(0, _G_VALUE_Y, self._value_font, parent=self._gauge_time)
        self._level_value = Label(0, _G_VALUE_Y, self._value_font, parent=self._gauge_level)

        # Error message — replaces the gauges on a failed capture
        self._error_lbl = TextWidget(
            box=Box.xywh(_G_MARGIN, _G_TOP, _W - 2 * _G_MARGIN, _G_H),
            text="",
            font=font,
            text_halign=TextHAlign.CENTRE,
            v_margin=28,
            outline=0,
            fgnd_color=_ERR_FG,
            visible=False,
            parent=self,
        )

        # Chrome buttons — exit far-left (matches tuner/plugin), action far-right
        self._btn_close = Button(
            box=Box.xywh(_BTN_X_CLOSE, _BTN_Y, _BTN_W, _BTN_H),
            text="Close",
            font=font,
            outline_radius=4,
            parent=self,
            action=lambda *_: on_dismiss(),
        )
        self._btn_action = Button(
            box=Box.xywh(_BTN_X_ACTION, _BTN_Y, _BTN_W, _BTN_H),
            text="Start",
            font=font,
            outline_radius=4,
            parent=self,
            action=lambda *_: self._on_action(),
        )
        self.add_sel_widget(self._btn_close)
        self.add_sel_widget(self._btn_action)

        self._apply_state(CaptureState.IDLE)

    def _make_gauge(self, x: int, caption: str) -> Widget:
        gauge = Widget(
            box=Box.xywh(x, _G_TOP, _G_W, _G_H),
            bkgnd_color=_GAUGE_BG,
            outline=1,
            outline_color=_GAUGE_OUTLINE,
            outline_radius=6,
            parent=self,
        )
        cap = Label(0, _G_CAPTION_Y, self._caption_font, parent=gauge)
        cap.set_text(caption, _CAPTION_FG, x=_centred_x(caption, self._caption_font, _G_W))
        return gauge

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
                self._set_timer(countdown, _TIMER_CAP)
                self._last_countdown = countdown

            now = time.monotonic()
            if now - self._last_level_update >= 0.5:
                self._last_level_update = now
                diff = self._engine.level_diff_db()
                prev_none = self._prev_diff_none
                self._prev_diff_none = diff is None
                if diff is not None:
                    sign = "+" if diff >= 0 else ""
                    self._set_level(f"{sign}{diff:.1f} dB", _LEVEL_FG)
                    if prev_none:
                        # First reading after silence may be skewed — replace it quickly
                        self._last_level_update = now - 0.4
                else:
                    self._set_level("---", _DIM)

    # ── private ───────────────────────────────────────────────────────────────

    def _on_action(self) -> None:
        if self._engine.state == CaptureState.CAPTURING:
            self._on_abort()
        else:
            self._on_start()

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
        text, color = _STATUS[state]
        self._btn_action.set_text("Abort" if state == CaptureState.CAPTURING else "Start")

        if state == CaptureState.FAILED:
            self._show_error(self._engine.error or "Unknown error")
            self._set_status(text, color)
            return

        self._show_gauges()
        self._set_level("---", _DIM)

        if state == CaptureState.IDLE:
            self._set_timer(_fmt_time(self._duration), _TIMER_IDLE)
            self._set_status(text, color)
        elif state == CaptureState.CAPTURING:
            self._set_timer(_fmt_time(self._duration), _TIMER_CAP)
            self._last_countdown = _fmt_time(self._duration)
            self._prev_diff_none = True
            self._set_status(text, color)
        elif state == CaptureState.DONE:
            self._set_timer("0:00", _TIMER_DONE)
            path = self._engine.output_path
            self._set_status(f"Saved · {path.name}" if path is not None else text, color)
        else:  # ABORTED
            self._set_timer(_fmt_time(self._duration), _TIMER_IDLE)
            self._set_status(text, color)

    def _set_timer(self, text: str, color: tuple[int, int, int]) -> None:
        self._timer_value.set_text(text, color, x=_centred_x(text, self._value_font, _G_W))

    def _set_level(self, text: str, color: tuple[int, int, int]) -> None:
        self._level_value.set_text(text, color, x=_centred_x(text, self._value_font, _G_W))

    def _set_status(self, text: str, color: tuple[int, int, int]) -> None:
        self._status_lbl.set_text(text, color, x=_centred_x(text, self._status_font, _W))

    def _show_error(self, message: str) -> None:
        self._gauge_time.hide(refresh=False)
        self._gauge_level.hide(refresh=False)
        self._error_lbl.set_text("\n".join(textwrap.wrap(message, width=34)[:3]))
        self._error_lbl.show()

    def _show_gauges(self) -> None:
        self._error_lbl.hide(refresh=False)
        self._gauge_time.show(refresh=False)
        self._gauge_level.show()
