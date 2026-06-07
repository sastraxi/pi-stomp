"""Connections area — left-aligned status pills in the top row."""

from unittest.mock import MagicMock

from tests.types import SystemFixture


def _enable_midi_device(handler):
    """Make has_present_device() report a reachable external MIDI device."""
    em = handler.external_midi
    em.enabled = True
    em.midi_ports["c4"] = MagicMock()
    return em


def test_midi_pill_hidden_without_device(v3_system: SystemFixture, snapshot):
    """No configured/reachable device → no MIDI pill (and no snapshot drift)."""
    v3_system.handler.poll_lcd_updates()
    assert "MIDI" not in v3_system.handler.lcd.w_connections
    snapshot()


def test_midi_pill_shown_when_device_present(v3_system: SystemFixture, snapshot):
    handler = v3_system.handler
    _enable_midi_device(handler)

    handler.poll_lcd_updates()

    assert "MIDI" in handler.lcd.w_connections
    assert handler.lcd.w_connections["MIDI"].selectable
    snapshot()


def test_midi_pill_clears_when_device_removed(v3_system: SystemFixture):
    handler = v3_system.handler
    em = _enable_midi_device(handler)
    handler.poll_lcd_updates()
    assert "MIDI" in handler.lcd.w_connections

    em.midi_ports.clear()
    handler.poll_lcd_updates()
    assert "MIDI" not in handler.lcd.w_connections


def test_midi_pill_flashes_on_traffic(v3_system: SystemFixture, snapshot, monkeypatch):
    import pistomp.lcd320x240 as lcdmod

    clock = {"t": 1000.0}
    monkeypatch.setattr(lcdmod.time, "monotonic", lambda: clock["t"])

    handler = v3_system.handler
    em = _enable_midi_device(handler)
    handler.poll_lcd_updates()
    snapshot("idle")

    em.traffic_count += 1
    handler.poll_lcd_updates()
    snapshot("flash")

    # flash holds for its wall-clock window regardless of how many polls land in it
    for _ in range(3):
        handler.poll_lcd_updates()
    snapshot("flash")

    # once the window elapses, it reverts to idle
    clock["t"] += lcdmod.Lcd._CONNECTION_FLASH_SEC
    handler.poll_lcd_updates()
    snapshot("idle")


def test_midi_pill_action_is_noop(v3_system: SystemFixture):
    from uilib.misc import InputEvent

    handler = v3_system.handler
    _enable_midi_device(handler)
    handler.poll_lcd_updates()

    handler.lcd.w_connections["MIDI"].input_event(InputEvent.CLICK)  # must not raise
