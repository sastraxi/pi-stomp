# Input Router — Architecture

A single, ordered dispatch mechanism for all hardware input events on
piStomp. Replaces the current overlapping mechanisms
(`value_change_callback` slot, `consume_tweak_rotation` hook,
`ExternalMidiOut` wrapper, ad-hoc volume binding in
`modhandler.__init__`).

## Branch graph

```
feat/external-midi
  feat/controller-routing
    feat/encoder-controller
      feat/input-router      ← this document
        feat/plugin-panels   ← PLUGIN_PANELS.md (consumes the router)
          feat/x42-eq        ← X42_EQ_CODE_REVIEW.md (rewrites EQ on the base)

feat/blend-mode              ← sibling; integration deferred (Appendix A)
```

## How it works

```
Hardware tick (10ms poll)
  → Controller.poll()  (Encoder, Footswitch, AnalogMidiControl, ...)
      → builds an event (EncoderEvent | AnalogEvent | SwitchEvent)
      → self.router.dispatch(event)
          → walks sinks top-down (last-pushed first)
              → each InputSink.on_encoder / on_analog / on_switch
              → may mutate the event; may set event.consumed = True to terminate
```

- **Handler** owns the `InputRouter` (one per handler instance).
- **Controllers** are the *sources*. They construct events and call
  `router.dispatch`. They don't know who consumes — their job ends at
  dispatch.
- **InputSinks** are the *actors*. They are the only things that act on
  events: write `parameter.value`, emit MIDI, notify mod-host, drive a
  panel, etc. Pushed/popped on the router's stack; ordering is explicit.
- **Internal helpers** (`GpioSwitch`, `AnalogSwitch`) sit *underneath*
  their owning Controller. They detect hardware edges (GPIO interrupts,
  ADC thresholds) and call back into the Controller, which then builds
  the event. They never touch the router themselves and never appear in
  `hardware.controllers`.

The router is the single seam between "hardware produced something" and
"the system reacts." Everything inline today in
`EncoderController.refresh()`, `AnalogMidiControl._send_value()`, and
`Footswitch.pressed()` becomes a sink on the stack. Push/pop layering
replaces destructive callback-slot overwrite (the pattern in
`blend/input_controller.py:55-67` today).

## Scope: v3 only

This branch migrates **v3 (`Modhandler` + `Pistomptre`) only**. v1
(`Mod` + `Pistomp`) keeps its current dispatch paths unchanged —
`value_change_callback`, the encoder state machines at
`mod.py:127-129`, the inline footswitch dispatch, none of it moves.
The `Handler` ABC gains a `router` attribute, but `Mod.__init__`
either leaves it `None` or installs an empty router; no v1 code reads
it. Migrating v1 is a follow-up if/when someone needs it.

## Controllers and helpers

| Class | Role | Notes |
|-------|------|-------|
| `Encoder` | Controller | rotation + optional button; covers nav, tweak, volume |
| `Footswitch` | Controller | already owns its switch internally |
| `AnalogMidiControl` | Controller | expression pedal / knob |
| `GpioSwitch`, `AnalogSwitch` | helper | owned by a Footswitch or Encoder; detects raw edges |

**One class per hardware part.** Nav, tweak, and volume are the same
physical part — a rotary encoder, sometimes with a push button — so they
are one class. Today's `Encoder` + `EncoderController` collapse into a
unified `Encoder` (delete `encoder_controller.py`). The speed multiplier
always rides on the event — sinks decide whether they care. The
quantizer (`step_values` / `current_step`) is parameter-bound state and
is inert until `bind_to_parameter` is called. Role is sink-side, not
class-side.

**Encoders absorb their buttons.** The constructor takes optional
`sw_pin` / `sw_adc_chan` / `shortpress` / `longpress`, owns a private
helper, and emits `SwitchEvent(controller=self, kind=...)`. One logical
control = one Controller = one event source for rotation, PRESS, and
LONGPRESS. A sink that binds to encoder 1 catches everything via
`event.controller is self.target`.

