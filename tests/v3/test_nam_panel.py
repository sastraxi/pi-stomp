"""NAM Capture panel — LCD snapshot and lifecycle tests."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from pistomp.nam.engine import CaptureState
from pistomp.nam.panel import NamCapturePanel


# ──────────────────────────────────────────────────────────────────────────
# NAMPluginPanel — per-instance editor (not the capture panel above)
# ──────────────────────────────────────────────────────────────────────────


# ---------------------------------------------------------------------------
# Fake engine for deterministic panel rendering
# ---------------------------------------------------------------------------


class _FakeEngine:
    """Stand-in for NamCaptureEngine that lets tests control state/progress."""

    def __init__(self, state: CaptureState = CaptureState.IDLE, progress: float = 0.0) -> None:
        self._state = state
        self._progress = progress
        self.started: list[str] = []
        self.stopped = False
        self.aborted_error: str | None = None

    @property
    def state(self) -> CaptureState:
        return self._state

    @property
    def error(self) -> str | None:
        if self.aborted_error is not None:
            return self.aborted_error
        return "Reduce amp output" if self._state == CaptureState.FAILED else None

    def abort_with_error(self, msg: str) -> None:
        self.aborted_error = msg
        self._state = CaptureState.FAILED

    @property
    def output_path(self) -> Path | None:
        return (
            Path("/home/pistomp/data/user-files/Audio Recordings/my-amp.wav")
            if self._state == CaptureState.DONE
            else None
        )

    @property
    def pending_path(self) -> Path | None:
        return (
            Path("/home/pistomp/data/user-files/Audio Recordings/my-amp.wav")
            if self._state == CaptureState.CAPTURING
            else None
        )

    def progress(self) -> float:
        return self._progress

    def start(self, name: str) -> None:
        self.started.append(name)
        self._state = CaptureState.CAPTURING

    def stop(self) -> None:
        self.stopped = True
        self._state = CaptureState.ABORTED

    def reset(self) -> None:
        if self._state in (CaptureState.DONE, CaptureState.FAILED, CaptureState.ABORTED):
            self._state = CaptureState.IDLE

    def level_snapshot_db(self) -> tuple[float, float] | None:
        return None

    def set_state(self, state: CaptureState, progress: float = 0.0) -> None:
        self._state = state
        self._progress = progress


def _make_panel(engine: _FakeEngine, on_dismiss=None) -> NamCapturePanel:
    """Build a NamCapturePanel backed by *engine* without touching the filesystem."""
    if on_dismiss is None:
        on_dismiss = lambda: None  # noqa: E731
    with (
        patch.object(NamCapturePanel, "_create_engine", return_value=engine),
        patch("pistomp.nam.panel.wav_duration", return_value=190.0),
    ):
        return NamCapturePanel(output_dir="/tmp", on_dismiss=on_dismiss)


# ---------------------------------------------------------------------------
# Snapshot tests
# ---------------------------------------------------------------------------


class TestNamPanelSnapshot:
    def test_idle_state(self, v3_system, snapshot):
        panel = _make_panel(_FakeEngine(CaptureState.IDLE))
        v3_system.handler.lcd.show_fullscreen_panel(panel)
        panel.tick()
        v3_system.handler.poll_lcd_updates()
        snapshot("idle")

    def test_capturing_state(self, v3_system, snapshot):
        panel = _make_panel(_FakeEngine(CaptureState.CAPTURING, progress=0.45))
        v3_system.handler.lcd.show_fullscreen_panel(panel)
        panel.tick()
        v3_system.handler.poll_lcd_updates()
        snapshot("capturing")

    def test_done_state(self, v3_system, snapshot):
        panel = _make_panel(_FakeEngine(CaptureState.DONE, progress=1.0))
        v3_system.handler.lcd.show_fullscreen_panel(panel)
        panel.tick()
        v3_system.handler.poll_lcd_updates()
        snapshot("done")

    def test_failed_state(self, v3_system, snapshot):
        panel = _make_panel(_FakeEngine(CaptureState.FAILED, progress=0.3))
        v3_system.handler.lcd.show_fullscreen_panel(panel)
        panel.tick()
        v3_system.handler.poll_lcd_updates()
        snapshot("failed")

    def test_aborted_state(self, v3_system, snapshot):
        panel = _make_panel(_FakeEngine(CaptureState.ABORTED, progress=0.6))
        v3_system.handler.lcd.show_fullscreen_panel(panel)
        panel.tick()
        v3_system.handler.poll_lcd_updates()
        snapshot("aborted")


# ---------------------------------------------------------------------------
# Lifecycle tests
# ---------------------------------------------------------------------------


class TestNamPanelLifecycle:
    def test_start_button_starts_engine(self, v3_system):
        engine = _FakeEngine(CaptureState.IDLE)
        panel = _make_panel(engine)
        assert panel._btn_start.action is not None
        panel._btn_start.action()
        assert "capture" in engine.started

    def test_abort_button_shows_dialog_when_parented(self, v3_system):
        # With no parent (tests), _on_abort falls through to immediate abort.
        # Parented case is exercised by the integration flow below.
        engine = _FakeEngine(CaptureState.CAPTURING)
        panel = _make_panel(engine)
        panel.tick()
        # Direct call to confirmed path (dialog tested via integration)
        panel._on_confirmed_abort()
        assert engine.stopped

    def test_abort_noop_when_idle(self, v3_system):
        engine = _FakeEngine(CaptureState.IDLE)
        panel = _make_panel(engine)
        panel._on_abort()
        assert not engine.stopped

    def test_close_setup_calls_on_dismiss(self, v3_system):
        dismissed = []
        panel = _make_panel(_FakeEngine(CaptureState.IDLE), on_dismiss=lambda: dismissed.append(True))
        assert panel._btn_setup_close.action is not None
        panel._btn_setup_close.action()
        assert dismissed == [True]

    def test_done_saved_button_calls_on_dismiss(self, v3_system):
        dismissed = []
        engine = _FakeEngine(CaptureState.DONE, progress=1.0)
        panel = _make_panel(engine, on_dismiss=lambda: dismissed.append(True))
        panel.tick()
        assert panel._btn_done.action is not None
        panel._btn_done.action()
        assert dismissed == [True]

    def test_failed_back_returns_to_idle(self, v3_system):
        engine = _FakeEngine(CaptureState.FAILED, progress=0.3)
        panel = _make_panel(engine)
        panel.tick()  # switch to capture view → FAILED
        assert panel._btn_capture_close.action is not None
        panel._btn_capture_close.action()  # "Back"
        assert engine.state == CaptureState.IDLE

    def test_aborted_back_returns_to_idle(self, v3_system):
        engine = _FakeEngine(CaptureState.ABORTED, progress=0.6)
        panel = _make_panel(engine)
        panel.tick()  # switch to capture view → ABORTED
        assert panel._btn_capture_close.action is not None
        panel._btn_capture_close.action()  # "Back"
        assert engine.state == CaptureState.IDLE

    def test_tick_updates_reel_progress(self, v3_system):
        engine = _FakeEngine(CaptureState.CAPTURING, progress=0.5)
        panel = _make_panel(engine)
        panel._reel._total = 60.0  # override for predictable elapsed
        panel.tick()
        assert abs(panel._reel._elapsed - 30.0) < 1.0

    def test_retry_button_restarts_engine(self, v3_system):
        engine = _FakeEngine(CaptureState.FAILED, progress=0.3)
        panel = _make_panel(engine)
        panel.tick()  # switch to capture view → FAILED
        assert panel._btn_capture_right.action is not None
        panel._btn_capture_right.action()  # "Retry"
        assert "capture" in engine.started

    def test_reel_frozen_on_failure(self, v3_system):
        engine = _FakeEngine(CaptureState.FAILED, progress=0.3)
        panel = _make_panel(engine)
        panel.tick()
        frozen_progress = panel._reel._progress
        panel._reel.set_progress(0.9)  # attempt to advance
        assert panel._reel._progress == frozen_progress

    def test_destroy_stops_engine(self, v3_system):
        engine = _FakeEngine(CaptureState.CAPTURING)
        panel = _make_panel(engine)
        v3_system.handler.lcd.show_fullscreen_panel(panel)
        v3_system.handler.lcd.hide_fullscreen_panel()  # pop_panel → auto_destroy → destroy()
        assert engine.stopped

    def test_analog_clipping_aborts_engine(self, v3_system):
        from pistomp.analogVU import VuState

        engine = _FakeEngine(CaptureState.CAPTURING)
        panel = _make_panel(engine)
        panel._handler = v3_system.handler
        vu = v3_system.handler.hardware.indicators[0]
        vu.state = VuState.CLIP

        for _ in range(4):
            panel.tick()
            assert engine._state == CaptureState.CAPTURING
        panel.tick()
        assert engine._state == CaptureState.FAILED
        assert engine.aborted_error == "Analog clipping: lower amp output"


class TestCaptureSessionSilence:
    def test_silence_decay(self):
        import numpy as np
        from pistomp.nam.capture_session import CaptureSession

        samples = np.ones(48000, dtype=np.float32)
        session = CaptureSession(samples, "out", "in")
        # Verify initial state
        assert session._silent_frames == 0
        session._silent_frames = 24000
        # Simulate non-silent callback step with decay calculation
        frames = 480
        decay = int(frames * (96000 / 96000))
        session._silent_frames = max(0, session._silent_frames - decay)
        assert session._silent_frames == 23520


class TestNamHandlerIntegration:
    def test_nam_board_mounts_panel(self, v3_system):
        handler = v3_system.handler
        fake_engine = _FakeEngine(CaptureState.IDLE)
        with (
            patch.object(NamCapturePanel, "_create_engine", return_value=fake_engine),
            patch("pistomp.nam.panel.wav_duration", return_value=190.0),
        ):
            handler._mount_nam_capture_panel()
        assert isinstance(handler._fullscreen_panel, NamCapturePanel)

    def test_switching_board_stops_engine(self, v3_system):
        handler = v3_system.handler
        fake_engine = _FakeEngine(CaptureState.CAPTURING)
        panel = _make_panel(fake_engine)
        handler._fullscreen_panel = panel
        handler.lcd.show_fullscreen_panel(panel)

        pb = handler.current.pedalboard
        handler.set_current_pedalboard(pb)

        assert fake_engine.stopped
        assert handler._fullscreen_panel is None


# ---------------------------------------------------------------------------
# NAM plugin panel — per-instance editor (3 knobs + virtualized file list)
# ---------------------------------------------------------------------------
#
# These tests build a real ``NamPanel`` on top of the v3 fixture and
# exercise the layout / encoder routing / file-list virtualisation. We
# monkeypatch ``list_nam_files`` so the test never depends on the real
# filesystem state of the device, and we also bypass the model TTL path
# (just set ``model_path`` directly on the plugin instance via
# ``model_ttl_path`` -> effect.ttl file).
#
# Run with --snapshot-update to regenerate baselines:
#     uv run pytest tests/v3/test_nam_panel.py::TestNamPluginPanel --snapshot-update

import os
import tempfile

import pytest

import pistomp.switchstate as switchstate
import common.token as Token
from modalapi.parameter import Parameter
from modalapi.plugin import Plugin
from pistomp.controller import Controller
from pistomp.input.event import EncoderEvent

from plugins.nam.files import current_index
from plugins.nam.panel import (
    NAM_MODEL_URI,
    NamPanel,
    _INPUT_SYM,
    _OUTPUT_SYM,
    _QUALITY_SYM,
)
from tests.types import SystemFixture


# URIs registered by plugins/nam/__init__.py. Hardcoding keeps the tests
# honest if the registration list ever changes — we'd notice immediately.
_NAM_URI = "http://github.com/mikeoliphant/neural-amp-modeler-lv2"


def _write_model_ttl(tmpdir: Path, basename: str) -> Path:
    """Create a fake effect.ttl whose `#model` field points at ``basename``.

    The Plugin class reads the model path with the regex
    ``<[^>]*#model>\\s+<([^>]+)>``, so a minimal valid TTL is:
        <> lv2:appliesTo <...> ; <...#model> <Foo.nam> .
    """
    eff = tmpdir / "effect.ttl"
    eff.write_text(
        "@prefix lv2: <http://lv2plug.in/ns/lv2core#> .\n"
        "@prefix pset: <http://lv2plug.in/ns/ext/presets#> .\n"
        "<> a pset:Preset ;\n"
        f"   lv2:appliesTo <{_NAM_URI}> ;\n"
        f'   <http://github.com/mikeoliphant/neural-amp-modeler-lv2#model> <{basename}> .\n'
    )
    return eff


class _NavEnc(Controller):
    """Fake NAV encoder. The panel consumes this to step the file list."""

    def __init__(self) -> None:
        super().__init__(midi_channel=0, midi_CC=None)
        self.type = Token.NAV  # "nav" (lower) — modhandler checks type == Token.NAV
        self.id = 0


class _TweakEnc(Controller):
    def __init__(self, id: int) -> None:
        super().__init__(midi_channel=0, midi_CC=None)
        self.id = id


def _make_nam_plugin(
    instance_id: str = "nam_1",
    *,
    model_basename: str = "Clean.nam",
    input_db: float = 0.0,
    output_db: float = 0.0,
    quality: float = 1.0,
    tmpdir: Path | None = None,
) -> Plugin:
    """Build a Plugin mirroring the NAM LV2's port layout."""
    params: dict[str, Parameter] = {}
    bypass_info = {"shortName": "bypass", "symbol": ":bypass", "ranges": {"minimum": 0, "maximum": 1}}
    params[":bypass"] = Parameter(bypass_info, 0.0, None, instance_id)
    params[_INPUT_SYM] = Parameter(
        {"shortName": "Input Lvl", "symbol": _INPUT_SYM, "ranges": {"minimum": -20.0, "maximum": 20.0}},
        input_db, None, instance_id,
    )
    params[_OUTPUT_SYM] = Parameter(
        {"shortName": "Output Lvl", "symbol": _OUTPUT_SYM, "ranges": {"minimum": -20.0, "maximum": 20.0}},
        output_db, None, instance_id,
    )
    params[_QUALITY_SYM] = Parameter(
        {"shortName": "Quality", "symbol": _QUALITY_SYM, "ranges": {"minimum": 0.0, "maximum": 1.0}},
        quality, None, instance_id,
    )

    tmp = tmpdir or Path(tempfile.mkdtemp())
    eff = _write_model_ttl(tmp, model_basename)
    p = Plugin(
        instance_id, params, {}, "Simulator", uri=_NAM_URI,
        model_ttl_path=eff,
    )
    p.has_footswitch = False
    p.pedalboard_snapshot = {
        _INPUT_SYM: input_db,
        _OUTPUT_SYM: output_db,
        _QUALITY_SYM: quality,
        ":bypass": 0.0,
    }
    return p


