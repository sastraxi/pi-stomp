"""Tap-tempo LCD takeover and the transient BPM toast."""

import time
from unittest.mock import MagicMock

import pistomp.switchstate as switchstate
from tests.types import SystemFixture


def _mock_bpm(mock_get, bpm: str):
    def get_side_effect(url, **kwargs):
        resp = MagicMock()
        resp.status_code = 200
        resp.text = bpm if "get_bpm" in url else "{}"
        return resp

    mock_get.side_effect = get_side_effect


def _tap_footswitch(hw):
    return next(fs for fs in hw.footswitches if fs.taptempo is not None)


def test_tap_mode_takeover(v3_system: SystemFixture, snapshot):
    """Long-press toggles tap mode: footswitch D slot restyles to TAP + BPM,
    and restores (without a stale BPM label) when toggled back off."""
    handler = v3_system.handler
    _mock_bpm(v3_system.mock_get, "100.0")

    snapshot("default")
    handler.toggle_tap_tempo_enable()
    snapshot("tap-mode")
    handler.toggle_tap_tempo_enable()
    snapshot("default")


def test_taps_update_bpm_and_send_ws(v3_system: SystemFixture, snapshot):
    """Taps recompute the BPM, send transport-bpm over WS, and redraw the digits."""
    handler = v3_system.handler
    hw = v3_system.hw
    ws_bridge = v3_system.ws_bridge
    _mock_bpm(v3_system.mock_get, "100.0")

    handler.toggle_tap_tempo_enable()
    fs = _tap_footswitch(hw)
    base = time.monotonic()
    for i in range(4):
        fs._on_switch(switchstate.Value.RELEASED, base + i * 0.5)  # 120 BPM

    assert any(m.startswith("transport-bpm 120") for m in ws_bridge.sent)
    assert fs.get_display_label() == "120"
    snapshot("tap-120")


def test_transport_message_updates_tap_widget(v3_system: SystemFixture, snapshot):
    """While tap mode is active, an inbound transport message redraws the digits
    (mod-ui owns transport state) and shows no toast."""
    handler = v3_system.handler
    lcd = handler.lcd
    ws_bridge = v3_system.ws_bridge
    _mock_bpm(v3_system.mock_get, "100.0")

    handler.toggle_tap_tempo_enable()
    ws_bridge.inject("transport 1 4.0 132.0 0")
    handler.poll_ws_messages()

    assert lcd.w_bpm_toast is None
    snapshot("tap-132")


def test_bpm_toast(v3_system: SystemFixture, snapshot):
    """Outside tap mode, an inbound transport message shows a transient BPM
    toast which auto-dismisses, restoring the screen."""
    handler = v3_system.handler
    lcd = handler.lcd
    ws_bridge = v3_system.ws_bridge

    snapshot("default")
    ws_bridge.inject("transport 1 4.0 132.0 0")
    handler.poll_ws_messages()
    assert lcd.w_bpm_toast is not None
    snapshot("toast")

    lcd._bpm_toast_expiry = time.monotonic() - 1
    lcd.poll_updates()
    assert lcd.w_bpm_toast is None
    snapshot("default")


def test_bpm_toast_skipped_when_unchanged(v3_system: SystemFixture):
    """A transport message repeating the known BPM does not toast."""
    handler = v3_system.handler
    lcd = handler.lcd
    ws_bridge = v3_system.ws_bridge

    handler.hardware.taptempo.set_bpm(132.0)
    ws_bridge.inject("transport 1 4.0 132.0 0")
    handler.poll_ws_messages()
    assert lcd.w_bpm_toast is None