**Switches are not Controllers.** `GpioSwitch` inherits `Controller`
today in name only — all call sites pass `None, None` for the MIDI
fields and the `channel:CC` registry never sees it. Drop the
inheritance. `AnalogSwitch` was never a Controller. Neither fires router
events directly.

## Event types

```python
@dataclass
class ControllerEvent:
    controller: Controller
    consumed: bool = False

@dataclass
class EncoderEvent(ControllerEvent):
    rotations: int          # raw detents this tick
    multiplier: float = 1.0 # acceleration; natural movement = rotations * multiplier

@dataclass
class AnalogEvent(ControllerEvent):
    raw_value: int          # ADC reading

@dataclass
class SwitchEvent(ControllerEvent):
    kind: SwitchEventKind   # PRESS | RELEASE | LONGPRESS
```

## Router

```python
# pistomp/input_router.py
class InputSink(Protocol):
    def on_encoder(self, event: EncoderEvent) -> None: ...
    def on_analog(self, event: AnalogEvent) -> None: ...
    def on_switch(self, event: SwitchEvent) -> None: ...

class InputRouter:
    def __init__(self) -> None:
        self._sinks: list[InputSink] = []
    def push(self, c: InputSink) -> None: ...
    def pop(self, c: InputSink) -> None: ...   # remove by identity
    def dispatch(self, event: ControllerEvent) -> None:
        for c in reversed(self._sinks):        # last pushed runs first
            ...                                # type-dispatch
            if event.consumed: return
```

The `Handler` ABC declares `router: InputRouter` so every hardware
version sees the same contract. Controllers take the router directly in
their constructor — it's their only outward dependency:

```python
class Controller:
    def __init__(self, ..., router: InputRouter):
        self.router = router
    def fire(self, event: ControllerEvent) -> None:
        self.router.dispatch(event)
```

Hardware reads `self.handler.router` and passes it into each controller
constructor. Helpers don't take `router` — the owning Controller wires
the helper's callback to its own `_emit_switch_event` that builds and
dispatches.

## The standard sink pipeline

These sinks aren't optional defaults — they **are** the inline work
from `EncoderController.refresh()`, `AnalogMidiControl._send_value()`,
and the sinks's encoder/analog callbacks, extracted into named
objects. Without them, encoders and analog controls do nothing.

End-of-init stack, top-down:

```
[top — runs first]
ParameterUpdateSink      # writes parameter.value, advances quantizer
ModSink                  # notifications (encoder_value_changed, ...)
ExternalMidiSink         # tries external; consumes on success, falls through on failure
MidiOutSink              # virtual ALSA MIDI Through; unconditional when reached
[bottom — runs last]
```

`ExternalMidiSink` filters by `controller.get_routing_info()` — for
VIRTUAL controllers it early-returns without consuming; for EXTERNAL
controllers it calls `ExternalMidiManager.send_cc()` and consumes only
if the send succeeds. `MidiOutSink` is the unconditional fallback: it
emits whenever the event reaches it, which happens for VIRTUAL
controllers (external sink skipped them) or when an EXTERNAL send
failed (external sink left the event un-consumed). This reproduces the
old `ExternalMidiOut` fallback behavior structurally via the sink
stack instead of inline branching, and kills the wrapper class entirely.

`ModSink` sits **above** `ExternalMidiSink` so handler hooks (LCD
refresh, REST commits) always fire — they can't be short-circuited by
an external send consuming the event.

`ParameterUpdateSink` reads/writes per-encoder quantizer state
(`step_values`, `current_step`), which stays on the Controller. The
quantizer resets on `controller.bind_to_parameter()` — already true today.

Speed amplification is not a sink — it's intrinsic to the encoder,
computed in `Encoder.refresh()` before the event fires.

### Installation

