"""Integration tests for blend mode — v3 only (requires EncoderMidiControl)."""

import json
import os

from tests.types import SystemFixture


def _blend_encoder(hw):
    """Return the EncoderMidiControl with id=1 used by the blend fixture."""
    from pistomp.encodermidicontrol import EncoderMidiControl
    return next(e for e in hw.encoders if isinstance(e, EncoderMidiControl) and getattr(e, "id", None) == 1)


# ---------------------------------------------------------------------------
# Preparation
# ---------------------------------------------------------------------------


def test_blend_prepare_creates_segment_diff_map(blend_system: SystemFixture):
    handler, *_ = blend_system

    assert "Blend" in handler.blend_modes
    blend_mode = handler.blend_modes["Blend"]

    # Two stops → one segment → one diff map
    assert len(blend_mode.stops) == 2
    assert len(blend_mode.segment_diff_maps) == 1

    diff = blend_mode.segment_diff_maps[0]
    assert "/BigMuff" in diff
    assert "Tone" in diff["/BigMuff"]
    assert "Level" in diff["/BigMuff"]
    # :bypass is identical across stops, so it must NOT be in the diff map
    assert ":bypass" not in diff.get("/BigMuff", {})


def test_blend_auto_activates_on_blend_snapshot(blend_system: SystemFixture):
    handler, hw, *_ = blend_system

    assert handler.active_blend_mode is not None
    assert handler.active_blend_mode.config.get("name") == "Blend"

    enc = _blend_encoder(hw)
    assert enc.value_change_callback is not None


# ---------------------------------------------------------------------------
# Parameter sending
# ---------------------------------------------------------------------------


def test_blend_activate_sends_initial_params(blend_system: SystemFixture):
    """Re-activate after a manual deactivate to check what sync_current_position sends."""
    handler, hw, *_ = blend_system
    blend_mode = handler.active_blend_mode

    # Deactivate, reset tracking, and re-activate from position 0
    enc = _blend_encoder(hw)
    enc.midi_value = 0
    blend_mode.deactivate()
    handler.ws_bridge.sent.clear()
    blend_mode.activate()

    # At position 0 all differing params should equal the Clean stop values
    tone_values = handler.ws_bridge.sent_values_for("/BigMuff", "Tone")
    level_values = handler.ws_bridge.sent_values_for("/BigMuff", "Level")
    assert tone_values and abs(tone_values[-1] - 0.2) < 1e-6
    assert level_values and abs(level_values[-1] - 0.5) < 1e-6

    # :bypass is constant (0.0) — sent via the constant path in sync_current_position
    bypass_values = handler.ws_bridge.sent_values_for("/BigMuff", ":bypass")
    assert bypass_values and abs(bypass_values[-1] - 0.0) < 1e-6


def test_blend_full_sweep_reaches_lead_stop(blend_system: SystemFixture):
    """handle_value_change at midi_value=127 (100%) should send Lead stop values."""
    handler, hw, *_ = blend_system
    enc = _blend_encoder(hw)
    enc.midi_value = 127

    handler.active_blend_mode.input_controller.handle_value_change(127, enc)

    tone_values = handler.ws_bridge.sent_values_for("/BigMuff", "Tone")
    level_values = handler.ws_bridge.sent_values_for("/BigMuff", "Level")
    assert tone_values and abs(tone_values[-1] - 0.8) < 1e-6
    assert level_values and abs(level_values[-1] - 0.9) < 1e-6


def test_blend_dedup_suppresses_redundant_messages(blend_system: SystemFixture):
    """Two consecutive identical positions should produce only one WS message per param."""
    handler, hw, *_ = blend_system
    enc = _blend_encoder(hw)
    enc.midi_value = 64

    ic = handler.active_blend_mode.input_controller
    ic.handle_value_change(64, enc)
    sent_after_first = len(handler.ws_bridge.sent)
    assert sent_after_first > 0

    # Second call at the same position — nothing new should be queued
    ic.handle_value_change(64, enc)
    assert len(handler.ws_bridge.sent) == sent_after_first


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


def test_blend_deactivate_detaches_encoder(blend_system: SystemFixture):
    handler, hw, *_ = blend_system
    enc = _blend_encoder(hw)
    blend_mode = handler.active_blend_mode

    blend_mode.deactivate()

    assert enc.value_change_callback is None
    assert blend_mode.input_controller.controlled_input is None


def test_pedalboard_switch_clears_blend_modes(blend_system: SystemFixture):
    handler, hw, lcd, mock_get, mock_post = blend_system

    other_pb = handler.pedalboards["/path/to/new.pedalboard"]
    other_pb.plugins = []

    # Switching to a pedalboard with no blend config should wipe all blend state
    handler.set_current_pedalboard(other_pb)

    assert handler.blend_modes == {}
    assert handler.active_blend_mode is None
    enc = _blend_encoder(hw)
    assert enc.value_change_callback is None


# ---------------------------------------------------------------------------
# WebSocket-driven snapshot changes
# ---------------------------------------------------------------------------


def test_ws_pedal_snapshot_deactivates_blend(blend_system: SystemFixture):
    handler, hw, *_ = blend_system
    enc = _blend_encoder(hw)

    # Inject a switch to a non-blend snapshot
    handler.ws_bridge.inject("pedal_snapshot 0 Clean")
    handler.poll_modui_changes()

    assert handler.active_blend_mode is None
    assert enc.value_change_callback is None


def test_ws_pedal_snapshot_activates_blend(blend_system: SystemFixture):
    handler, hw, *_ = blend_system

    # Manually deactivate so we can re-activate via WebSocket
    handler.active_blend_mode.deactivate()
    handler.active_blend_mode = None

    handler.ws_bridge.inject("pedal_snapshot 2 Blend")
    handler.poll_modui_changes()

    assert handler.active_blend_mode is not None
    assert handler.active_blend_mode.config.get("name") == "Blend"
    enc = _blend_encoder(hw)
    assert enc.value_change_callback is not None


# ---------------------------------------------------------------------------
# File watching
# ---------------------------------------------------------------------------


def test_snapshots_file_change_triggers_reprepare(blend_system: SystemFixture):
    handler, *_ = blend_system
    blend_mode = handler.active_blend_mode
    snapshots_path = blend_mode.snapshots_monitor.path

    # Write updated stop values and advance mtime so the monitor detects a change
    updated = json.loads(open(snapshots_path).read())
    updated["snapshots"][1]["data"]["BigMuff"]["ports"]["Tone"] = 0.95
    open(snapshots_path, "w").write(json.dumps(updated))
    future = os.path.getmtime(snapshots_path) + 1
    os.utime(snapshots_path, (future, future))

    handler.poll_modui_changes()

    # blend mode re-prepares in-place — same object, updated diff maps
    assert "Blend" in handler.blend_modes
    diff = handler.blend_modes["Blend"].segment_diff_maps[0]
    assert abs(diff["/BigMuff"]["Tone"].val_b - 0.95) < 1e-6
