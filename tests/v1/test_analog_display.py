"""v1 analog assignment display — TDD characterization.

Covers the data layer (current.assignments after bind) and the LCD rendering
(render_assignments on lcdgfx) for KNOB, EXPRESSION, and External assignments.
"""

import common.token as Token
from pistomp.analogmidicontrol import AnalogMidiControl
from pistomp.controller import AssignmentSource, ControlKind


# ── Data layer ────────────────────────────────────────────────────────────────


def test_bound_knob_appears_in_assignments(v1_system, make_plugin, make_parameter):
    handler = v1_system.handler
    hw = v1_system.hw

    knob_key = next(k for k, c in hw.controllers.items() if isinstance(c, AnalogMidiControl) and c.type == Token.KNOB)

    tone = make_plugin("tone", category="Filter")
    tone.parameters["cutoff"] = make_parameter("cutoff", "tone")
    tone.parameters["cutoff"].binding = knob_key

    handler.current.pedalboard.plugins = [tone]
    handler.bind_current_pedalboard()

    knob_assignments = [a for a in handler.current.assignments.values() if a.kind == ControlKind.KNOB]
    assert len(knob_assignments) == 1
    assert knob_assignments[0].label == "cutoff"
    assert knob_assignments[0].category == "Filter"
    assert knob_assignments[0].source == AssignmentSource.MIDI_LEARNED


def test_bound_expression_appears_in_assignments(v1_system, make_plugin, make_parameter):
    handler = v1_system.handler
    hw = v1_system.hw

    exp_key = next(
        k for k, c in hw.controllers.items() if isinstance(c, AnalogMidiControl) and c.type == Token.EXPRESSION
    )

    tone = make_plugin("tone", category="Filter")
    tone.parameters["volume"] = make_parameter("volume", "tone")
    tone.parameters["volume"].binding = exp_key

    handler.current.pedalboard.plugins = [tone]
    handler.bind_current_pedalboard()

    exp_assignments = [a for a in handler.current.assignments.values() if a.kind == ControlKind.EXPRESSION]
    assert len(exp_assignments) == 1
    assert exp_assignments[0].label == "volume"
    assert exp_assignments[0].source == AssignmentSource.MIDI_LEARNED


def test_unbound_controls_absent_from_assignments(v1_system):
    handler = v1_system.handler
    handler.bind_current_pedalboard()

    analog_assignments = [
        a for a in handler.current.assignments.values() if a.kind in (ControlKind.KNOB, ControlKind.EXPRESSION)
    ]
    assert analog_assignments == []


# ── LCD rendering ─────────────────────────────────────────────────────────────


def test_render_assignments_knob_label(v1_system, make_plugin, make_parameter, snapshot):
    handler = v1_system.handler
    hw = v1_system.hw

    knob_key = next(k for k, c in hw.controllers.items() if isinstance(c, AnalogMidiControl) and c.type == Token.KNOB)

    tone = make_plugin("tone", category="Filter")
    tone.parameters["cutoff"] = make_parameter("cutoff", "tone")
    tone.parameters["cutoff"].binding = knob_key

    handler.current.pedalboard.plugins = [tone]
    handler.bind_current_pedalboard()
    handler.lcd.render_assignments(handler.current.assignments)

    snapshot("knob_bound")


def test_render_assignments_expression_label(v1_system, make_plugin, make_parameter, snapshot):
    handler = v1_system.handler
    hw = v1_system.hw

    exp_key = next(
        k for k, c in hw.controllers.items() if isinstance(c, AnalogMidiControl) and c.type == Token.EXPRESSION
    )

    tone = make_plugin("tone", category="Filter")
    tone.parameters["volume"] = make_parameter("volume", "tone")
    tone.parameters["volume"].binding = exp_key

    handler.current.pedalboard.plugins = [tone]
    handler.bind_current_pedalboard()
    handler.lcd.render_assignments(handler.current.assignments)

    snapshot("expression_bound")


def test_render_assignments_both_bound(v1_system, make_plugin, make_parameter, snapshot):
    handler = v1_system.handler
    hw = v1_system.hw

    knob_key = next(k for k, c in hw.controllers.items() if isinstance(c, AnalogMidiControl) and c.type == Token.KNOB)
    exp_key = next(
        k for k, c in hw.controllers.items() if isinstance(c, AnalogMidiControl) and c.type == Token.EXPRESSION
    )

    tone = make_plugin("tone", category="Filter")
    tone.parameters["cutoff"] = make_parameter("cutoff", "tone")
    tone.parameters["cutoff"].binding = knob_key
    tone.parameters["volume"] = make_parameter("volume", "tone")
    tone.parameters["volume"].binding = exp_key

    handler.current.pedalboard.plugins = [tone]
    handler.bind_current_pedalboard()
    handler.lcd.render_assignments(handler.current.assignments)

    snapshot("both_bound")


def test_render_assignments_unbound(v1_system, snapshot):
    handler = v1_system.handler
    handler.bind_current_pedalboard()
    handler.lcd.render_assignments(handler.current.assignments)

    snapshot("unbound")
