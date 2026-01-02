# Encoder Velocity Architecture Plan

## Problem Statement

### Current Architecture Issues

1. **Hardcoded magic number in 3 places**
   - `EncoderMidiControl.per_click = 8` (encodermidicontrol.py:40)
   - `Mod.parameter_tweak_amount = 8` (mod.py:124)
   - `Parameterdialog.parameter_tweak_amount = 8` (parameterdialog.py:36)

2. **Three separate code paths for encoder changes**
   - **MIDI encoders (v3)**: EncoderMidiControl → MIDI CC → handler.parameter_midi_change → LCD
   - **Volume encoders (v3)**: Plain Encoder → handler.system_menu_headphone_volume → audio_parameter_change → LCD
   - **Volume encoders (v1/v2)**: Plain Encoder → state machine → parameter_value_change → LCD

3. **LCD module participates in value calculation**
   - `modhandler.py:965-1023`: audio_parameter_change() creates ParameterDialog and calls lcd.enc_step_widget()
   - `lcd320x240.py:593-613`: enc_step_widget() calls dialog.parameter_value_change(direction)
   - `parameterdialog.py:102-128`: parameter_value_change() calculates new value using hardcoded tweak_amount
   - LCD shouldn't be doing math - it should only display

4. **Duplicate parameter change logic**
   - `mod.py:1378-1393`: parameter_value_change() - does renormalize_float + clamping
   - `parameterdialog.py:102-128`: parameter_value_change() - does discrete point lookup + clamping
   - Different algorithms for the same conceptual operation

5. **No velocity sensitivity**
   - Impossible to precisely select certain MIDI values with fixed step size
   - Fast spins and slow turns feel the same

## Proposed Architecture

### Core Principle: Separation of Concerns

**Current**: Encoder → (MIDI or State Machine or LCD) → Value Calculation → Display
**Proposed**: Encoder → Controller (calculates value) → Handler (notifies) → Display

---

### System Overview

The new architecture introduces three components that work together to handle encoder rotations:

**1. VelocityTracker** - Measures rotation speed
- Collects rotation events with timestamps over a 200ms sliding window
- Performs linear regression to calculate velocity (rotations/second)
- Maps velocity to step multiplier using quadratic curve (slow=1 step, fast=16+ steps)

**2. ParameterQuantizer** - Manages discrete parameter values
- Quantizes parameter range (float or int) into 256 discrete steps
- Maintains current step position
- Moves by N steps and returns the actual parameter value
- Works identically for MIDI (0-127), volume (-32.0 to 6.0), or plugin parameters

**3. EncoderController** - Orchestrates the encoder behavior
- Inherits from base Encoder (hardware quadrature decoding)
- Owns a VelocityTracker and ParameterQuantizer
- On each rotation: asks VelocityTracker for multiplier, moves ParameterQuantizer by that many steps
- Sends MIDI CC if configured (for MIDI-mapped parameters)
- Notifies handler of new value for display update

---

### Data Flow

```
User rotates encoder
    ↓
Encoder GPIO interrupt fires (base class)
    ↓
EncoderController.refresh(direction) called
    ↓
    ├─→ VelocityTracker.add_rotation(direction)
    │   └─→ returns multiplier (1-16+) based on rotation speed
    ↓
    ├─→ ParameterQuantizer.move_steps(direction * multiplier)
    │   └─→ returns new parameter value from discrete step array
    ↓
    ├─→ Send MIDI CC (if encoder has midi_CC configured)
    ↓
    ├─→ Update parameter.value
    ↓
    └─→ handler.encoder_value_changed(parameter, new_value)
        └─→ lcd.display_parameter_value(parameter, new_value)
            └─→ ParameterDialog.update_value(new_value)
                └─→ Redraw graph, reset auto-close timer
```

---

### Unification of Encoder Types

All encoders use the same code path regardless of type:

- **MIDI encoders (v3 tweak knobs)**: EncoderController with `midi_CC=70/71`, bound to plugin parameter
- **Volume encoders**: EncoderController with `midi_CC=None`, bound to audiocard parameter
- **Navigation encoders**: EncoderController bound to menu/selection parameter