`Modhandler` (v3) populates the pipeline at the end of its own
`__init__`, not in the base class. `Mod` (v1) is out of scope (see
"Scope: v3 only" above) and does not install sinks on this branch:

```python
# modalapi/modhandler.py
def __init__(self, ...):
    ...existing state...
    self.router = InputRouter()
    self._install_default_sinks()    # v3 set
```

The ABC declares `router` but doesn't instantiate it — matches the
existing `add_lcd` / `add_hardware` "subclass populates" idiom. v1
keeps its state-machine encoder modes (`mod.py:127-129`) and never
populates the router.

## Push/pop sites

**On this branch:** all pushes happen in `_install_default_sinks`
during handler init (the standard pipeline + `AudioVolumeSink` if
configured). Nothing pops. `pop` is dead code until a later branch
consumes the protocol.

Runtime push/pop arrives with:
- `feat/plugin-panels` — panels become `InputSink`s, push on open,
  pop on dismiss.
- `feat/blend-mode` — `BlendMode.activate()` / `deactivate()`.

**Pedalboard change does not push/pop.** Sinks are stateless w.r.t.
pedalboard config: they dispatch through `event.controller.X` (mode,
midi_CC, shortpress, ...) every event. `hardware.reinit(cfg)`
continues to mutate those fields on the controllers as today, and
the next event the sink handles picks up the new value automatically.
The pipeline is installed once at handler init and never rebuilt.
Appendix C describes a follow-up that replaces the `reinit` mutation
path with a pushed overlay sink; not needed for this branch.

The one exception is `FootswitchChordSink`, which owns instance state
(the group-membership map, moved off `Footswitch` classvars). It
subscribes to a `Handler.pedalboard_changed` callback and rebuilds
its map by walking `hardware.controllers`.

**Contrast with `PanelStack`** (`uilib/panel.py:259`, ~15 callers):
similar LIFO shape, but PanelStack delivers `input_event` to **only the
top** (`self.current`), while `InputRouter` walks down until
`event.consumed`. This is load-bearing: the standard pipeline at the
bottom must keep running for non-intercepted events when a panel sits on
top. PanelStack's one-current model can't express that.

**Cross-stack coordination** (later, not this branch): a plugin panel
pushes onto **both** stacks — PanelStack for rendering, InputRouter for
input interception. The panel's `show()` / `hide()` lifecycle is the
natural seam; no new mechanism needed.

## Volume encoder

Not a special case. `AudioVolumeSink` filters by `event.controller.type == Token.VOLUME`
(set in YAML, passed to `Encoder` at construction), and consumes. If no encoder
has `type: VOLUME`, the sink isn't pushed. The
`enc.value_change_callback = volume_change_callback` block in
`modhandler.__init__` gets deleted.

## Switch events (footswitches and encoder buttons)

Footswitches and encoder buttons are the same kind of source: both
detect edges and produce `SwitchEvent`. Hardware coupling stays on the
**controller** side (an `Encoder` owns its button and emits both
rotations and clicks); routing concerns split on the **sink** side
(rotation may go to `ExternalMidiSink` / `MidiOutSink`, the click to
`SwitchActionSink`, with neither sink knowing about the other).

Today: `Footswitch.pressed()` dispatches inline on `self.mode` (CC /
Bypass / Preset / Tap) and updates LED + bound parameter directly.
Longpress group state (`footswitch.py:30-86`) lives in class-level
timestamps and is resolved by `check_longpress_events()` once per poll
cycle inside a 400 ms window.

After:

- The internal switch helper detects raw PRESS / LONGPRESS / RELEASE
  edges. Its callback into the owning Controller (Footswitch or
  Encoder) is a thin shim that emits `SwitchEvent(controller=self,
  kind=...)`. The event is fully self-describing; no sink times
  anything by hand.
- `SwitchActionSink` handles `SwitchEvent` for both footswitches and
  encoder buttons. It reads the configured action off
  `event.controller` (footswitch `mode` for CC / bypass / preset / tap;
  encoder `shortpress` / `longpress` callbacks with optional args from
  YAML) and performs it. One sink, one config shape, two source types.
