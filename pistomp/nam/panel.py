"""Full-screen LCD panel for the NAM Capture pedalboard marker.

Two distinct layouts:

  SETUP VIEW (IDLE)
  ┌─ NAM Capture ─────────────────────────────────┐
  │ Name: [ my-fender-clean                     ] │
  │ ~3:10 · T3K-sweep-v3                          │
  │   ╭──────────╮           ╭──────────╮         │
  │   │  INPUT   │           │  HEADPH  │         │
  │   │   GAIN   │           │   VOL    │         │
  │   │  (knob)  │           │  (knob)  │         │
  │   │  -6.0 dB │           │ -12.0 dB │         │
  │   ╰──────────╯           ╰──────────╯         │
  │ [ Close ]                         [ Start ]   │
  └───────────────────────────────────────────────┘

  CAPTURE VIEW (CAPTURING / DONE / FAILED / ABORTED)
  ┌─ ● NAM Capture              my-fender-clean ──┐
  │    ╭────╮  ▓▓▓▓▓▓▓░░░░░░░░░░  ╭──────╮      │
  │    │ ◎  │▓▓▓▓▓▓▓▓▓░░░░░░░░░░│  ◎    │      │
  │    ╰────╯   0:52 elapsed  1:18 remaining      │
  │ OUT ████████████░░░░░░  -9.1dB                │
  │  IN ████████████████░░  -3.1dB                │
  │                              [ Abort ]        │
  └───────────────────────────────────────────────┘

Levels freeze on failure (FAILED/ABORTED) so the screen serves as a
diagnostic snapshot. Encoders 2/3 control headphone vol / input gain
in both views (the setup view shows knobs; the capture view adjusts
silently).
"""

from __future__ import annotations

import math
import time
from pathlib import Path
from typing import Callable

from uilib.box import Box
from uilib.config import Config
from uilib.label import Label
from uilib.misc import TextHAlign, get_text_bbox, get_text_size
from uilib.paint import PaintContext
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
_REAMP_WAV = Path(__file__).resolve().parents[2] / "setup" / "nam" / "T3K-sweep-v3.wav"

# ── Layout constants ──────────────────────────────────────────────────────────

# Setup view
_TITLE_H = 26
_NAME_Y = 30
_NAME_H = 28
_HINT_Y = 66
_KNOB_Y = 82
_KNOB_H = 114
_KNOB_W = 148

# Chrome row — shared between views
_BTN_GAP = 2
_BTN_H = 28
_BTN_Y = _H - _BTN_H - _BTN_GAP  # 210
_BTN_W = (_W - 4 * _BTN_GAP) // 3  # 104
_BTN_X_CLOSE = _BTN_GAP
_BTN_X_ACTION = _BTN_GAP * 3 + _BTN_W * 2

# Capture view
_CAP_HDR_H = 22
_REEL_Y = _CAP_HDR_H
_REEL_H = 110
_ERR_Y = _REEL_Y + _REEL_H + 2       # 134 — feedback text above meters
_METER_H = 22
_METER_OUT_Y = _ERR_Y + _METER_H     # 156
_METER_IN_Y = _METER_OUT_Y + _METER_H + 2  # 180

# ── Colour palette ────────────────────────────────────────────────────────────

# Reel / tape
_REEL_BODY = (30, 25, 15)
_REEL_RIM = (100, 80, 40)
_REEL_HUB = (60, 50, 30)
_TAPE_FILLED = (148, 92, 22)
_TAPE_EMPTY = (22, 17, 8)

# Status LED
_LED_IDLE = (70, 70, 78)
_LED_CAPTURING = (0, 200, 80)
_LED_DONE = (0, 210, 90)
_LED_FAILED = (230, 70, 70)
_LED_ABORTED = (160, 90, 20)
_LED_OFF = (14, 14, 17)

# Level meters
_SEG_GREEN = (0, 175, 55)
_SEG_YELLOW = (195, 165, 0)
_SEG_RED = (215, 55, 40)
_SEG_OFF = (16, 20, 14)
_METER_LABEL_FG = (110, 110, 118)
_METER_VALUE_FG = (155, 155, 165)
_METER_CLIP_FG = (220, 60, 50)