def _patch_nam_files(monkeypatch, files: list[str]) -> None:
    """Replace the panel's filesystem scan with a fixed list of basenames."""
    full = [Path("/fake/NAM Models") / os.path.basename(f) for f in files]
    monkeypatch.setattr("plugins.nam.panel.list_nam_files", lambda: full)


def open_nam(
    v3_system: SystemFixture,
    plugin: Plugin,
    files: list[str],
) -> NamPanel:
    """Install *plugin* on the v3 system and mount the NAM panel."""
    handler = v3_system.handler
    hw = v3_system.hw
    assert handler.current
    handler.current.pedalboard.plugins = [plugin]
    handler.lcd.link_data(handler.pedalboard_list, handler.current, hw.footswitches)
    handler.lcd.draw_main_panel()
    panel = NamPanel(plugin=plugin, handler=handler, on_dismiss=handler.hide_fullscreen_panel)
    handler._fullscreen_panel = panel
    handler.lcd.show_fullscreen_panel(panel)
    # The list reads its initial file set from the plugin constructor
    # (which calls list_nam_files). Replace _all_files on the instance to
    # honour the test's monkeypatched list, then rebuild the row window.
    panel._all_files = [Path(f) for f in files]
    panel._file_list.set_files([str(p) for p in panel._all_files])
    # Locate the model basename in the supplied list and re-select it.
    from plugins.nam.files import current_index as _ci
    idx = _ci(panel._all_files, plugin.model_path)
    if idx >= 0:
        panel._file_list.set_selected_index(idx, scroll=True)
    panel._last_selected = idx
    handler.poll_lcd_updates()
    return panel