- The 400 ms multi-switch chord window is its own sink
  (`FootswitchChordSink`) sitting *above* `SwitchActionSink` on the
  stack. It buffers LONGPRESS events from group-member footswitches;
  if a chord resolves within the window it consumes both and fires
  the group callback, otherwise it lets the LONGPRESS fall through
  to `SwitchActionSink` unchanged. The router's stack model is
  exactly the right shape for this "intercept above, fall through
  below" behavior. See Appendix B for the full semantics.
- **LED has two writers, both preserved.** Today
  `Footswitch.set_value()` (the parameter-binding inbound hook)
  updates the LED when an external source changes `:bypass`; the
  press path updates it after acting. After migration, the press-side
  update moves into `SwitchActionSink` (and no-ops for encoder
  buttons, which have no LED); the parameter-binding hook stays
  exactly where it is. The router doesn't touch outbound LED state.

## EQ panel intercept

Stays on `feat/x42-eq` for now via `Handler.consume_tweak_rotation`. On
`feat/plugin-panels` the panel base class becomes an `InputSink` and
push/pops itself; the `consume_tweak_rotation` hook deletes then.

## File layout

```
pistomp/
  input_router.py              # events, InputSink, InputRouter
  controller/
    __init__.py
    encoder.py
    footswitch.py
    ...
  sink/
    __init__.py
    parameter_update.py
    midi_emit.py
    external_midi.py
    mod.py                     # ModSink
    audio_volume.py
    switch_action.py           # handles footswitch + encoder-button SwitchEvents
    footswitch_chord.py        # 400 ms group resolver; sits above switch_action
```

## Out of scope

- **EQ panel rewrite** — `feat/plugin-panels` → `feat/x42-eq`.
- **Tuner panel** — doesn't intercept inputs today; untouched.
- **MIDI Learn coordination** — mod-host owns the learn map. Suppressing
  CC emission means consuming the event before it arrives at `MidiOutSink`.
- **LCD / output side** — router is input only.

## Migration checklist

1. Add `pistomp/input_router.py` (events + `InputSink` +
   `InputRouter`). Declare `router` on the `Handler` ABC.
2. Implement standard sinks (parameter update, midi emit, external
   midi, mod). Per-subclass `_install_default_sinks()`.
3. **In one commit:** collapse `Encoder` + `EncoderController` into one
   `Encoder` class (delete `encoder_controller.py`); absorb each
   encoder's button (optional `sw_pin` / `sw_adc_chan` in the
   constructor); drop `Controller` inheritance from `GpioSwitch`;
   delete `Hardware.encoder_switches` / `encoder_switch_map`; thread
   `router: InputRouter` into every Controller constructor; rewrite
   `Encoder.refresh()` and `AnalogMidiControl._send_value()` to fire
   events. Speed multiplier stays inside `Encoder` and is set on the
   event before dispatch. Doing this as one commit avoids a transient
   state where the encoder class is collapsed but still calls the
   deleted callback path.
4. Add `AudioVolumeSink`; delete the `value_change_callback` setup
   in `modhandler.__init__`.
5. Migrate footswitch and encoder-button dispatch to `SwitchActionSink`
   (reads footswitch `mode` or encoder `shortpress`/`longpress` config
   off the controller). Add `FootswitchChordSink` above it to resolve
   the 400 ms group window — instance state replaces today's
   class-level group registry on `Footswitch`. Wire the chord sink to
   a `Handler.pedalboard_changed` callback so it rebuilds its group
   map after `hardware.reinit(cfg)`. Call `chord_sink.tick()` from
   `Modhandler`'s `poll_controls` path. Delete class-level group state.
6. Replace `ExternalMidiOut` with `ExternalMidiSink` calling
   `ExternalMidiManager` directly. Delete the wrapper.
7. Run full test suite; expect minimal snapshot churn.

