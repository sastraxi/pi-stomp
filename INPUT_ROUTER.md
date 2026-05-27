# Input Router — Architecture

Single dispatch path for all hardware input events. Replaces
`value_change_callback` slots, `consume_tweak_rotation`, the
`ExternalMidiOut` wrapper, and ad-hoc volume binding in `modhandler`.

## Branch graph

```
feat/external-midi
  feat/controller-routing
    feat/encoder-controller
      feat/input-router      ← this document
        feat/plugin-panels   ← uses LCD-as-sink to intercept
          feat/x42-eq        ← rewrites EQ on the base

feat/blend-mode              ← sibling; integration deferred
```

## Dispatch model

```
Hardware tick (10ms poll)
  → Controller.poll()
      → builds event (EncoderEvent | AnalogEvent | SwitchEvent)
      → controller already advanced its own state (quantizer,
        parameter.value, midi_value)
      → self.sink.handle(event)  →  bool (True = consumed)
```

- **Controllers are sources.** Each has one field, `sink: InputSink | None`.
  Before dispatching, the controller has already written `parameter.value`
  and `midi_value` — the event carries facts, not requests.
- **InputSinks are actors.** A sink implements `handle(event) -> bool`.
  Returning `True` means the sink fully handled the event; the controller
  does nothing further. Returning `False` is informational only — there is
  no automatic forwarding, no stack, no framework. Composition is whatever
  the sink's `handle` chooses to write.
- **Handler is the default sink** for v3 controllers. It asks the LCD
  first (so panels can intercept), then runs its own cascade in plain
  code: display the parameter dialog, commit to mod-host unless externally
  routed, send external MIDI, send to MIDI Through.
- **LCD owns its panel stack.** Push/pop semantics live where they
  actually mean something — opening and dismissing a panel. The LCD's
  `handle` walks that stack and returns True if any panel consumed.
- **Helpers** (`GpioSwitch`, `AnalogSwitch`) sit underneath their owning
  Controller. They detect raw edges; the Controller builds the event.
  They never call `sink.handle`.

There is no `InputRouter` class. There is no global sink stack. The
"router" is the single `sink` field on each controller plus whatever code
each sink chooses to write inside `handle`.

## Scope: v3 only

This branch migrates v3 (`Modhandler` + `Pistomptre`). v1 (`Mod` +
`Pistomp`) keeps its current dispatch paths. Shared controller classes
(`AnalogMidiControl`) accept `sink: InputSink | None`; `None` selects the
v1 inline path.

## Event types

```python
@dataclass
class ControllerEvent:
    controller: Controller

@dataclass
class EncoderEvent(ControllerEvent):
    rotations: int = 0
    multiplier: float = 1.0
    new_value: float = 0.0      # already-quantized parameter value
    new_midi_value: int = 0     # already-renormalized MIDI value

@dataclass
class AnalogEvent(ControllerEvent):
    raw_value: int = 0          # ADC reading
    midi_value: int = 0         # already-converted MIDI value

@dataclass
class SwitchEvent(ControllerEvent):
    kind: SwitchEventKind        # PRESS | RELEASE | LONGPRESS
```

Events are immutable carriers. They have no `consumed` field — that's the
return value of `handle`. Sinks discriminate by `isinstance` or `match`.

## InputSink protocol

```python
class InputSink(abc.ABC):
    @abc.abstractmethod
    def handle(self, event: ControllerEvent) -> bool: ...
```

One method. Sinks are free to do anything: forward to another sink,
ignore some event types, run a cascade, check `event.controller.type`,
match on event class, etc. No base class machinery — the freedom *is* the
design.

## Default cascade in `Modhandler.handle`

```python
def handle(self, event):
    if self.lcd.handle(event):
        return True
    match event:
        case EncoderEvent(controller=c, new_value=v):
            if c.parameter is not None:
                self.lcd.display_parameter_value(c.parameter, v)
            if not self.hardware.is_external(c) and c.parameter is not None:
                self.parameter_value_commit(c.parameter, v)
            self._emit_midi(c)
        case AnalogEvent(controller=c):
            if c.type == Token.VOLUME:
                self.audio_card.set_volume(c.midi_value)
                return True
            self._emit_midi(c)
        case SwitchEvent(controller=c, kind=k):
            self._handle_switch(c, k)   # footswitch chord resolver lives
                                        # here as a Handler-owned helper
    return True
```

`_emit_midi(c)` tries the external port if `hardware.is_external(c)`, else
sends to MIDI Through. Plain function, not a sink.

The chord resolver (Appendix A) becomes a `FootswitchChords` helper owned
by Handler — instance state, `tick()` called from `poll_controls`. Same
behavior as today, no longer framed as a sink.

## Encoder owns its state

`Encoder.refresh()` (now unified with `EncoderController`) advances the
quantizer, writes `self.parameter.value`, sets `self.midi_value`, then
builds an `EncoderEvent` with `new_value` and `new_midi_value` already
filled in, and dispatches. Sinks never reach into the encoder's
internals. `_move_steps` / `_value_to_midi` stay private.

Same shape for `AnalogMidiControl._send_value()`: compute `midi_value`,
stash it on `self`, build the event with `midi_value` set, dispatch.

## Push/pop lives on the LCD

When a panel opens, it pushes itself on the LCD's panel stack. When it
closes, it pops. The LCD's `handle` walks the stack top-down; a panel
returns True if it wants to consume the event for that controller. This
is the only place stack semantics exist in the system, and they live next
to the thing that actually needs them.