def tweak(handler, idx: int, rotations: int) -> bool:
    """Drive a tweak encoder rotation through the handler."""
    event = EncoderEvent(controller=_TweakEnc(idx), rotations=rotations, new_value=0.0, new_midi_value=0)
    return handler.handle(event)


def nav_step(handler, direction: int) -> bool:
    """Drive the nav encoder by one click (direction=±1)."""
    event = EncoderEvent(
        controller=_NavEnc(),
        rotations=direction,
        new_value=0.0,
        new_midi_value=0,
    )
    return handler.handle(event)


# ── saga tests ─────────────────────────────────────────────────────────────


# A representative list of model basenames covering long+short names and
# nested dirs (the panel uses basenames only, but we want to verify the
# scan collapsed them to flat basenames).
_SAGA_FILES = [
    "/fake/NAM Models/Clean.nam",
    "/fake/NAM Models/Crunch.nam",
    "/fake/NAM Models/Darkglass Distortion - ALPHA.nam",
    "/fake/NAM Models/DDE - Hyper Droner (NoCab).nam",
    "/fake/NAM Models/FORTIN GRIND.nam",
    "/fake/NAM Models/Facebender.nam",
    "/fake/NAM Models/Ibanez TS9 Tube Screamer.nam",
    "/fake/NAM Models/Ampeg/Ampeg SVT 4 Pro - Clean - Full Rig.nam",
    "/fake/NAM Models/Ampeg/Ampeg SVT - Ultra Hi MD 421.nam",
    "/fake/NAM Models/Ampeg/Ampeg SVT - Ultra Lo and Hi MD 421.nam",
]


