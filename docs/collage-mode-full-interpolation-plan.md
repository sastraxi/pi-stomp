# Full Interpolation Mode Implementation Plan

## Architecture

**Two interpolation modes:**

1. **Segment Mode** (current) - Segment-based remapping + easing within segments
   - **EasingFunc**: `Callable[[float], float]` - Transforms local percentage within segment
   - Functions: `linear`, `ease-in`, `ease-out`, `ease-in-out`, `exponential`, `sine`
   - Single CC mapped per segment with easing applied to interpolation
   - Efficient, no extra MIDI traffic

2. **Parameter Mode** (new) - Full interpolation across all stops
   - **InterpolationFunc**: `Callable[[float, list[CollageStop]], SnapshotStateDict]` - Computes complete state
   - Functions: `linear`, `hermite`, `catmull_rom`
   - Virtual MIDI CCs (one per parameter)
   - Dedicated MIDI channel required

## Function Signatures

**EasingFunc (Segment Mode):**
```python
EasingFunc = Callable[[float], float]

# Input: t ∈ [0,1] - local position within current segment
# Output: eased_t ∈ [0,1] - transformed position

ease_in_quad(0.5) -> 0.25  # Slow start, fast finish
```

**InterpolationFunc (Parameter Mode):**
```python
InterpolationFunc = Callable[[float, list[CollageStop]], SnapshotStateDict]

# Input: percentage ∈ [0,1] - global position across all stops
#        stops - list of all CollageStop objects
# Output: Complete interpolated state for all parameters

hermite_interpolation(0.5, stops) -> {
  "/BigMuffPi": {"Tone": 0.45, "Level": 0.62},
  "/Reverb": {"Mix": 0.33}
}
```

## Config Schema

```yaml
collage_mode:
  mode: segment              # segment | parameter

  # Segment mode options
  easing: linear            # linear | ease-in | ease-out | ease-in-out | exponential

  # Parameter mode options
  interpolation: hermite    # linear | hermite | catmull_rom
  virtual_midi_channel: 15  # Required for parameter mode

  expression_pedal_id: 0
  stops: [...]
```

## Implementation Tree

```
Phase 1: Segment Mode Easing Functions
├── collagestop.py
│   ├── EasingFunc = Callable[[float], float]
│   ├── linear_easing(t) -> t
│   ├── ease_in_quad(t) -> t²
│   ├── ease_out_quad(t) -> 1-(1-t)²
│   ├── ease_in_out_quad(t) -> piecewise
│   ├── ease_in_cubic(t) -> t³
│   ├── ease_out_cubic(t) -> 1-(1-t)³
│   ├── ease_in_out_cubic(t) -> piecewise
│   ├── exponential_easing(t) -> 2^(10(t-1))
│   └── sine_easing(t) -> sin((t*π)/2)
├── collagemode.py
│   ├── Add easing_func: EasingFunc attribute
│   ├── Modify apply_midi_mappings():
│   │   └── Apply easing to local_pct before computing min/max
│   └── Map config easing name to function

Phase 2: Parameter Mode Infrastructure
├── collagemode.py
│   ├── Add mode: 'segment' | 'parameter' attribute
│   ├── Add interpolation_func: InterpolationFunc attribute
│   ├── Add virtual_cc_mappings: dict[str, int] attribute
│   └── Modify initialize():
│       ├── If mode == 'parameter':
│       │   ├── Validate virtual_midi_channel exists
│       │   ├── Assign virtual CC number to each parameter
│       │   ├── Send initial midi_map for virtual CCs
│       │   └── Always hijack pedal (even for 2-stop)
│       └── Else: use segment mode (current behavior)

Phase 3: Virtual MIDI CC Generation
├── collagemode.py
│   ├── send_virtual_midi_cc(cc_num, value)
│   │   └── Use handler.hardware.send_midi_cc()
│   └── Modify hijacked_refresh():
│       ├── If mode == 'parameter':
│       │   ├── percentage = cc_value / 127.0
│       │   ├── state = self.interpolation_func(percentage, self.stops)
│       │   ├── For each param:
│       │   │   ├── Get virtual_cc_num
│       │   │   ├── Scale value to 0-127
│       │   │   └── send_virtual_midi_cc(virtual_cc_num, scaled_value)
│       └── Else: segment mode logic (current)

Phase 4: Config Validation & Defaults
├── collagemode.py
│   └── Add validate_config():
│       ├── If mode == 'parameter':
│       │   └── Require virtual_midi_channel
│       ├── If mode == 'segment':
│       │   └── Validate easing name
│       └── Set defaults
```

## Critical Integration Points

**Segment Mode - EasingFunc Usage:**
```python
# EasingFunc transforms local percentage within segment
# Used in apply_midi_mappings() when computing segment min/max

def apply_midi_mappings(self, segment_index):
    lower, upper = self.stops[segment_index], self.stops[segment_index + 1]

    # For a given pedal position within segment:
    local_pct = (percentage - lower.position) / segment_range
    eased_pct = self.easing_func(local_pct)  # Transform: [0,1] -> [0,1]

    # Still use single CC, just with eased interpolation
    value = val_a + (val_b - val_a) * eased_pct
    midi_map(instance, symbol, channel, cc, val_a, val_b)  # mod-host still interpolates
```

**Parameter Mode - InterpolationFunc Usage:**
```python
# InterpolationFunc computes complete state across all stops
# Used in hijacked_refresh() to generate virtual MIDI CCs

# During init - assign unique CC per parameter:
virtual_cc_mappings = {
  "/BigMuffPi:Tone": 70,
  "/BigMuffPi:Level": 71,
  ...
}

# In hijacked_refresh():
percentage = cc_value / 127.0
state = self.interpolation_func(percentage, self.stops)  # Computes all parameter values

for instance_id, params in state.items():
    for symbol, value in params.items():
        key = f"{instance_id}:{symbol}"
        cc_num = virtual_cc_mappings[key]
        send_midi_cc(channel=15, cc=cc_num, value=int(value * 127))
```

## Files Modified

- `modalapi/collagestop.py` - Add easing functions
- `modalapi/collagemode.py` - Mode selection, virtual CC logic
- `setup/config_templates/*.yml` - Add mode/easing/interpolation examples

## Estimated Complexity

- Phase 1 (Easing): ~2 hours
- Phase 2-3 (Parameter mode): ~4 hours
- Phase 4 (Config): ~1 hour

Total: ~7 hours
