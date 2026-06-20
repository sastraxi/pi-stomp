"""NAM Capture panel — LCD snapshot and lifecycle tests."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from pistomp.nam.engine import CaptureState, NamCaptureEngine
from pistomp.nam.panel import NamCapturePanel


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

    @property
    def state(self) -> CaptureState:
        return self._state

    @property
    def error(self) -> str | None:
        return "Oops" if self._state == CaptureState.FAILED else None

    @property
    def output_path(self) -> Path | None:
        return (
            Path("/home/pistomp/data/user-files/Audio Recordings/my-amp.wav")
            if self._state == CaptureState.DONE
            else None
        )

    def progress(self) -> float:
        return self._progress

    def start(self, name: str) -> None:
        self.started.append(name)

    def stop(self) -> None:
        self.stopped = True

    def set_state(self, state: CaptureState, progress: float = 0.0) -> None:
        self._state = state
        self._progress = progress


# ---------------------------------------------------------------------------
# Snapshot tests
# ---------------------------------------------------------------------------


class TestNamPanelSnapshot:
    def test_idle_state(self, v3_system, snapshot):
        engine = _FakeEngine(CaptureState.IDLE)
        panel = NamCapturePanel(engine=engine, on_dismiss=lambda: None)
        v3_system.handler.lcd.show_plugin_panel(panel)
        panel.tick()
        v3_system.handler.poll_lcd_updates()
        snapshot("idle")

    def test_capturing_state(self, v3_system, snapshot):
        engine = _FakeEngine(CaptureState.CAPTURING, progress=0.45)
        panel = NamCapturePanel(engine=engine, on_dismiss=lambda: None)
        v3_system.handler.lcd.show_plugin_panel(panel)
        panel.tick()
        v3_system.handler.poll_lcd_updates()
        snapshot("capturing")

    def test_done_state(self, v3_system, snapshot):
        engine = _FakeEngine(CaptureState.DONE, progress=1.0)
        panel = NamCapturePanel(engine=engine, on_dismiss=lambda: None)
        v3_system.handler.lcd.show_plugin_panel(panel)
        panel.tick()
        v3_system.handler.poll_lcd_updates()
        snapshot("done")

    def test_failed_state(self, v3_system, snapshot):
        engine = _FakeEngine(CaptureState.FAILED)
        panel = NamCapturePanel(engine=engine, on_dismiss=lambda: None)
        v3_system.handler.lcd.show_plugin_panel(panel)
        panel.tick()
        v3_system.handler.poll_lcd_updates()
        snapshot("failed")


# ---------------------------------------------------------------------------
# Lifecycle tests
# ---------------------------------------------------------------------------


class TestNamPanelLifecycle:
    def test_start_button_triggers_engine(self, v3_system):
        engine = _FakeEngine(CaptureState.IDLE)
        panel = NamCapturePanel(engine=engine, on_dismiss=lambda: None)
        panel._btn_start.action()
        assert engine.started == ["capture"]

    def test_abort_button_stops_engine_when_capturing(self, v3_system):
        engine = _FakeEngine(CaptureState.CAPTURING)
        panel = NamCapturePanel(engine=engine, on_dismiss=lambda: None)
        panel._btn_abort.action()
        assert engine.stopped

    def test_abort_noop_when_idle(self, v3_system):
        engine = _FakeEngine(CaptureState.IDLE)
        panel = NamCapturePanel(engine=engine, on_dismiss=lambda: None)
        panel._btn_abort.action()
        assert not engine.stopped

    def test_dismiss_button_calls_on_dismiss(self, v3_system):
        dismissed = []
        engine = _FakeEngine(CaptureState.IDLE)
        panel = NamCapturePanel(engine=engine, on_dismiss=lambda: dismissed.append(True))
        panel._btn_dismiss.action()
        assert dismissed == [True]

    def test_tick_updates_progress_label(self, v3_system):
        engine = _FakeEngine(CaptureState.CAPTURING, progress=0.0)
        panel = NamCapturePanel(engine=engine, on_dismiss=lambda: None)
        # Progress changes from 0 → 0.5 between ticks
        engine.set_state(CaptureState.CAPTURING, progress=0.5)
        panel.tick()
        assert abs(panel._progress_bar.progress - 0.5) < 0.01


class TestNamHandlerIntegration:
    def test_nam_board_mounts_panel(self, v3_system):
        handler = v3_system.handler
        # Simulate selecting the NAM Capture pedalboard by calling the private mount.
        with (
            patch("pistomp.nam.engine.NamCaptureEngine.__init__", return_value=None),
            patch.object(
                NamCaptureEngine,
                "state",
                new_callable=lambda: property(lambda self: CaptureState.IDLE),
            ),
            patch.object(NamCaptureEngine, "progress", return_value=0.0),
            patch.object(NamCaptureEngine, "error", new_callable=lambda: property(lambda self: None)),
            patch.object(NamCaptureEngine, "output_path", new_callable=lambda: property(lambda self: None)),
        ):
            handler._mount_nam_capture_panel()

        assert handler._nam_engine is not None
        assert isinstance(handler._fullscreen_panel, NamCapturePanel)

    def test_switching_board_stops_engine(self, v3_system):
        handler = v3_system.handler
        fake_engine = _FakeEngine(CaptureState.CAPTURING)
        handler._nam_engine = fake_engine

        # Trigger the cleanup logic at the top of set_current_pedalboard by
        # reloading the current pedalboard (same board is fine for this test).
        pb = handler.current.pedalboard
        handler.set_current_pedalboard(pb)

        assert fake_engine.stopped
        assert handler._nam_engine is None