**v1 scope reminder:** all of the above touches v3 code paths
(`Modhandler`, `Pistomptre`, the controllers it instantiates). v1
(`Mod`, `Pistomp`, `Pistompcore`) is left alone on this branch —
including its `EncoderController` usage and inline footswitch
dispatch. If shared code (e.g. `Footswitch`) is modified, v1 paths
must continue to work.

The `value_change_callback` slot on `AnalogMidiControl` / `Encoder`
**stays** on this branch — no caller here, but kept so `feat/blend-mode`
continues to build as a sibling. Deletes with the blend migration
(Appendix A).

## Resolved during design

- **Controllers take `router: InputRouter` directly**, not `handler`.
  After the refactor, `router.dispatch` is the controller's only outward
  dependency — `encoder_value_changed`, MIDI emit, and LCD refresh all
  move to sinks; LED writes are direct GPIO. The contract still
  lives on the `Handler` ABC; Hardware passes `handler.router` through.
- **One class per hardware part.** `Encoder` + `EncoderController`
  collapse; nav, tweak, and volume are the same hardware with different
  sinks. Encoder + button are one Controller (no `Controller.parent`
  back-reference needed).
- **`parameter.value` is not read synchronously per-tick.** Read sites
  (`controller.py:92`, `encoder_controller.py:138`) are bind-time only;
  per-tick callbacks receive `new_value` as an argument. The
  `param.value = new_value` mutation in
  `EncoderController.refresh():198` is redundant with the one inside
  `parameter_value_commit`. `ParameterUpdateSink` placement is
  flexible; above `ModSink` is tidiest.

## Open questions

- **Tests.** Existing `tests/v3/test_eq_panel.py` is untouched on this
  branch (EQ migration is later). Add `tests/input_router/` with
  per-sink unit tests plus push/pop integration tests asserting
  dispatch order.

---

## Appendix A — Blend mode: the motivating example

Blend is the feature that made the current architecture's limits
unignorable. The router exists in the shape it does because of what
blend exposed.

### The pattern blend has to work around

`blend/input_controller.py:55-67` takes over an input by writing into
the controller object directly:

```python
control.value_change_callback = self.handle_value_change   # hijack
...
control.value_change_callback = None                       # restore
```

That single slot is the only handoff mechanism. It has three problems
that compound:

1. **Destructive.** Whatever was in the slot is gone. If anything else
   on the system also wants that input (volume binding, an open panel,
   a future feature), last write wins silently.
2. **No layering.** No notion of "blend is on top *for now*"; only "blend
   replaced whatever was there." Restoring means remembering the prior
   value, which works for one layer and breaks for two.
3. **Action-at-a-distance.** The clobber happens deep inside
   `BlendMode.activate()`, far from the input it touches. The volume
   callback set in `modhandler.__init__` can be erased without either
   site referencing the other — a known footgun today.

The router replaces this with **push/pop on an explicit stack**. Each
intercepting feature is a named sink; ordering is important; nothing
overwrites anything, it just overrides. Blend, panels, and the volume
encoder coexist by construction.

The same pattern reappears in `Handler.consume_tweak_rotation` (the EQ
panel hook) and in `EncoderController.value_change_callback` — both are
single-slot hijack mechanisms with the same compounding problems. All
three deletions are tracked: EQ on `feat/plugin-panels`, blend in this
appendix, volume in step 5 of the migration checklist.

### Why blend's migration is deferred

`feat/blend-mode` is a sibling of `feat/input-router`, not a descendant.
This branch leaves the `value_change_callback` slot in place (unused
here) and does **not** invoke it from the rewritten `refresh()` /
`_send_value()` paths. In any bouquet composition that includes both
branches, blend will fail to take over inputs until the migration below
lands.

We accept that short-lived regression rather than carry transitional
dispatch in `refresh()` / `_send_value()` — keeping this branch's code
clean is worth more than bouquet continuity for the one feature.