# Knobs
_KNOB_BODY = (28, 28, 36)
_KNOB_RIM = (75, 75, 90)
_KNOB_INDICATOR = (255, 200, 55)
_KNOB_LABEL_FG = (115, 115, 125)
_KNOB_VALUE_FG = (175, 175, 195)

# Misc
_DIM = (75, 75, 82)
_ERR_FG = (225, 85, 85)
_HEADER_FG = (130, 130, 140)
_HEADER_NAME_FG = (100, 100, 110)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _fmt_time(seconds: float) -> str:
    s = max(0, int(seconds))
    return f"{s // 60}:{s % 60:02d}"


def _centred_x(text: str, font, width: int) -> int:
    bb = get_text_bbox(text, font)
    return (width - (bb[2] - bb[0])) // 2 - bb[0]


# ── Custom widgets ────────────────────────────────────────────────────────────


class KnobWidget(Widget):
    """Skeuomorphic rotary knob for audio parameter display/control."""

    def __init__(
        self,
        box: Box,
        label: str,
        min_val: float,
        max_val: float,
        default_font,
        caption_font,
        parent: Widget,
    ) -> None:
        super().__init__(box=box, bkgnd_color=(0, 0, 0), parent=parent)
        self._label = label
        self._min_val = min_val
        self._max_val = max_val
        self._default_font = default_font
        self._caption_font = caption_font
        self._value = min_val

    def set_value(self, value: float) -> None:
        self._value = max(self._min_val, min(self._max_val, value))
        self.refresh()

    def _draw(self, ctx: PaintContext) -> None:
        w, h = ctx.width, ctx.height
        label_h = 16
        value_h = 20
        knob_area_h = h - label_h - value_h

        knob_r = min(w // 2 - 6, knob_area_h // 2 - 4)
        cx = w // 2
        cy = label_h + knob_area_h // 2

        # Category label
        ctx.draw_text((cx, label_h // 2), self._label, fill=_KNOB_LABEL_FG, font=self._caption_font, anchor="mm")

        # Knob body
        body_box = Box.xywh(cx - knob_r, cy - knob_r, 2 * knob_r, 2 * knob_r)
        ctx.draw_ellipse(body_box, fill=_KNOB_BODY, outline=_KNOB_RIM, width=2)

        # Range arc background (the swept area, very dim)
        for angle_step in range(0, 301, 3):
            a = math.radians(210.0 + angle_step)
            rx = cx + int((knob_r - 4) * math.sin(a))
            ry = cy - int((knob_r - 4) * math.cos(a))
            dot_box = Box.xywh(rx - 1, ry - 1, 3, 3)
            ctx.draw_ellipse(dot_box, fill=(45, 45, 55))

        # Indicator line + tip dot
        t = (self._value - self._min_val) / (self._max_val - self._min_val) if self._max_val != self._min_val else 0.0
        t = max(0.0, min(1.0, t))
        angle_deg = 210.0 + t * 300.0
        angle_rad = math.radians(angle_deg)
        r_inner = int(knob_r * 0.70)
        end_x = cx + int(r_inner * math.sin(angle_rad))
        end_y = cy - int(r_inner * math.cos(angle_rad))
        ctx.draw_line([(cx, cy), (end_x, end_y)], fill=_KNOB_INDICATOR, width=3)
        dot_r = 3
        ctx.draw_ellipse(Box.xywh(end_x - dot_r, end_y - dot_r, dot_r * 2, dot_r * 2), fill=_KNOB_INDICATOR)

        # Value text
        value_text = f"{self._value:.1f} dB"
        ctx.draw_text((cx, h - value_h // 2), value_text, fill=_KNOB_VALUE_FG, font=self._default_font, anchor="mm")


class ReelWidget(Widget):
    """Tape-reel progress display with elapsed/remaining time labels."""

    _LEFT_CX = 55
    _RIGHT_CX = 265
    _REEL_CY = 50
    _MAX_R = 36
    _MIN_R = 10
    _HUB_R = 5
    _TAPE_Y = 46
    _TAPE_H = 9

    def __init__(self, box: Box, total_seconds: float, reel_font, caption_font, parent: Widget) -> None:
        super().__init__(box=box, bkgnd_color=(0, 0, 0), parent=parent)
        self._total = total_seconds
        self._progress = 0.0
        self._frozen = False
        self._elapsed = 0.0
        self._remaining = total_seconds
        self._reel_font = reel_font
        self._caption_font = caption_font

    def set_progress(self, progress: float) -> None:
        if self._frozen:
            return
        p = max(0.0, min(1.0, progress))
        elapsed = p * self._total
        # Only redraw when the integer-second display changes
        if int(elapsed) == int(self._elapsed) and abs(p - self._progress) < 0.002:
            self._progress = p
            self._elapsed = elapsed
            self._remaining = self._total - elapsed
            return
        self._progress = p
        self._elapsed = elapsed
        self._remaining = self._total - elapsed
        self.refresh()

    def freeze(self) -> None:
        self._frozen = True

    def set_done(self) -> None:
        self._progress = 1.0
        self._elapsed = self._total
        self._remaining = 0.0
        self._frozen = True
        self.refresh()

    def reset(self) -> None:
        self._progress = 0.0
        self._elapsed = 0.0
        self._remaining = self._total
        self._frozen = False
        self.refresh()

    def _draw(self, ctx: PaintContext) -> None:
        p = self._progress
        lc = self._LEFT_CX
        rc = self._RIGHT_CX
        cy = self._REEL_CY
        max_r, min_r, hub_r = self._MAX_R, self._MIN_R, self._HUB_R

        left_r = int(max_r * (1.0 - p) + min_r * p)
        right_r = int(min_r * (1.0 - p) + max_r * p)

        tape_left = lc + left_r + 3
        tape_right = rc - right_r - 3

        ctx.fill((0, 0, 0))

        # Tape ribbon
        if tape_left < tape_right:
            tape_w = tape_right - tape_left
            ctx.draw_rectangle(Box.xywh(tape_left, self._TAPE_Y, tape_w, self._TAPE_H), fill=_TAPE_EMPTY)
            filled_w = int(tape_w * p)
            if filled_w > 0:
                ctx.draw_rectangle(Box.xywh(tape_left, self._TAPE_Y, filled_w, self._TAPE_H), fill=_TAPE_FILLED)

        # Left reel (feed — shrinks)
        ctx.draw_ellipse(
            Box.xywh(lc - left_r, cy - left_r, 2 * left_r, 2 * left_r),
            fill=_REEL_BODY,
            outline=_REEL_RIM,
            width=2,
        )
        ctx.draw_ellipse(Box.xywh(lc - hub_r, cy - hub_r, 2 * hub_r, 2 * hub_r), fill=_REEL_HUB)

        # Right reel (take-up — grows)
        ctx.draw_ellipse(
            Box.xywh(rc - right_r, cy - right_r, 2 * right_r, 2 * right_r),
            fill=_REEL_BODY,
            outline=_REEL_RIM,
            width=2,
        )
        ctx.draw_ellipse(Box.xywh(rc - hub_r, cy - hub_r, 2 * hub_r, 2 * hub_r), fill=_REEL_HUB)

        # Time labels — elapsed left, countdown right with leading "−"
        elapsed_str = _fmt_time(self._elapsed)
        remaining_str = f"−{_fmt_time(self._remaining)}"
        label_y = self._REEL_CY + self._MAX_R + 14

        ctx.draw_text((lc, label_y), elapsed_str, fill=(125, 100, 48), font=self._caption_font, anchor="mm")
        ctx.draw_text((rc, label_y), remaining_str, fill=(200, 158, 68), font=self._reel_font, anchor="mm")


class LevelMeter(Widget):
    """Segmented horizontal VU meter with dB readout and clip indicator."""

    _SEG_COUNT = 20
    _SEG_GREEN_MAX = 10
    _SEG_YELLOW_MAX = 16
    _LABEL_W = 28
    _BAR_X = 30
    _SEG_W = 10
    _SEG_GAP = 1
    # Total bar width: 20*10 + 19*1 = 219px → x=30 to x=249
    _VALUE_CX = 284  # center of value region x=252..316

    def __init__(self, box: Box, label: str, default_font, caption_font, parent: Widget) -> None:
        super().__init__(box=box, bkgnd_color=(0, 0, 0), parent=parent)
        self._label = label
        self._default_font = default_font
        self._caption_font = caption_font
        self._level_db: float | None = None
        self._clipping: bool = False

    def set_level(self, db: float | None) -> None:
        self._level_db = db
        self.refresh()

    def set_clip(self, clipping: bool) -> None:
        if self._clipping != clipping:
            self._clipping = clipping
            self.refresh()

    def _db_to_segments(self, db: float) -> int:
        return max(0, min(self._SEG_COUNT, int((db + 60.0) / 60.0 * self._SEG_COUNT + 0.5)))

    def _draw(self, ctx: PaintContext) -> None:
        h = ctx.height
        bar_y = (h - 10) // 2
        bar_h = 10

        ctx.draw_text(
            (self._LABEL_W // 2, h // 2), self._label, fill=_METER_LABEL_FG, font=self._caption_font, anchor="mm"
        )

        if self._clipping:
            n_segs = self._SEG_COUNT
        elif self._level_db is not None:
            n_segs = self._db_to_segments(self._level_db)
        else:
            n_segs = 0

        for i in range(self._SEG_COUNT):
            sx = self._BAR_X + i * (self._SEG_W + self._SEG_GAP)
            lit = i < n_segs
            if not lit:
                color = _SEG_OFF
            elif self._clipping:
                color = _SEG_RED
            elif i < self._SEG_GREEN_MAX:
                color = _SEG_GREEN
            elif i < self._SEG_YELLOW_MAX:
                color = _SEG_YELLOW
            else:
                color = _SEG_RED
            ctx.draw_rectangle(Box.xywh(sx, bar_y, self._SEG_W, bar_h), fill=color)

        if self._clipping:
            text, color = "CLIP", _METER_CLIP_FG
        elif self._level_db is not None:
            sign = "+" if self._level_db >= 0 else ""
            text, color = f"{sign}{self._level_db:.1f}dB", _METER_VALUE_FG
        else:
            text, color = "---", _DIM

        ctx.draw_text((self._VALUE_CX, h // 2), text, fill=color, font=self._default_font, anchor="mm")


class StatusLed(Widget):
    """10×10 status indicator dot."""

    def __init__(self, x: int, y: int, parent: Widget) -> None:
        super().__init__(Box.xywh(x, y, 10, 10), bkgnd_color=(0, 0, 0), parent=parent)
        self._led_color = _LED_IDLE
        self._on = True

    def set_color(self, color: tuple[int, int, int]) -> None:
        self._led_color = color
        self._on = True
        self.refresh()

    def set_on(self, on: bool) -> None:
        if self._on != on:
            self._on = on
            self.refresh()

    def _draw(self, ctx: PaintContext) -> None:
        color = self._led_color if self._on else _LED_OFF
        ctx.draw_ellipse(ctx.bounds, fill=color)


# ── Main panel ────────────────────────────────────────────────────────────────


class NamCapturePanel(FullscreenPanel):
    """Full-screen panel for NAM capture. Owns the engine lifecycle."""

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
        self._last_level_update: float = 0.0
        self._last_blink: float = 0.0
        self._led_on: bool = True
        self._in_capture_view: bool = False
        self._duration = wav_duration(reamp_wav)

        font = Config().get_font("default")
        title_font = Config().get_font("default_title")
        self._caption_font = _make_font(str(_FONTS_DIR / "DejaVuSans-Bold.ttf"), 12)
        self._reel_font = _make_font(str(_FONTS_DIR / "DejaVuSans-Bold.ttf"), 18)

        # ── SETUP VIEW ────────────────────────────────────────────────────────

        _, title_h = get_text_size("NAM Capture", title_font)
        self._title_bar = TextWidget(
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

        self._setup_name_label = Label(10, _NAME_Y + 7, font, parent=self)
        self._setup_name_label.set_text("Name:", (160, 160, 160))

        self._name_btn = Button(
            box=Box.xywh(64, _NAME_Y, _W - 72, _NAME_H),
            text="capture",
            font=font,
            outline_radius=3,
            edit_message="Capture name:",
            parent=self,
        )

        duration_text = f"~{_fmt_time(self._duration)}  ·  T3K-sweep-v3"
        self._duration_hint = Label(0, _HINT_Y, self._caption_font, parent=self)
        self._duration_hint.set_text(
            duration_text, (78, 78, 86), x=_centred_x(duration_text, self._caption_font, _W)
        )

        self._knob_gain = KnobWidget(
            box=Box.xywh(8, _KNOB_Y, _KNOB_W, _KNOB_H),
            label="INPUT GAIN",
            min_val=-19.75,
            max_val=12.0,
            default_font=font,
            caption_font=self._caption_font,
            parent=self,
        )
        self._knob_vol = KnobWidget(
            box=Box.xywh(_W - _KNOB_W - 8, _KNOB_Y, _KNOB_W, _KNOB_H),
            label="HEADPH VOL",
            min_val=-25.75,
            max_val=6.0,
            default_font=font,
            caption_font=self._caption_font,
            parent=self,
        )

        self._btn_setup_close = Button(
            box=Box.xywh(_BTN_X_CLOSE, _BTN_Y, _BTN_W, _BTN_H),
            text="Close",
            font=font,
            outline_radius=4,
            parent=self,
            action=lambda *_: on_dismiss(),
        )
        self._btn_start = Button(
            box=Box.xywh(_BTN_X_ACTION, _BTN_Y, _BTN_W, _BTN_H),
            text="Start",
            font=font,
            outline_radius=4,
            parent=self,
            action=lambda *_: self._on_start(),
        )

        self._setup_group = [
            self._title_bar,
            self._setup_name_label,
            self._name_btn,
            self._duration_hint,
            self._knob_gain,
            self._knob_vol,
            self._btn_setup_close,
            self._btn_start,
        ]
        self.add_sel_widget(self._name_btn)
        self.add_sel_widget(self._btn_setup_close)
        self.add_sel_widget(self._btn_start)

        # ── CAPTURE VIEW ──────────────────────────────────────────────────────

        # Header strip (y=0-22 replaces title bar when in capture view)
        self._cap_hdr_bg = Widget(
            box=Box.xywh(0, 0, _W, _CAP_HDR_H),
            bkgnd_color=(0, 0, 0),
            parent=self,
        )
        self._status_led = StatusLed(6, 6, parent=self)
        self._cap_title_lbl = Label(20, 5, self._caption_font, parent=self)
        self._cap_title_lbl.set_text("NAM Capture", _HEADER_FG)
        self._cap_name_lbl = Label(0, 5, self._caption_font, parent=self)
        self._cap_name_lbl.set_text("", _HEADER_NAME_FG)

        self._reel = ReelWidget(
            box=Box.xywh(0, _REEL_Y, _W, _REEL_H),
            total_seconds=self._duration,
            reel_font=self._reel_font,
            caption_font=self._caption_font,
            parent=self,
        )
        self._meter_out = LevelMeter(
            box=Box.xywh(0, _METER_OUT_Y, _W, _METER_H),
            label="OUT",
            default_font=font,
            caption_font=self._caption_font,
            parent=self,
        )
        self._meter_in = LevelMeter(
            box=Box.xywh(0, _METER_IN_Y, _W, _METER_H),
            label="IN",
            default_font=font,
            caption_font=self._caption_font,
            parent=self,
        )

        self._error_lbl = Label(0, _ERR_Y + 2, font, parent=self)

        self._btn_capture_close = Button(
            box=Box.xywh(_BTN_X_CLOSE, _BTN_Y, _BTN_W, _BTN_H),
            text="Close",
            font=font,
            outline_radius=4,
            parent=self,
            action=lambda *_: on_dismiss(),
        )
        self._btn_capture_right = Button(
            box=Box.xywh(_BTN_X_ACTION, _BTN_Y, _BTN_W, _BTN_H),
            text="Abort",
            font=font,
            outline_radius=4,
            parent=self,
            action=lambda *_: self._on_abort(),
        )
        # Full-width "Saved as …" button shown only in DONE state
        self._btn_done = Button(
            box=Box.xywh(_BTN_GAP, _BTN_Y, _W - 2 * _BTN_GAP, _BTN_H),
            text="Saved",
            font=font,
            outline_radius=4,
            parent=self,
            action=lambda *_: on_dismiss(),
        )

        self._capture_group = [
            self._cap_hdr_bg,
            self._status_led,
            self._cap_title_lbl,
            self._cap_name_lbl,
            self._reel,
            self._meter_out,
            self._meter_in,
            self._error_lbl,
            self._btn_capture_close,
            self._btn_capture_right,
            self._btn_done,
        ]

        # Initially hide the whole capture group
        for w in self._capture_group:
            w.hide(refresh=False)

        # Read initial knob values from hardware
        self._refresh_knob_values()

    # ── Engine factory (overridden in tests) ──────────────────────────────────

    def _create_engine(self, output_dir: str | Path, reamp_wav: Path) -> NamCaptureEngine:
        return NamCaptureEngine(output_dir, reamp_wav=reamp_wav)

    # ── Panel lifecycle ───────────────────────────────────────────────────────

    def destroy(self) -> None:
        self._engine.stop()
        super().destroy()

    # ── Input handling ────────────────────────────────────────────────────────

    def handle(self, event: ControllerEvent) -> bool:
        if not isinstance(event, EncoderEvent):
            return False
        cid = getattr(event.controller, "id", None)
        if cid not in (2, 3):
            return False

        state = self._engine.state

        # Swallow enc 2/3 during capture — no level changes mid-recording.
        if state == CaptureState.CAPTURING:
            return True

        # Only on failure: pass through so the vanilla parameter overlay pops
        # up and the user can adjust levels before retrying.
        if state == CaptureState.FAILED:
            return False

        # IDLE: handle locally and update the on-screen knobs.
        # DONE/ABORTED: swallow — the setup view knobs aren't visible.
        if state == CaptureState.IDLE and self._handler is not None:
            steps = int(round(event.rotations * event.multiplier))
            if cid == 3:
                self._handler.system_menu_input_gain(steps)
            else:
                self._handler.system_menu_headphone_volume(steps)
            self._refresh_knob_values()
        return True

    # ── Polling ───────────────────────────────────────────────────────────────

    def tick(self) -> None:
        state = self._engine.state
        if state != self._last_state:
            self._apply_state(state)
            self._last_state = state

        if state == CaptureState.CAPTURING:
            self._reel.set_progress(self._engine.progress())

            now = time.monotonic()
            if now - self._last_level_update >= 0.5:
                self._last_level_update = now
                snap = self._engine.level_snapshot_db()
                if snap is not None:
                    in_db, out_db = snap
                    self._meter_in.set_level(in_db)
                    self._meter_in.set_clip(in_db > -1.0)
                    self._meter_out.set_level(out_db)
                else:
                    self._meter_in.set_level(None)
                    self._meter_out.set_level(None)

            if now - self._last_blink >= 0.5:
                self._last_blink = now
                self._led_on = not self._led_on
                self._status_led.set_on(self._led_on)

    # ── Private ───────────────────────────────────────────────────────────────

    def _on_start(self) -> None:
        if self._engine.state not in (
            CaptureState.IDLE,
            CaptureState.DONE,
            CaptureState.FAILED,
            CaptureState.ABORTED,
        ):
            return
        name = self._name_btn.text or "capture"
        self._update_cap_name_label(name)
        self._engine.start(name)

    def _on_abort(self) -> None:
        if self._engine.state == CaptureState.CAPTURING:
            self._engine.stop()

    def _apply_state(self, state: CaptureState) -> None:
        if state == CaptureState.IDLE:
            if self._in_capture_view:
                self._switch_to_setup_view()
            return

        # All non-IDLE states live in the capture view
        if not self._in_capture_view:
            self._switch_to_capture_view()
        else:
            # Re-entering CAPTURING after DONE/FAILED/ABORTED (Restart/Retry)
            if state == CaptureState.CAPTURING:
                self._reel.reset()
                self._meter_in.set_level(None)
                self._meter_in.set_clip(False)
                self._meter_out.set_level(None)
                self._error_lbl.hide(refresh=False)

        self._configure_for_state(state)

    def _configure_for_state(self, state: CaptureState) -> None:
        """Update LED, buttons, and error label for *state* (capture view already shown)."""
        # LED
        led_colors = {
            CaptureState.CAPTURING: _LED_CAPTURING,
            CaptureState.DONE: _LED_DONE,
            CaptureState.FAILED: _LED_FAILED,
            CaptureState.ABORTED: _LED_ABORTED,
        }
        self._status_led.set_color(led_colors.get(state, _LED_IDLE))
        self._led_on = True

        # Reel
        if state == CaptureState.DONE:
            self._reel.set_done()
        elif state in (CaptureState.FAILED, CaptureState.ABORTED):
            self._reel.freeze()

        # Error label — single short line, centred
        if state == CaptureState.FAILED:
            err = self._engine.error or "Capture failed"
            font = Config().get_font("default")
            self._error_lbl.set_text(err, _ERR_FG, x=_centred_x(err, font, _W))
            self._error_lbl.show(refresh=False)
            if "clip" in err.lower() or "amp" in err.lower():
                self._meter_in.set_clip(True)
        else:
            self._error_lbl.hide(refresh=False)

        # Buttons — rebuild sel list for capture view
        for w in (self._btn_capture_close, self._btn_capture_right, self._btn_done):
            if w in self.sel_list:
                self.del_sel_widget(w)
            w.hide(refresh=False)

        if state == CaptureState.CAPTURING:
            self._btn_capture_right.set_text("Abort")
            self._btn_capture_right.set_action(lambda *_: self._on_abort())
            self._btn_capture_right.show(refresh=False)
            self.add_sel_widget(self._btn_capture_right)

        elif state == CaptureState.DONE:
            path = self._engine.output_path
            name = path.name if path is not None else "capture.wav"
            self._btn_done.set_text(f"Saved as {name}")
            self._btn_done.show(refresh=False)
            self.add_sel_widget(self._btn_done)

        elif state == CaptureState.ABORTED:
            self._btn_capture_right.set_text("Restart")
            self._btn_capture_right.set_action(lambda *_: self._on_start())
            self._btn_capture_close.show(refresh=False)
            self._btn_capture_right.show(refresh=False)
            self.add_sel_widget(self._btn_capture_close)
            self.add_sel_widget(self._btn_capture_right)

        elif state == CaptureState.FAILED:
            self._btn_capture_right.set_text("Retry")
            self._btn_capture_right.set_action(lambda *_: self._on_start())
            self._btn_capture_close.show(refresh=False)
            self._btn_capture_right.show(refresh=False)
            self.add_sel_widget(self._btn_capture_close)
            self.add_sel_widget(self._btn_capture_right)

        self.refresh()

    def _switch_to_capture_view(self) -> None:
        for w in (self._name_btn, self._btn_setup_close, self._btn_start):
            self.del_sel_widget(w)
        for w in self._setup_group:
            w.hide(refresh=False)
        name = self._name_btn.text or "capture"
        self._update_cap_name_label(name)
        for w in self._capture_group:
            if w not in (self._error_lbl, self._btn_done):
                w.show(refresh=False)
        self._in_capture_view = True

    def _switch_to_setup_view(self) -> None:
        for w in (self._btn_capture_close, self._btn_capture_right, self._btn_done):
            if w in self.sel_list:
                self.del_sel_widget(w)
        for w in self._capture_group:
            w.hide(refresh=False)
        self._refresh_knob_values()
        for w in self._setup_group:
            w.show(refresh=False)
        self.add_sel_widget(self._name_btn)
        self.add_sel_widget(self._btn_setup_close)
        self.add_sel_widget(self._btn_start)
        self._in_capture_view = False

    def _update_cap_name_label(self, name: str) -> None:
        tw, _ = get_text_size(name, self._caption_font)
        rx = _W - 4 - tw
        self._cap_name_lbl.set_text(name, _HEADER_NAME_FG, x=rx)

    def _refresh_knob_values(self) -> None:
        if self._handler is None or not hasattr(self._handler, "audiocard"):
            return
        try:
            ac = self._handler.audiocard
            self._knob_gain.set_value(ac.get_volume_parameter(ac.CAPTURE_VOLUME))
            self._knob_vol.set_value(ac.get_volume_parameter(ac.MASTER))
        except Exception:
            pass