The only difference is what parameter they're bound to and whether they send MIDI CCs.

---

### Blend Mode Integration

Blend mode remains orthogonal via the existing callback mechanism:

```python
# Normal mode
encoder.value_change_callback = None
encoder.refresh(direction)
  → handler.encoder_value_changed()  # Updates LCD

# Blend mode active
encoder.value_change_callback = blend_controller.handle_input
encoder.refresh(direction)
  → blend_controller.handle_input()  # Interpolates parameters, skips LCD
```

EncoderController checks `if self.value_change_callback` before calling handler - no special-case code needed.

---

### New Components

#### 1. VelocityTracker (`pistomp/velocity_tracker.py`)

**Responsibility**: Track rotation timing, calculate velocity, return step multiplier.

```python
class VelocityTracker:
    WINDOW_MS = 200

    def __init__(self):
        self.samples = []  # [(timestamp, direction), ...]

    def add_rotation(self, direction: int) -> int:
        now = time.monotonic()
        self.samples.append((now, direction))
        self._prune_old_samples(now)

        velocity = self._calculate_velocity()
        return self._velocity_to_multiplier(velocity)

    def _calculate_velocity(self) -> float:
        # Linear regression: fit line through (time, cumulative_rotations)
        # Extract timestamps and cumulative rotation counts
        # Use np.polyfit(time_deltas, cumulative_rotations, 1)
        # Slope = rotations per second (absolute value)
        ...

    def _velocity_to_multiplier(self, velocity: float) -> int:
        # Quadratic curve: multiplier = int(velocity^2)
        # Clamped to range [1, 8] (tuned via hardware testing)
        ...
```

**Why this helps**: Single place for velocity calculation. No hardcoded per_click. Testable independently.

---

#### 2. ParameterQuantizer (`pistomp/parameter_quantizer.py`)

**Responsibility**: Quantize continuous ranges into discrete steps, handle step movement.

```python
class ParameterQuantizer:
    def __init__(self, minimum: float, maximum: float, num_steps: int, taper: float = 1.0):
        self.minimum = minimum
        self.maximum = maximum
        self.num_steps = num_steps  # Configurable per use case
        self.step_values = self._compute_steps()
        self.current_step = 0

    def set_value(self, value: float):
        self.current_step = self._value_to_step(value)

    def move_steps(self, delta_steps: int) -> float:
        self.current_step = np.clip(self.current_step + delta_steps, 0, self.num_steps - 1)
        return self.step_values[self.current_step]

    def get_value(self) -> float:
        return self.step_values[self.current_step]
```

**Why this helps**:
- Unifies MIDI (0-127 int) and volume (-32.0 to 6.0 float) into same abstraction
- Single place for discrete step calculation (replaces 3 different implementations)
- Configurable resolution: MIDI encoders use 128 steps, non-MIDI use 256

---

#### 3. EncoderController (`pistomp/encoder_controller.py`)

**Responsibility**: Combine encoder hardware with velocity tracking and parameter quantization.

**Inherits from**: `encoder.Encoder` (hardware interface) and `controller.Controller` (from `pistomp/controller.py`)

```python
class EncoderController(encoder.Encoder, controller.Controller):
    def __init__(self, handler, d_pin, clk_pin, midi_CC, midi_channel, midiout, **kwargs):
        super().__init__(d_pin=d_pin, clk_pin=clk_pin, callback=self.refresh,
                        midi_CC=midi_CC, midi_channel=midi_channel, **kwargs)
        self.handler = handler
        self.midiout = midiout
        self.velocity_tracker = VelocityTracker()
        self.quantizer = None
        self.value_change_callback = None  # For blend mode override

    def bind_to_parameter(self, parameter):
        self.parameter = parameter
        self.quantizer = ParameterQuantizer(parameter.minimum, parameter.maximum)
        self.quantizer.set_value(parameter.value)

    def refresh(self, direction: int):
        multiplier = self.velocity_tracker.add_rotation(direction)
        delta_steps = direction * multiplier
        new_value = self.quantizer.move_steps(delta_steps)

        if self.midi_CC:
            midi_value = self._value_to_midi(new_value)
            self.midiout.send_message([self.midi_channel | CONTROL_CHANGE, self.midi_CC, midi_value])

        self.parameter.value = new_value

        if self.value_change_callback:
            self.value_change_callback(new_value, self)
        else:
            self.handler.encoder_value_changed(self.parameter, new_value)
```

