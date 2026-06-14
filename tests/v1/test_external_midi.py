"""External controllers must render in the v1 mono analog-assignments zone."""

from pistomp.controller import AssignmentSource, ControlAssignment, ControlKind


def test_external_analog_assignments_render(v1_system, snapshot):
    v1_system.handler.lcd.render_assignments({
        3: ControlAssignment(slot_id=3, kind=ControlKind.KNOB, label=None, category="External",
                             source=AssignmentSource.EXTERNAL, port_name="c4", midi_cc=75),
        4: ControlAssignment(slot_id=4, kind=ControlKind.EXPRESSION, label=None, category="External",
                             source=AssignmentSource.EXTERNAL, port_name="hx", midi_cc=76),
    })
    snapshot()