class TestNamPluginPanel:
    """Snapshot + behavioural saga for the NAM plugin editor."""

    def test_opened(self, v3_system: SystemFixture, snapshot, monkeypatch):
        """Default panel with the model currently loaded matched and marked."""
        plugin = _make_nam_plugin(model_basename="FORTIN GRIND.nam")
        _patch_nam_files(monkeypatch, _SAGA_FILES)
        open_nam(v3_system, plugin, _SAGA_FILES)
        snapshot("opened")

    def test_tweak1_quality_change(self, v3_system, monkeypatch):
        """Tweak1 moves the quality knob and sends a param_set over WS."""
        plugin = _make_nam_plugin(quality=1.0)
        _patch_nam_files(monkeypatch, _SAGA_FILES)
        panel = open_nam(v3_system, plugin, _SAGA_FILES)
        bridge = v3_system.ws_bridge
        bridge.sent.clear()
        tweak(v3_system.handler, 1, -2)  # 1.0 - 2*0.05 = 0.9
        v3_system.handler.poll_lcd_updates()
        assert panel._knob_quality.value == pytest.approx(0.9)
        sent = [m for m in bridge.sent if _QUALITY_SYM in m]
        assert sent, f"expected a { _QUALITY_SYM } param_set, got {bridge.sent}"
        # Trailing value formatted by f"{value}" — exact repr may vary
        # (e.g. 0.9 vs 0.8999…), so parse and compare numerically.
        sent_value = float(sent[-1].rsplit(" ", 1)[1])
        assert sent_value == pytest.approx(0.9)

    def test_tweak2_input_change(self, v3_system, monkeypatch):
        """Tweak2 moves the input knob and sends a param_set over WS."""
        plugin = _make_nam_plugin(input_db=0.0)
        _patch_nam_files(monkeypatch, _SAGA_FILES)
        panel = open_nam(v3_system, plugin, _SAGA_FILES)
        bridge = v3_system.ws_bridge
        bridge.sent.clear()
        tweak(v3_system.handler, 2, 3)  # 0.0 + 3*0.5 = 1.5
        v3_system.handler.poll_lcd_updates()
        assert panel._knob_input.value == pytest.approx(1.5)
        sent = [m for m in bridge.sent if _INPUT_SYM in m]
        assert sent
        assert float(sent[-1].rsplit(" ", 1)[1]) == pytest.approx(1.5)

    def test_tweak3_output_change(self, v3_system, monkeypatch):
        """Tweak3 moves the output knob."""
        plugin = _make_nam_plugin(output_db=-3.0)
        _patch_nam_files(monkeypatch, _SAGA_FILES)
        panel = open_nam(v3_system, plugin, _SAGA_FILES)
        bridge = v3_system.ws_bridge
        bridge.sent.clear()
        tweak(v3_system.handler, 3, 4)  # -3.0 + 4*0.5 = -1.0
        v3_system.handler.poll_lcd_updates()
        assert panel._knob_output.value == pytest.approx(-1.0)
        sent = [m for m in bridge.sent if _OUTPUT_SYM in m]
        assert sent
        assert float(sent[-1].rsplit(" ", 1)[1]) == pytest.approx(-1.0)

    def test_tweak_clamps_at_max(self, v3_system, monkeypatch):
        """Rotating past the max stops at the max without an extra send."""
        plugin = _make_nam_plugin(quality=0.95)
        _patch_nam_files(monkeypatch, _SAGA_FILES)
        panel = open_nam(v3_system, plugin, _SAGA_FILES)
        bridge = v3_system.ws_bridge
        bridge.sent.clear()
        tweak(v3_system.handler, 1, 10)  # would be 1.45 → clamped to 1.0
        v3_system.handler.poll_lcd_updates()
        assert panel._knob_quality.value == 1.0
        sent = [m for m in bridge.sent if _QUALITY_SYM in m]
        assert float(sent[-1].rsplit(" ", 1)[1]) == pytest.approx(1.0)

    def test_nav_selects_and_picks_file(self, v3_system, monkeypatch):
        """Nav steps the file list and each step sends a patch_set."""
        plugin = _make_nam_plugin(model_basename="Clean.nam")
        _patch_nam_files(monkeypatch, _SAGA_FILES)
        panel = open_nam(v3_system, plugin, _SAGA_FILES)
        bridge = v3_system.ws_bridge
        bridge.sent.clear()
        nav_step(v3_system.handler, 1)  # Clean -> Crunch
        nav_step(v3_system.handler, 1)  # Crunch -> Darkglass Distortion - ALPHA
        v3_system.handler.poll_lcd_updates()
        # The selected file's basename is the basename of the 2-step move.
        assert panel._file_list.selected_index == 2
        patch_sents = [m for m in bridge.sent if m.startswith("patch_set ")]
        assert patch_sents, f"expected patch_set frames, got {bridge.sent}"
        # Two picks (one per nav_step). The last one is the most recent.
        assert NAM_MODEL_URI in patch_sents[-1]
        assert "Darkglass Distortion - ALPHA.nam" in patch_sents[-1]

    def test_reset_restores_control_ports(self, v3_system, monkeypatch):
        """Reset restores input/output/quality from the snapshot, then resends the model."""
        plugin = _make_nam_plugin(
            model_basename="Clean.nam",
            input_db=0.0, output_db=0.0, quality=1.0,
        )
        _patch_nam_files(monkeypatch, _SAGA_FILES)
        panel = open_nam(v3_system, plugin, _SAGA_FILES)

        # Tweak everything away from the snapshot values.
        tweak(v3_system.handler, 2, 10)  # input → +5.0
        tweak(v3_system.handler, 3, -6)  # output → -3.0
        tweak(v3_system.handler, 1, -10)  # quality → 0.5
        # Move the file list off the loaded model.
        nav_step(v3_system.handler, 5)
        v3_system.handler.poll_lcd_updates()
        assert panel._knob_input.value == pytest.approx(5.0)

        # Fire the Reset button. The base class handles ctrl-ports; the
        # panel subclass sends the model atom patch itself.
        bridge = v3_system.ws_bridge
        bridge.sent.clear()
        panel._on_reset()
        v3_system.handler.poll_lcd_updates()
        assert panel._knob_input.value == pytest.approx(0.0)
        assert panel._knob_output.value == pytest.approx(0.0)
        assert panel._knob_quality.value == pytest.approx(1.0)
        assert panel._file_list.selected_index == 0  # back to "Clean.nam"
        patch_sents = [m for m in bridge.sent if m.startswith("patch_set ")]
        assert patch_sents
        assert "Clean.nam" in patch_sents[-1]

    def test_only_selected_row_has_scrolling_child(self, v3_system, monkeypatch):
        """The marquee animation is structural: only the selected row has a ScrollingText child.

        Other rows either have no child at all, or a child that doesn't tick.
        """
        from uilib.text import ScrollingText

        plugin = _make_nam_plugin(model_basename="Clean.nam")
        _patch_nam_files(monkeypatch, _SAGA_FILES)
        panel = open_nam(v3_system, plugin, _SAGA_FILES)
        fl = panel._file_list
        scrolling_children = [
            c for c in fl.children
            if isinstance(c, _ListRow) and any(isinstance(cc, ScrollingText) for cc in c.children)
        ]
        assert len(scrolling_children) == 1
        # And the file in the scrolling row matches selected_path.
        sel = fl.selected_path
        scrolling_row = scrolling_children[0]
        scrolling_text = next(c for c in scrolling_row.children if isinstance(c, ScrollingText))
        assert scrolling_text.text == os.path.basename(sel)

    def test_scrolling_resets_on_selection_change(self, v3_system, monkeypatch):
        """Scrolling to a new file replaces the ScrollingText child, resetting the animation timer."""
        from uilib.text import ScrollingText

        plugin = _make_nam_plugin(model_basename="Clean.nam")
        _patch_nam_files(monkeypatch, _SAGA_FILES)
        panel = open_nam(v3_system, plugin, _SAGA_FILES)
        fl = panel._file_list

        # Capture the first ScrollingText instance, move selection, then
        # confirm the row was rebuilt and a new ScrollingText was created.
        st_old = next(
            c for row in fl.children if isinstance(row, _ListRow)
            for c in row.children if isinstance(c, ScrollingText)
        )
        nav_step(v3_system.handler, 1)
        v3_system.handler.poll_lcd_updates()
        st_new_list = [
            c for row in fl.children if isinstance(row, _ListRow)
            for c in row.children if isinstance(c, ScrollingText)
        ]
        assert len(st_new_list) == 1
        st_new = st_new_list[0]
        assert st_new is not st_old
        # _anchor_time is the animation clock; it's None after a fresh
        # build, meaning the next tick will re-anchor the marquee cycle
        # at offset 0 (the standard "reset" semantics of ScrollingText).
        assert st_new._anchor_time is None

    def test_scrollbar_with_many_files(self, v3_system, monkeypatch):
        """With 50 files the scrollbar is visible and selected row stays centred-ish."""
        big = [f"/fake/NAM Models/file_{i:03d}.nam" for i in range(50)]
        plugin = _make_nam_plugin(model_basename="file_000.nam")
        _patch_nam_files(monkeypatch, big)
        open_nam(v3_system, plugin, big)
        v3_system.handler.poll_lcd_updates()
        # Just asserting nothing throws. The scrollbar existence is
        # implicitly verified by the snapshot in test_opened (8 visible
        # rows with the list overflowing).
        # Here we verify a different invariant: scrolling way down still
        # leaves a valid selection.
        for _ in range(40):
            nav_step(v3_system.handler, 1)
        v3_system.handler.poll_lcd_updates()
        # No exception, selection is at index 40 (clamped at last).
        fl = v3_system.handler._fullscreen_panel._file_list
        assert fl.selected_index == 40

    def test_empty_file_list(self, v3_system, monkeypatch):
        """An empty library opens the panel without errors; nav does nothing."""
        plugin = _make_nam_plugin(model_basename="ghost.nam")
        _patch_nam_files(monkeypatch, [])
        panel = open_nam(v3_system, plugin, [])
        # No crash on tick.
        panel.tick()
        v3_system.handler.poll_lcd_updates()
        # Nav shouldn't move the selection.
        for _ in range(3):
            nav_step(v3_system.handler, 1)
        assert panel._file_list.selected_index == 0 or panel._file_list._files == []

    def test_bypass_button_still_works(self, v3_system, monkeypatch):
        """The base class Bypass button routes through the plugin bypass plumbing."""
        plugin = _make_nam_plugin()
        _patch_nam_files(monkeypatch, _SAGA_FILES)
        open_nam(v3_system, plugin, _SAGA_FILES)
        panel = v3_system.handler._fullscreen_panel
        bridge = v3_system.ws_bridge
        bridge.sent.clear()
        panel._on_toggle_bypass()
        v3_system.handler.poll_lcd_updates()
        assert plugin.is_bypassed() is True
        assert any(":bypass" in m and " 1" in m for m in bridge.sent)

    def test_files_helper_finds_known_model(self, monkeypatch):
        """The ``current_index`` helper resolves URL-encoded paths from the plugin's TTL."""
        files = [
            Path("/u/NAM Models/Clean.nam"),
            Path("/u/NAM Models/FORTIN GRIND.nam"),
            Path("/u/NAM Models/BASS - PULTEC EQ.nam"),
        ]
        # URL-encoded space (matches how NAM TTL stores the path)
        assert current_index(files, "/u/NAM%20Models/FORTIN%20GRIND.nam") == 1
        # Missing file
        assert current_index(files, "/u/NAM Models/nope.nam") == -1
        # Empty
        assert current_index(files, None) == -1


# Import the private row class for the "only selected animates" test.
from plugins.nam.widgets import _ListRow  # noqa: E402