### The migration (proposed `feat/blend-mode-router`)

1. Rename `blend.input_controller.InputController` → `BlendInputSink`;
   implement `InputSink`. `on_analog` / `on_encoder` filter by
   `event.controller is self.target`; on match, run the existing
   `_resolve_position` / `_send_diff_map` flow and consume.
2. Delete `attach_to_input` / `detach_from_input`. `BlendMode.activate()`
   calls `handler.router.push(self.sink)`; `deactivate()` pops.
3. Delete the `value_change_callback` slot from `AnalogMidiControl` and
   `Encoder` — no callers remain.
4. The volume-encoder clobber footgun goes away by construction —
   `AudioVolumeSink` and `BlendInputSink` sit on the stack
   independently and never match the same controller.

### Why it's safe to defer

- The frozen parameter set is written via `parameter_setter` straight to
  mod-host; never touches the router on either branch.
- The input hijack is the only coupling, and it's a single attribute on
  two controller classes — cheap to leave behind, cheap to delete.
- Blend's internal logic (resolve, send_diff_map, sync, easing) is
  unchanged by the router. Only its attachment shape changes.

---

## Appendix B — Footswitch chords (longpress groups)

A pre-existing quirk worth flagging because it has to survive the
migration. Today, lives entirely in `pistomp/footswitch.py`.

### What it is

Each footswitch's YAML `longpress` field is a string (or a list of
strings). The string names a **group**. Every footswitch that names the
same group is a member; the group name is also the key into
`Footswitch.callbacks`, which resolves to a handler method.

Group resolution runs once per poll cycle in
`Footswitch.check_longpress_events()` (footswitch.py:56-81), inside a
400 ms window:

- Two switches in the same group both longpressed within 400 ms →
  fire the group's callback once. Both solos are suppressed (the
  resolver calls `_clear_all_groups()` after firing).
- A switch alone in its group (group `number_in_group == 1`) → solo
  longpress fires 400 ms after the press, with no partner having
  arrived.
- A switch sharing a group with another (`number_in_group >= 2`) but
  no partner press within 400 ms → nothing fires. The solo branch is
  skipped when the group has multiple members.

The list form (`longpress: [a, b]`) puts one switch in multiple groups
at once, which is the only configuration where a switch keeps its solo
action *and* contributes to a chord.

### Why it's weird

- **Fixed string registry.** The six valid group names are hardcoded
  in `Footswitch.init` (`footswitch.py:45-50`):
  `next_snapshot`, `previous_snapshot`, `toggle_bypass`,
  `set_mod_tap_tempo`, `toggle_tap_tempo_enable`,
  `toggle_tuner_enable`. Anything else parses silently and never
  fires.
- **Group name = callback name.** One identifier links switches *and*
  keys the handler callback. Two chords that both call
  `toggle_bypass` aren't expressible; renaming a group means renaming
  a handler method.
- **Same YAML keyword, mode-dependent semantics.** `longpress: X`
  means "fires solo after 400 ms" when the switch is alone in group
  X, and "fires only as a chord" when X has other members. Not
  documented in the template comments.
- **Footswitches only.** Encoder buttons with `longpress: <name>` go
  straight through `GpioSwitch.longpress_callback` (gpioswitch.py:92)
  and never touch the group state. Same keyword, completely different
  dispatch path.

### How it fits the router

A `FootswitchChordSink` (or equivalent — named here to distinguish
from the per-switch `SwitchActionSink`) owns the group-membership map
and the 400 ms resolver as instance state. It sits on the stack
**above** `SwitchActionSink` and consumes:

- `SwitchEvent(kind=LONGPRESS)` on **any footswitch** → always consume
  and buffer the timestamp. Every footswitch's `longpress` field names
  a group today (singletons included), so the chord sink owns every
  footswitch longpress. Nothing falls through to `SwitchActionSink`
  for footswitch longpresses.
