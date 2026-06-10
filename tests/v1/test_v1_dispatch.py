"""v1 handler dispatch — Mod.handle() routes events to the correct state machine."""

from pistomp.input.event import AnalogEvent, EncoderEvent, SwitchEvent, SwitchEventKind
from pistomp.input.sink import InputSink


class RecordingSink(InputSink):
    def __init__(self):
        self.events = []

    def handle(self, event):
        self.events.append(event)
        return True


def test_v1_nav_encoder_top(v1_system):
    """Top encoder (id=0) rotation routes to top_encoder_select."""
    handler = v1_system.handler
    hw = v1_system.hw
    for enc in hw.encoders:
        enc.sink = RecordingSink()
    enc = hw.encoders[0]
    assert enc.id == 0

    handler.handle(EncoderEvent(controller=enc, rotations=3, new_value=0, new_midi_value=0))

    # State machine should have moved to PEDALBOARD_SELECTED
    assert handler.top_encoder_mode.name == "PEDALBOARD_SELECTED"


def test_v1_nav_encoder_bottom(v1_system):
    """Bottom encoder (id=1) rotation routes to bot_encoder_select."""
    handler = v1_system.handler
    hw = v1_system.hw
    for enc in hw.encoders:
        enc.sink = RecordingSink()
    enc = hw.encoders[1]
    assert enc.id == 1

    handler.handle(EncoderEvent(controller=enc, rotations=2, new_value=0, new_midi_value=0))

    assert handler.bot_encoder_mode.name == "VALUE_EDIT"


def test_v1_analog_event_emits_midi(v1_system):
    """AnalogEvent is forwarded to _emit_midi (midiout is asserted in hardware tests)."""
    handler = v1_system.handler
    hw = v1_system.hw
    # Find the expression control (has a MIDI CC)
    ctrl = next(c for c in hw.analog_controls if getattr(c, "midi_CC", None) is not None)
    ctrl.sink = handler
    hw.midiout.reset_mock()

    handler.handle(AnalogEvent(controller=ctrl, raw_value=512, midi_value=64))

    hw.midiout.send_message.assert_called_once()
    cc = hw.midiout.send_message.call_args[0][0]
    assert cc[2] == 64


def test_v1_encoder_button_top(v1_system):
    """Top encoder short-press routes to top_encoder_sw."""
    handler = v1_system.handler
    hw = v1_system.hw
    for enc in hw.encoders:
        enc.sink = RecordingSink()
    enc = hw.encoders[0]

    handler.handle(SwitchEvent(controller=enc, kind=SwitchEventKind.PRESS, timestamp=1.0))

    assert handler.top_encoder_mode.name == "PRESET_SELECT"


def test_v1_encoder_button_bottom(v1_system):
    """Bottom encoder short-press routes to bottom_encoder_sw."""
    handler = v1_system.handler
    hw = v1_system.hw
    for enc in hw.encoders:
        enc.sink = RecordingSink()
    enc = hw.encoders[1]

    handler.handle(SwitchEvent(controller=enc, kind=SwitchEventKind.PRESS, timestamp=1.0))

    assert handler.bot_encoder_mode.name == "DEEP_EDIT"


def test_v1_footswitch_short_press(v1_system):
    """Footswitch press routes to _handle_footswitch."""
    handler = v1_system.handler
    hw = v1_system.hw
    fs = hw.footswitches[0]
    fs.sink = handler
    hw.midiout.reset_mock()

    handler.handle(SwitchEvent(controller=fs, kind=SwitchEventKind.PRESS, timestamp=1.0))

    # Toggles state + MIDI
    assert fs.toggled is True
    hw.midiout.send_message.assert_called_once()