**Why this helps**:
- Single controller for ALL encoder types (MIDI, volume, nav)
- Velocity tracking built-in, no special cases
- Blend mode support via callback (orthogonal, no special code)
- LCD decoupled - handler decides what to display, controller just reports new value

---

### Updated Components

#### 4. Handler Changes (`modalapi/modhandler.py`)

**Before**:
```python
def parameter_midi_change(self, param, direction):
    if param:
        d = self.lcd.draw_parameter_dialog(param)
        if d:
            self.lcd.enc_step_widget(d, direction)  # LCD does the math!

def system_menu_headphone_volume(self, direction):
    self.audio_parameter_change(direction, audiocard.MASTER, ...)

def audio_parameter_change(self, direction, symbol, ...):
    d = self.lcd.draw_audio_parameter_dialog(...)  # LCD does the math!
    self.lcd.enc_step_widget(d, direction)
```

**After**:
```python
def encoder_value_changed(self, parameter, new_value):
    # Handler doesn't care if it's MIDI, volume, or plugin parameter
    # It just updates the display
    self.lcd.display_parameter_value(parameter, new_value)

    # Commit to backend if needed
    if parameter.is_audio_parameter():
        self.audiocard.set_volume_parameter(parameter.symbol, new_value)
    elif parameter.is_plugin_parameter():
        self.parameter_commit(parameter)
```

**Why this helps**:
- Handler orchestrates, doesn't calculate
- Same code path for all parameter types
- LCD doesn't participate in value calculation

---

#### 5. LCD Changes (`pistomp/lcd320x240.py`)

**Before**:
```python
def enc_step_widget(self, widget, direction):
    # LCD calculates new value!
    if type(widget) is Parameterdialog:
        widget.parameter_value_change(direction)
    elif type(widget) is Menu:
        widget.input_event(InputEvent.RIGHT if direction == 1 else InputEvent.LEFT)
```

**After**:
```python
def display_parameter_value(self, parameter, value):
    # LCD only displays, doesn't calculate
    dialog = self.get_or_create_parameter_dialog(parameter)
    dialog.update_value(value)
    dialog.refresh()
```

**Why this helps**:
- LCD is purely presentation layer
- No more type-checking widget to decide math algorithm
- ParameterDialog becomes dumb display widget

---

#### 6. ParameterDialog Changes (`uilib/parameterdialog.py`)

**Before**:
```python
def parameter_value_change(self, direction):
    # Dialog does the math!
    value = float(self.param_value)
    i = self._find_nearest_element_index(self.actual_points, value)
    new = i-1 if (direction != 1) else i+1
    new_value = self.actual_points[new] if (0 <= new < self.num_actual) else value
    # ... clamping ...
    self.param_value = new_value
    if self.action is not None:
        self.action(self.object, new_value)
    self._draw_graph()
```

**After**:
```python
def update_value(self, new_value: float):
    # Dialog just displays!
    self.param_value = new_value
    self._draw_graph_optimized()  # Only redraws changed portion
```

**Why this helps**:
- Dialog doesn't need quantizer reference anymore
- No more _find_nearest_element_index, actual_points, graph_points complexity
- Continuous graph view (not discrete 15-point jumps) - smoother visualization with 256 steps
- Optimized LCD refresh: only redraw the changed portion of the graph (not full panel)

---

### Architecture Diagram

