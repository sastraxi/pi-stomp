# Blend mode: new name for collage mode

## New config format and naming, multiple collages + other input support

We get rid of `enabled` because collage mode is enabled exactly when
a "Blend Snapshot" is activated as the current snapshot. Because
collage mode snapshots _are_ snapshots, we need a name for this noun
in particular. Let's remove "collage" and instead make it clear what
this functionality does: blending between snapshots. We'll call it
"Blend mode".

We'll create *multiple* blend snapshots (previously just one snapshot
we created called "Collage Mode") and each of them will enable collage
mode with a different.

We use `input_id` rather than `expression_pedal_id` to capture the
idea that Tweak1/Tweak2 could also be used for blending between
snapshots, if we desire: this works becuase the IDs for expression
pedals and rotary encoders are in the same space (non-overlapping).

No matter which input is chosen, we'll need to make sure that any
parameters in the MOD graph (of the pedalboard) that are controlling
MIDI parameters are not interpolated between the stops; they need to
be fully controlled by that other input.

```yaml
blend_snapshots:
  - name: Clean→Fuzz
    input_id: 0
    interpolation: ease_out_quad
    stops:
      "0.0": "Cleanish",
      "0.5": "Hairy",
      "1.0": "Full Fuzz"
  - input_id: 0
    name: Volume
    stops: ["Cleanish", "Loudish"]  # stops follow space_between [0..1]
```

## What's Been Accomplished

1. Core Refactoring
  - Renamed collage/ → blend/ directory
  - Renamed all classes: CollageMode → BlendMode, CollageStop → BlendStop, PedalController → InputController
  - Updated all imports throughout the codebase
2. Type System
  - Added proper types: BlendSnapshotConfig, PedalboardBlendConfig, NormalizedStops, MidiBoundParams, BlendInputProtocol
3. Multiple Blend Snapshots
  - Support for multiple blend snapshots per pedalboard (not just one)
  - Each blend snapshot has its own name, input, interpolation, and stops
  - Auto-switches to FIRST blend snapshot on pedalboard load ✅
4. Analog Input Support
  - Supports both expression pedals AND tweak encoders via input_id
  - Clean protocol-based abstraction (BlendInputProtocol)
  - Encoder callback override: when active, skips regular display update (with XXX comment for future LCD work)
5. Smart Features
  - MIDI-bound parameters automatically excluded from interpolation
  - List stops syntax: ["A", "B", "C"] auto-spaces to {"0.0": "A", "0.5": "B", "1.0": "C"}
  - Proper type definitions for all data interchange formats
6. Handler Updates
  - modhandler.py (v3) - Multiple blend modes with activation/deactivation
  - mod.py (v1/v2) - Same updates for older hardware
7. Documentation
  - Updated GUIDE.md with comprehensive blend mode documentation
  - Updated config template (default_config_pistomptre.yml) with examples

## Next steps

1. Update Beths pedalboard with new config shape
2. Re-deploy modified code and delete old collage/ folder on device
3. Test on the device
4. `git sync -s` and fix merge conflicts with lower branches