- A `tick()` method called explicitly by the handler each poll cycle
  (not part of the `InputSink` protocol). On tick, resolve pending
  timestamps:
  - Chord matched within 400 ms → fire group callback, clear both.
  - Singleton group (`number_in_group == 1`) and 400 ms elapsed →
    fire solo callback.
  - Multi-member group, no partner within 400 ms → discard (matches
    today's behavior — nothing fires).
- Encoder-button LONGPRESS bypasses this sink entirely (encoder
  buttons don't participate in groups) and falls through to
  `SwitchActionSink`.

Two reasons this has to be its own sink, not folded into
`SwitchActionSink`:

1. The chord resolver needs cross-controller state (timestamps from
   multiple footswitches). `SwitchActionSink` is stateless per event.
2. The "consume vs. let-through" decision is timing-dependent and
   deferred — exactly the kind of work the router's stack model is
   for. Putting it above `SwitchActionSink` means the solo path stays
   trivial: by the time a `LONGPRESS` reaches `SwitchActionSink`, the
   chord sink has already decided it isn't a chord.

Encoder-button longpresses bypass this sink entirely; they fall
straight to `SwitchActionSink` and fire immediately. Preserves
current behavior. Any decision to unify encoder + footswitch longpress
semantics is out of scope here.

---

## Appendix C — Pedalboard config as a pushed sink

A forward-looking simplification the router unlocks. Not part of this
branch, but worth recording because the design here was chosen with it
in mind.

### The pattern today

Pedalboard configs overlay the global config via field-level merge in
`hardware.reinit(cfg)`. On pedalboard change the merge runs against the
existing controller instances, mutating their state in place. On
pedalboard *un*load the only way to restore defaults is to merge the
*next* config — there's no symmetric "undo." This is fine in practice
but brittle: every field that can be overridden needs explicit merge
logic, and the order of operations between `reinit`, `bind_current_pedalboard`,
and external-MIDI sync is load-bearing.

### What the router enables

For overrides that are *dispatch behavior*, a pedalboard config
naturally compiles into one `PedalboardOverlaySink`:

```
push at pedalboard load:   global pipeline ── [PedalboardOverlaySink] ← top
pop at pedalboard unload:  global pipeline   (restored by construction)
```

The overlay sink holds a map `controller_id → override-action`. On
each event it checks membership; if the controller has an override
under the current pedalboard, it runs the override and consumes;
otherwise it falls through to the standard pipeline. The global
defaults underneath never move, never get rewritten, never need to be
"restored."

Covers cleanly:

- Footswitch `midi_CC`, `bypass`, `preset`, `mode`.
- Encoder `shortpress` / `longpress` callback + args.
- Analog control behavior (different CC, different parameter target).

### What it does not cover

These are not dispatch and still need an overlay mechanism somewhere
else — `hardware.reinit(cfg)` doesn't go away entirely:

- **Display attributes** — footswitch `color`, `lcd_color`, `category`,
  `display_label`, pixel colors. Rendered on LCD/LED, never carried on
  router events. A parallel "config overlay" object readable by the
  LCD layer is the likely shape, but that's a separate refactor.
- **Parameter bindings.** `bind_current_pedalboard()` wires controllers
  to plugin parameters on load. Structural setup, not a dispatch
  override.
- **External MIDI on load.** Already pedalboard-scoped one-shot;
  unrelated to the router.

### Load-bearing assumption

This model assumes pedalboard configs override *properties of existing
controllers* and never create or destroy controllers. That matches
today's usage; the design breaks if a pedalboard ever needs to declare
"and also a fifth encoder."

### Why it's a follow-up, not this branch

The overlay refactor can't even be attempted until the router exists.
Landing it inside `feat/input-router` would conflate two architectural
moves and balloon the diff. Sequence: ship the router with global
config wired in directly, then a follow-up branch (`feat/pedalboard-overlay`
or similar) extracts the overlay sink and removes the dispatch-side
fields from `hardware.reinit`.