```
CURRENT ARCHITECTURE (3 code paths):

┌─ MIDI Encoder ─────────────────────────────────────────┐
│ EncoderMidiControl.refresh(direction)                  │
│   → midi_value += (direction * 8)  [HARDCODED]         │
│   → send MIDI CC                                        │
│   → handler.parameter_midi_change(param, direction)    │
│       → lcd.draw_parameter_dialog(param)               │
│       → lcd.enc_step_widget(dialog, direction)         │
│           → dialog.parameter_value_change(direction)   │
│               → find nearest in 15 discrete points     │
│               → action(object, new_value)              │
└────────────────────────────────────────────────────────┘

┌─ Volume Encoder (v3) ──────────────────────────────────┐
│ Encoder.read_rotary() → handler callback               │
│   → handler.system_menu_headphone_volume(direction)    │
│       → audio_parameter_change(direction, ...)         │
│           → lcd.draw_audio_parameter_dialog(...)       │
│           → lcd.enc_step_widget(dialog, direction)     │
│               → dialog.parameter_value_change(dir)     │
│                   → find nearest in 15 discrete points │
│                   → action(symbol, new_value)          │
│                       → audiocard.set_volume(...)      │
└────────────────────────────────────────────────────────┘

┌─ Volume Encoder (v1/v2) ───────────────────────────────┐
│ Encoder.read_rotary() → handler callback               │
│   → handler.top_encoder_select(direction)              │
│       → [state machine check]                          │
│       → parameter_value_change(dir, callback)          │
│           → tweak = renormalize(8, ...)  [HARDCODED]   │
│           → new_value = value ± tweak                  │
│           → callback()                                 │
│           → lcd.draw_value_edit_graph(...)             │
└────────────────────────────────────────────────────────┘


PROPOSED ARCHITECTURE (1 code path):

┌─ ANY Encoder ──────────────────────────────────────────┐
│ Encoder.read_rotary() → EncoderController.refresh(dir) │
│   ├─ VelocityTracker.add_rotation(dir)                 │
│   │   → returns multiplier (1-16+) based on speed      │
│   ├─ ParameterQuantizer.move_steps(dir * multiplier)   │
│   │   → returns new_value from 256 discrete steps      │
│   ├─ Send MIDI CC (if midi_CC set)                     │
│   └─ handler.encoder_value_changed(param, new_value)   │
│       └─ lcd.display_parameter_value(param, new_value) │
│           └─ dialog.update_value(new_value)            │
│               └─ dialog._draw_graph()                  │
└────────────────────────────────────────────────────────┘

Note: Blend mode uses value_change_callback override
      → orthogonal, no special code in controller
```

---

## Why This is Easier to Maintain

### 1. Single Source of Truth

**Before**: 3 places with `= 8` magic number
**After**: 1 place (`ParameterQuantizer.NUM_STEPS = 256`)

Want to change step resolution? Change one number.

---

### 2. Unified Code Path

**Before**: 3 different algorithms for "encoder moved"
**After**: 1 algorithm (VelocityTracker → ParameterQuantizer → Handler)

New encoder type? Just create EncoderController, no special handler logic.

---

### 3. LCD Decoupling

**Before**: LCD calculates new values via ParameterDialog.parameter_value_change()
**After**: LCD only displays values, EncoderController calculates

Why this matters:
- Can test parameter logic without GUI
- Can replace LCD implementation without touching parameter code
- Clear responsibility: Controller = logic, Display = presentation

---

### 4. Testability

**Before**: To test encoder behavior, need Hardware + Handler + LCD + MIDI + Parameter
**After**: Can test each component independently:

```python
# Test velocity calculation
tracker = VelocityTracker()
assert tracker.add_rotation(1) == 1  # slow
time.sleep(0.05)
assert tracker.add_rotation(1) == 4  # faster

# Test quantization
quantizer = ParameterQuantizer(-32.0, 6.0)
quantizer.set_value(0.0)
assert quantizer.move_steps(10) ≈ 1.48  # specific step value

# No need for GPIO, LCD, MIDI to test this logic
```

---

### 5. Eliminates Duplicate Code

**Before**:
- `mod.py:1378-1393`: parameter_value_change using renormalize_float
- `parameterdialog.py:102-128`: parameter_value_change using discrete points
- Two different algorithms doing the same thing

**After**:
- One algorithm: ParameterQuantizer.move_steps()
- Both v1/v2 and v3 use same code