Per-controller targeting is free: a panel that only cares about encoder
#2 checks `event.controller is self.target_encoder` and returns False for
everything else.

## Scope: what changes on this branch

### Step 3 — one commit

1. Collapse `Encoder` + `EncoderController` into a unified `Encoder`
   class. Delete `encoder_controller.py`. The quantizer
   (`step_values` / `current_step`) is parameter-bound state, inert
   until `bind_to_parameter`.
2. Absorb the encoder button: optional `sw_pin` / `sw_adc_chan` /
   `shortpress` / `longpress`; owns a private switch helper; emits
   `SwitchEvent(controller=self, kind=...)`.
3. Drop `Controller` inheritance from `GpioSwitch`. `AnalogSwitch` was
   never a `Controller`. Neither dispatches directly.
4. Delete `Hardware.encoder_switches` and `encoder_switch_map`.
5. Add `sink: InputSink | None` field to `Controller`. `Hardware.register_sink(sink)`
   walks its controllers and assigns. `Modhandler.add_hardware` calls
   `hardware.register_sink(self)` after construction. v1 never calls it;
   v3 controllers end up with `sink = self.handler`.
6. Rewrite `Encoder.refresh()` and `AnalogMidiControl._send_value()`:
   compute state, build event, dispatch. Speed multiplier computed in
   `Encoder.refresh()` and set on the event for diagnostics.
7. Delete the `value_change_callback` slot on `Encoder` /
   `AnalogMidiControl`. `feat/blend-mode` will deal with the fallout
   when it merges.

### Step 4 — Volume routing

`Handler.handle` checks `event.controller.type == Token.VOLUME` for
analog events and calls `audio_card.set_volume` directly. Delete the
`bind_volume_encoder` / `value_change_callback` block in `modhandler`.
No new sink class.

### Step 5 — Switch routing

Move the footswitch chord resolver off `Footswitch` classvars into a
`FootswitchChords` helper owned by Handler. Instance state, rebuilt on
`pedalboard_changed`. `tick()` called from `poll_controls`. Encoder
buttons skip the chord path entirely.

### Step 6 — Delete `ExternalMidiOut`

Handler's `_emit_midi` calls `ExternalMidiManager` directly. Remove the
wrapper. Controllers always hold the virtual `midiout`; the external
routing is read from `Hardware.external_routing` by Handler.

### Step 7 — Tests

Run full suite. Add `tests/input_router/` with unit tests for the
default cascade, LCD intercept, chord helper, and per-controller
targeting.

## File layout (post-step-3)

```
pistomp/
  input/
    event.py                 # event dataclasses
    sink.py                  # InputSink ABC
    # sink implementations land here if/when Handler.handle is split
  controller/                # new package; collapse from flat files
    encoder.py               # unified, absorbs button
    footswitch.py
    analog_midi_control.py
```

No `pistomp/sink/` directory. The previous step-1/2 scaffolding
(`pistomp/input_router.py`, `pistomp/sink/`) is deleted.

## What this replaces

- The `InputRouter` class and `pistomp/sink/` package added in steps 1–2
  are deleted. Those steps stand as the design-validation pass; this
  step is the keep version.
- `value_change_callback` is deleted on `Encoder` and
  `AnalogMidiControl`. `feat/blend-mode` handles the fallout on merge.
- `Handler.encoder_value_changed` is deleted — its body becomes two
  lines inside `Handler.handle`.
- The `is_external` kwarg evaporates — Handler reads
  `hardware.is_external(controller)` directly inside `handle`.

## Out of scope

- EQ panel rewrite — `feat/plugin-panels` → `feat/x42-eq`.
- Tuner panel — doesn't intercept inputs today; untouched.
- MIDI Learn coordination — mod-host owns the learn map.
- LCD / output side — input dispatch only.
- v1 migration — see "Scope".

---

## Appendix A — Footswitch chords (longpress groups)

Pre-existing behavior that has to survive the migration. Today lives in
`pistomp/footswitch.py` as class-level state.

### What it is

Each footswitch's YAML `longpress` field names a group (or a list of
groups). Every footswitch naming the same group is a member; the group
name is also the key into `Footswitch.callbacks`. Hardcoded valid
groups: `next_snapshot`, `previous_snapshot`, `toggle_bypass`,
`set_mod_tap_tempo`, `toggle_tap_tempo_enable`, `toggle_tuner_enable`.

Resolution runs once per poll cycle inside a 400 ms window:

- Two switches in the same group both longpressed within 400 ms → fire
  the group callback once; suppress both solos.
- Singleton group (`number_in_group == 1`) → solo longpress fires
  400 ms after the press.
- Multi-member group, no partner within 400 ms → nothing fires.

The list form (`longpress: [a, b]`) is the only configuration where a
switch keeps its solo action *and* contributes to a chord.

### Where it lives now

`FootswitchChords` helper owned by `Handler`. Instance state: group
membership map, pending-longpress timestamps. Rebuilt on
`pedalboard_changed` after `hardware.reinit(cfg)`. `tick()` called from
`poll_controls`. When `Handler.handle` gets a footswitch `LONGPRESS`, it
hands it to `chord_helper.observe(event)`; the helper decides what fires
on the next `tick`.

Not a sink. Plain helper. Two reasons it stays internal to Handler:

1. Cross-controller state (timestamps from multiple footswitches).
2. Consume-vs-fire is timing-dependent and deferred — wants direct
   access to the resolver, not a generic protocol.
