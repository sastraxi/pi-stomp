# Input dispatch

Every hardware input — footswitch, encoder, knob, expression pedal — flows through one path, identical across hardware versions. A control reads its own pins, advances its own state, packages what happened into an immutable event, and hands that event to a single sink. There is no `InputRouter` class and no global stack: the "router" is just the `sink` field every controller inherits, plus whatever code each sink writes inside `handle`.

## Controllers are sources, sinks are actors

A `Controller` (`controller.py`) owns one raw detector — an `Encoder`, `GpioSwitch`, `AnalogSwitch`, or ADC channel — and a `sink: InputSink`. On each 10ms tick `poll_hw()` reads the detector, advances the controller's own state (encoder quantizer, `parameter.value`, `midi_value`), builds the matching event, and calls `self.sink.handle(event)`. By the time the event is dispatched, the controller has already updated itself: **the event carries facts, not requests.**

An `InputSink` (`sink.py`) is one abstract method, `handle(event) -> bool`. `True` means "fully handled; the controller does nothing further." `False` is informational — there is no automatic forwarding, no framework. Sinks compose by writing the forwarding they want, in plain code.

The detectors underneath a controller (`Encoder.read_rotary()`, the GPIO/ADC switch callbacks) only sense raw edges and rotation. They never call `sink.handle` themselves — the owning controller builds the event.

## Events

Three immutable dataclasses (`event.py`), all carrying their source `controller`; sinks discriminate by `isinstance` / `match`:

* `EncoderEvent` — `rotations` this tick, plus the already-quantized `new_value` and `new_midi_value`.
* `AnalogEvent` — `raw_value` (ADC) and the already-converted `midi_value`.
* `SwitchEvent` — `kind` (`PRESS` | `LONGPRESS`) and a `timestamp`.

There is no `consumed` field; that's the return of `handle`. There is no `RELEASE` kind: GPIO footswitches fire only on short-press-release or long-press, so a completed short press *is* `PRESS` — the user-meaningful event. `SwitchEvent.timestamp` is the `time.monotonic()` captured at the moment of detection (the GPIO interrupt, the ADC press), threaded all the way through to tap-tempo stamping so timing reflects the press, not when the handler got around to it.

## The handler is the sink

For every controller on every version the sink is the handler — `Modhandler` (v2/v3) or `Mod` (v1) — wired once by `Hardware.register_sink(self)`. Its `handle` is a fixed cascade: ask the **LCD** first (so an open panel can intercept inputs for the encoder it cares about), then the active **blend mode**, then run the handler's own logic by event type — display the parameter dialog, commit to mod-host unless the control is externally routed, emit MIDI.

Push/pop semantics live on the LCD, next to the only thing that needs them: a panel pushes itself when it opens and pops when it closes, and the LCD's `handle` walks that stack top-down. Blend mode likewise intercepts at the handler instead of hijacking a controller callback — `intercept(event)` reads the source controller's normalized position and sends its diff map.

Encoders are split to keep this clean: `Encoder` is the pure quadrature decoder, `EncoderController` is the `Controller` that owns it plus the quantizer and the absorbed push-button. The nav encoder's button is not a standalone switch — it lives inside its controller and dispatches a `SwitchEvent` like any other. Footswitch chords (longpress groups) are the one piece of genuinely cross-controller, timing-deferred state, so they live in `footswitch_chords.py` as a handler-owned helper rather than a sink: `observe()` records a press, `tick()` resolves the 400ms window once per poll and names the callbacks that fired.

## Nav-encoder traversal pacing (`nav_queue.py`)

Nav detents advance the LCD panel-stack selector one position each. PR #42 correctly split compose from flush and batched detents into one event — collapsing a fast spin into a single jump. `NavQueue` restores the visible scanning feel without re-introducing inline SPI pushes into the 10ms path.

It's pure pacing policy (no pygame): the LCD enqueues signed detent counts on `enc_step`, drains capped steps per flush. Three levers:

1. **Queue, don't loop** — `enc_step` enqueues; the selector advances on each flush, spreading detents across frames.
2. **Flush every tick while pending** — `lcd_poll_divisor` drops to 1 when the queue is non-empty, so the main loop flushes at 100fps during a turn. Springs back when drained.
3. **Coalesce under backlog** — normally one detent per flush (visible scan). When pending exceeds `max_jump` (per-version, SPI-derived), drain pops up to `max_jump` per flush to bound latency.

Reversals create new tail runs — a mid-spin flip plays back as a direction change, not a net. Runs are never dropped; the queue drains FIFO, so the final run (the user's stopping direction) always lands exactly. The queue clears on `push_panel`/`pop_panel` so stale steps don't land on a new panel. `enc_step_widget` (param scrubbing) stays inline — it's a point-event with committed values, not traversal.

### `skip_frames`

`PanelStack.skip_frames` controls whether `propagate_dirty` pushes to the LCD immediately or defers to the next flush slot. The EQ plugin panel sets `skip_frames=True` (its dirty rects are near-full-screen and too large for the 10ms budget at 24MHz). Everything else pushes inline — each small clip (selection highlight, menu cursor) is its own frame, giving the snappy pre-#42 feel.