---

### 6. Volume Encoder Cleanup

**Before**:
- v3: Plain Encoder → handler.system_menu_headphone_volume → audio_parameter_change → LCD → ParameterDialog
- v1/v2: Plain Encoder → state machine → parameter_value_change → LCD

**After**:
- All: EncoderController → handler.encoder_value_changed → LCD
- Volume encoder is just EncoderController with midi_CC=None bound to audiocard parameter

---

## Implementation Plan

### Phase 1: Add New Components (Non-Breaking)

**Files to create**:
- `pistomp/velocity_tracker.py` - VelocityTracker class
- `pistomp/parameter_quantizer.py` - ParameterQuantizer class
- `pistomp/encoder_controller.py` - EncoderController class

**Testing**: Unit tests for each component independently

---

### Phase 2: Migrate v3 MIDI Encoders

**Files to modify**:
- `pistomp/pistomptre.py` - Use EncoderController instead of EncoderMidiControl
- `modalapi/modhandler.py` - Add encoder_value_changed(), keep old methods during migration
- `pistomp/lcd320x240.py` - Add display_parameter_value(), keep old methods

**Testing**: v3 hardware, tweak encoders with MIDI parameters

---

### Phase 3: Migrate v3 Volume Encoder

**Files to modify**:
- `pistomp/pistomptre.py` - Volume encoder uses EncoderController
- `modalapi/modhandler.py` - Volume uses encoder_value_changed() path
- Remove: system_menu_headphone_volume, audio_parameter_change

**Testing**: v3 hardware, volume knob

---

### Phase 4: Migrate v1/v2 Encoders

**Files to modify**:
- `pistomp/pistomp.py` - Use EncoderController for nav encoders
- `pistomp/pistompcore.py` - Use EncoderController for universal encoder
- `modalapi/mod.py` - Remove parameter_value_change, use encoder_value_changed()

**Testing**: v1/v2 hardware (if available)

---

### Phase 5: Cleanup

**Files to remove/modify**:
- Remove `pistomp/encodermidicontrol.py` (replaced by encoder_controller.py)
- Remove old handler methods: parameter_midi_change, audio_parameter_change
- Remove old LCD methods: enc_step_widget
- Simplify ParameterDialog to just update_value()

**Testing**: All hardware versions, all encoder types

---

## Design Clarifications

These clarifications resulted from initial review and investigation:

### 1. VelocityTracker Linear Regression Approach

**Implementation**: Fit a line through cumulative rotation count vs. time to calculate rotations/second.

- Collect rotation events with timestamps over 200ms sliding window
- Build arrays: `timestamps` and `cumulative_rotations` (absolute value to treat CW/CCW the same)
- Use `np.polyfit(time_deltas, cumulative_rotations, 1)` where time_deltas normalized to start at 0
- Slope of fitted line = rotations per second (velocity)
- Map velocity to multiplier with quadratic curve: `multiplier = int(velocity^2)`, clamped to [1, 32]

### 2. Controller Base Class

**Source**: `pistomp/controller.py` defines the `Controller` base class.

`EncoderController` inherits from both:
- `encoder.Encoder` - provides hardware quadrature decoding via GPIO interrupts
- `controller.Controller` - provides MIDI CC mapping, parameter binding interface

### 3. Blend Mode Interaction

**Behavior**: Same as current implementation, just with different value calculation.

When blend mode is active:
- `encoder.value_change_callback` is set to `blend_controller.handle_input`
- EncoderController still calculates new value using VelocityTracker + ParameterQuantizer
- Instead of calling `handler.encoder_value_changed()`, calls the callback
- Blend controller interpolates parameters, skips LCD update
- No special-case code needed in EncoderController - orthogonal via callback

### 4. LCD Update Frequency

**Clarification**: Update frequency doesn't change - we already send updates on every rotation.

The question about rate limiting was moot because:
- Current architecture: LCD updates on every encoder rotation event
- New architecture: LCD updates on every encoder rotation event (same frequency)
- Only difference: how we calculate the value, not when we send it
- Optimization is in *how* we redraw (partial vs full panel), not *when*

### 5. Testing Strategy for v1/v2

**Approach**: Careful refactoring + later manual testing.

- Phase 4 (v1/v2 migration) relies on code review and careful refactoring
- No v1/v2 hardware available for immediate testing
- Manual testing will occur later when hardware is accessible
- The unified architecture reduces risk - same code path as tested v3

### 6. Graph Visualization Changes

**Decision**: Change from discrete 15-point jumps to continuous graph.

**Rationale**:
- With 256 quantization steps, discrete jumps would look choppy
- Continuous line graph provides smoother visual feedback
- Matches the increased precision of the new system

**LCD Optimization**:
- Current: `panel.refresh()` redraws entire 320×170 or 320×64 panel (5Hz limit)
- Proposed: `widget.refresh()` redraws only changed portion of graph (much faster)
- Widget-level refresh capable of ~100Hz vs 5Hz for full panel
- Important for responsive feedback during fast encoder rotations
- Trade-off: CPU overhead vs visual responsiveness (worth it for parameter editing)

---

## Design Decisions

### 1. ParameterDialog Visual Style

**Decision**: Change to continuous graph visualization (replacing discrete 15-point jumps).

**Rationale**:
- With 256 quantization steps, discrete jumps would appear choppy and not reflect the precision
- Continuous line graph provides smoother visual feedback matching the increased resolution
- Enables LCD optimization - only redraw changed portion instead of full panel refresh
- Better user experience during fast encoder rotations

---

### 2. Blend Mode Integration

**Decision**: Keep current `value_change_callback` approach for now.

**Rationale**:
- The callback prevents the default action (LCD update) which is important for blend mode
- This architecture keeps blend mode orthogonal - no special-case code in EncoderController
- Potential improvements to blend mode integration should be a subsequent PR

---

### 3. Taper for Volume Parameters

**Decision**: Be consistent with current behavior - don't change taper.

**Rationale**:
- Keep scope focused on velocity tracking
- ParameterDialog already has taper support (parameterdialog.py:43)
- Current implementation will continue to work the same way
- If volume needs logarithmic taper, that's a separate enhancement

---

## Success Criteria

**Implemented:**
- [x] Single rotation = velocity-appropriate step size (slow=1, fast=8 max)
- [x] Can reach MIDI value 1, 127 precisely with slow rotations
- [x] Can sweep full range quickly with fast rotations
- [x] No hardcoded `per_click` or `parameter_tweak_amount` anywhere
- [x] LCD doesn't calculate values, only displays (via display_parameter_value)
- [x] v3 MIDI encoders use EncoderController
- [x] Blend mode still works (orthogonal via callback)
- [x] MIDI Learn works (encoders send MIDI when unbound)

**Deferred:**
- [ ] Volume encoders migrated to EncoderController
- [ ] v1/v2 encoders migrated to EncoderController
- [ ] ParameterDialog displays continuous graph (not discrete 15-point jumps)
- [ ] LCD refresh optimized - only redraws changed portion of graph (not full panel)

---

## Implementation Completed (January 2026)

### What Was Accomplished

**Phase 1: Core Components** ✅
- Created `VelocityTracker` with linear regression on rotation timing
- Created `ParameterQuantizer` with configurable step count (not hardcoded)
- Created `EncoderController` unifying encoder handling with velocity tracking

**Phase 2: v3 MIDI Encoder Migration** ✅
- Migrated `pistomptre.py` to use `EncoderController`
- Updated binding logic in `modhandler.py` to call `bind_to_parameter()` with taper
- Removed `EncoderMidiControl` entirely

**Handler & LCD Integration** ✅
- Added `modhandler.encoder_value_changed()` to coordinate LCD + parameter commit
- Added `lcd320x240.display_parameter_value()` for display-only updates
- Added `ParameterDialog.update_value()` with timeout timer reset

**Blend Mode Integration** ✅
- Updated `blend/input_controller.py` to support `EncoderController`
- Uses `get_normalized_value()` method for both bound and unbound states

### Key Implementation Changes from Original Plan

**1. Velocity Curve Tuning**
- **Planned**: Quadratic curve with max multiplier 32
- **Implemented**: Quadratic curve with max multiplier 8
- **Reason**: Testing showed 32 was too aggressive for MIDI's 0-127 range (full sweep in 4 clicks). With 8, slow=1 step, fast=8 steps, reasonable control.

**2. ParameterQuantizer Resolution**
- **Planned**: Hardcoded `NUM_STEPS = 256`
- **Implemented**: Configurable `num_steps` parameter in constructor
- **Reason**: MIDI encoders need 128 steps (1:1 with MIDI CC), non-MIDI encoders use 256 for higher precision
- **Usage**: `EncoderController.bind_to_parameter()` chooses: `num_steps = 128 if self.midi_CC else 256`

**3. MIDI Sending for Unbound Encoders**
- **Issue**: Original implementation only sent MIDI after binding, breaking MIDI Learn
- **Solution**: Added `midi_value` accumulator (like old `EncoderMidiControl`)
- **Behavior**: Always sends MIDI when rotated, whether bound to parameter or not
- **Why**: MIDI Learn requires detecting MIDI messages before binding can occur

**4. Multiple Inheritance Initialization**
- **Approach**: Used `super().__init__()` with all parameters passed through
- **Why**: `Encoder.__init__()` calls `super().__init__(**kw)` which chains to `Controller.__init__()`
- **Result**: Cooperative multiple inheritance works correctly by passing all args through chain

**5. EncoderMidiControl Removal**
- **Planned**: Keep during migration (Phase 5 cleanup)
- **Implemented**: Removed immediately after v3 migration
- **Reason**: No longer instantiated, only referenced in isinstance checks, cleaner to remove

### Testing Results

**Hardware Testing (v3 piStomp Tre)** ✅
- Encoders send MIDI CC 70/71 correctly
- MIDI Learn detects encoder rotations
- Velocity tracking responsive: slow=1 step, fast=8 steps
- Unbound encoders work (MIDI Learn use case)
- Bound encoders control parameters smoothly

**Backward Compatibility** ✅
- v1 (pistomp.py): Uses `lcdgfx`, no changes affect it
- v2 (pistompcore.py): Uses plain `Encoder`, new code paths not triggered
- Old code paths (`parameter_midi_change`, `enc_step_widget`) still functional

### Remaining Work

**Not Implemented:**
- Phase 3: Volume encoder migration (deferred)
- Phase 4: v1/v2 encoder migration (deferred) 
- Continuous graph visualization in ParameterDialog (deferred)
- LCD refresh optimization (partial redraw vs full panel) (deferred)

**Decision**: Focus on core velocity feature for v3 MIDI encoders. Other enhancements can be separate PRs.

### Files Modified

**Created:**
- `pistomp/velocity_tracker.py`
- `pistomp/parameter_quantizer.py`
- `pistomp/encoder_controller.py`

**Modified:**
- `pistomp/pistomptre.py` - use EncoderController instead of EncoderMidiControl
- `modalapi/modhandler.py` - encoder_value_changed(), binding logic
- `pistomp/lcd320x240.py` - display_parameter_value()
- `uilib/parameterdialog.py` - update_value(), _reset_timeout_timer()
- `blend/input_controller.py` - support EncoderController
- `CLAUDE.md` - added Contributing Code section

**Deleted:**
- `pistomp/encodermidicontrol.py`

### Lessons Learned

**1. Read existing code before designing**
- Multiple inheritance patterns already in place (Encoder + Controller)
- Should have understood `super()` chain before implementing

**2. Test incrementally**
- MIDI Learn failure caught early via logging
- Velocity tuning required hardware testing to get right

**3. Don't overcomplicate**
- Initial implementation coupled quantizer with MIDI sending unnecessarily
- Simpler to always send MIDI, optionally update parameters

**4. Configuration over constants**
- Making `num_steps` configurable was the right call
- Different use cases need different resolutions

**5. Type hints catch errors early**
- Prevented misuse of `Parameter.Type` (should be `Type`)
- Made API clearer (Optional[int] for midi_CC)
