# Parametric EQ Panel — Implementation Plan

## Goal

Re-use the x42 EQ panel's frequency-response curve visualization for all parametric EQ plugins in the system, while giving graphic EQs a traditional horizontal-bar UI.

## Design Decisions

### Two distinct panel types

| Panel | Bands | Visualization | BandSpec |
|-------|-------|---------------|----------|
| `ParametricEqPanel` | freq/Q/gain per band | Frequency-response curve (reused from x42) | `BandSpec` |
| `GraphicEqPanel` | gain-only, fixed freq | Horizontal list of vertical bars | `GraphicBandSpec` |

### `BandSpec` — tight, parametric-only

Every parametric EQ exposes the same control set per band, but individual
plugins omit some controls (fil4 HP/LP have no gain; distrho L/H and
ZamEQ2 L/H have no Q; ZamEQ2 and TAP EQ have no per-band enable). Omitted
controls use `None` (never an empty string) and the panel guards on
`is None` before touching them.

```python
@dataclass(frozen=True)
class BandSpec:
    name: str              # display label ("HP", "B1", "LS", ...)
    kind: BandKind         # "peak" | "shelf" | "hp" | "lp"
    enable_sym: str | None # LV2 symbol toggled by short-press; None = no per-band enable
    freq_sym: str          # LV2 symbol (Tweak2) — always present
    q_sym: str | None      # LV2 symbol (Tweak3); None = no Q (shelves, TAP EQ)
    gain_sym: str | None   # LV2 symbol (Tweak1); None = no gain (fil4 HP/LP)
    shelf_side: Literal["low", "high"] | None  # None for non-shelf bands
    freq_min: float
    freq_max: float
    q_min: float
    q_max: float
    gain_min: float = -18.0
    gain_max: float = 18.0
    color: tuple[int, int, int] = (255, 255, 255)  # RGB, for the curve node
```

`shelf_side` is authoritative per-subclass: `_stage_db` selects low vs high
shelf via this field rather than inspecting `name` (which previously
assumed an "L"-prefix convention). The fil4 subclass sets `"low"` for LS
and `"high"` for HS; distrho sets `"low"`/`"high"` for L/H; ZamEQ2 likewise.

### GraphicBandSpec — gain-only, fixed frequency

```python
@dataclass(frozen=True)
class GraphicBandSpec:
    name: str              # display label ("100Hz", "200Hz", ...)
    freq_hz: float         # fixed frequency position
    gain_sym: str          # LV2 symbol (only tweakable parameter)
    gain_min: float = -18.0
    gain_max: float = 18.0
    color: tuple[int, int, int] = (255, 255, 255)  # RGB, for the bar fill
```

### Graphic EQ visualization — traditional bar layout

```
┌─────────────────────────────────────────────────┐
│  20Hz    50Hz    100Hz    200Hz    ...    16kHz  │  ← freq labels (top)
│  ████  ████  ██████  ████  ...    ████           │
│  ████  ████  ██████  ████  ...    ████           │  ← vertical bars
│                                                     │
│                                                     │
│                                                     │
│                                                     │
│                                                     │
│                                                     │
│                                                     │
│                                                     │
│                                                     │
│                                                     │
│                                                     │
│                                                     │
│                                                     │
│                                                     │
│                                                     │
├─────────────────────────────────────────────────┤
│  B3: +6.2 dB                                      │  ← readout
└─────────────────────────────────────────────────┘
```

- Nav encoder cycles between bands (left to right)
- Tweak1 adjusts the selected band's gain
- Tweak2/3 are absorbed (no-op)
- Frequency labels at top, dB value at bottom
- No short-press action on graphic bands (CLICK returns False / no-op)
- Long-press still resets the selected band to the pedalboard snapshot
- Selected band highlighted (white halo)
- Bar fill uses `band.color` at reduced alpha; selected band gets full saturation
- `BarWidget` (new) renders the bars, freq labels, and dB readout — it does
  **not** reuse `GraphWidget` (the parametric curve renderer)

### Graphic EQ colour scheme

Each graphic EQ plugin gets a consistent colour palette derived from its band count and frequency range. The palette wraps through a warm-to-cool hue gradient (red → yellow → green → cyan → blue → magenta) so adjacent bands are visually distinct:

| Plugin | Bands | Palette strategy |
|--------|-------|------------------|
| caps-Eq10 | 10 | Full hue sweep (0°–300°), 10 stops |
| gx_graphiceq | 10 | Full hue sweep (0°–300°), 10 stops |
| gx_barkgraphiceq | 24 | Full hue sweep (0°–300°), 24 stops |
| ZamGEQ31 | 29 | Full hue sweep (0°–300°), 29 stops |

A helper function `_graphic_palette(n: int) -> list[tuple[int,int,int]]` generates the list from HSV interpolation. This lives in `plugins/eq/graphic.py`.

### Shared components

| Component | File | Reused by |
|-----------|------|-----------|
| `GraphWidget` (curve + grid + nodes) | `plugins/eq/panel.py` | `ParametricEqPanel` |
| `BarWidget` (vertical bars + freq/dB labels) | `plugins/eq/graphic.py` | `GraphicEqPanel` |
| `ReadoutWidget` (top-bar text) | `plugins/eq/panel.py` | `ParametricEqPanel` |
| `BandSelectable` (nav target) | `plugins/eq/panel.py` | `ParametricEqPanel` and `GraphicEqPanel` (each has its own subclass) |
| `PluginPanel` (chrome, bypass, reset, coalesce) | `plugins/base.py` | Both |
| `InputSink` (LCD dispatch) | `pistomp/input/sink.py` | Both |

### Curve computation

- **Parametric EQs:** Existing RBJ biquad math in `plugins/eq/curve.py` is generic. Refactor `CurveCache.compute(bands, state)` to accept a `Sequence[BandSpec]` argument instead of iterating the module-level `BANDS` tuple. `_stage_db(band, params)` accepts a `BandSpec`-like object and selects low/high shelf via `band.shelf_side` (not `band.name.startswith("L")`).
- **Graphic EQs:** No curve computation — `BarWidget` maps each band's gain directly to bar height. There is no `compute_graphic_curve()`.

### Plugin registrations

Each EQ plugin registers its own panel class via `plugins/customization.py`. The two abstract panel types are subclassed per-plugin; the concrete subclass provides `build_band_specs()` returning the plugin's `BandSpec`/`GraphicBandSpec` list.

| Plugin | URI | Panel Class | BandSpec Source |
|--------|-----|-------------|-----------------|
| x42-eq | `http://gareus.org/oss/lv2/fil4#mono` | `Fil4Panel` (concrete subclass of `ParametricEqPanel`) | `plugins/fil4/band_spec.py` |
| x42-eq | `http://gareus.org/oss/lv2/fil4#stereo` | `Fil4Panel` (concrete subclass of `ParametricEqPanel`) | `plugins/fil4/band_spec.py` |
| DISTRHO | `urn:distrho:a-eq` | `DistrhoAEqPanel` (`ParametricEqPanel`) | `plugins/distaq/band_spec.py` |
| ZamEQ2 | `urn:zamaudio:ZamEQ2` | `ZamEQ2Panel` (`ParametricEqPanel`) | `plugins/zameq2/band_spec.py` |
| TAP EQ | `http://moddevices.com/plugins/tap/eq` | `TapEqPanel` (`ParametricEqPanel`) | `plugins/tapeq/band_spec.py` |
| TAP EQ/BW | `http://moddevices.com/plugins/tap/eqbw` | `TapEqBwPanel` (`ParametricEqPanel`) | `plugins/tapeqbw/band_spec.py` |
| caps-Eq10 | `http://moddevices.com/plugins/caps/Eq10` | `CapsEq10Panel` (`GraphicEqPanel`) | `plugins/capseq10/band_spec.py` |
| gx-graphiceq | `http://guitarix.sourceforge.net/plugins/gx_graphiceq_#_graphiceq_` | `GxGraphicEqPanel` (`GraphicEqPanel`) | `plugins/graphiceq/band_spec.py` |
| gx-barkgraphiceq | `http://guitarix.sourceforge.net/plugins/gx_barkgraphiceq_#_barkgraphiceq_` | `GxBarkGraphicEqPanel` (`GraphicEqPanel`) | `plugins/barkgraphiceq/band_spec.py` |
| ZamGEQ31 | `urn:zamaudio:ZamGEQ31` | `ZamGEQ31Panel` (`GraphicEqPanel`) | `plugins/zamgeq31/band_spec.py` |

## File Tree

```
plugins/
├── eq/                        # shared EQ abstractions (no plugin-specific code)
│   ├── __init__.py            # unchanged
│   ├── panel.py               # REFACTOR — extract ParametricEqPanel (ABC) + BandSelectable; fil4-specific code moves to plugins/fil4/
│   ├── band_spec.py           # NEW — BandSpec + BandKind + GraphicBandSpec dataclasses (shared types)
│   ├── curve.py               # REFACTOR — CurveCache.compute() accepts Sequence[BandSpec]; shelf side via shelf_side
│   └── graphic.py             # NEW — GraphicEqPanel (ABC) + BarWidget + _graphic_palette()
├── fil4/                      # NEW directory (x42-eq / fil4 plugin-specific code)
│   ├── __init__.py            # NEW — registers Fil4Panel for fil4 mono+stereo URIs; moves FIL4_URIS constants here
│   ├── band_spec.py           # NEW — BAND_SPECS list of BandSpec for fil4 (moved out of eq/bands.py)
│   └── panel.py               # NEW — Fil4Panel (concrete subclass of ParametricEqPanel); fil4-specific snapshot/readout logic
├── distaq/                   # NEW directory
│   ├── __init__.py           # NEW — registers DistrhoAEqPanel for urn:distrho:a-eq
│   └── band_spec.py          # NEW — BandSpec for distrho-a-eq (6 bands: L, 1-4, H)
├── zameq2/                   # NEW directory
│   ├── __init__.py           # NEW — registers ZamEQ2Panel for urn:zamaudio:ZamEQ2
│   └── band_spec.py          # NEW — BandSpec for ZamEQ2 (4 bands: L, 1, 2, H)
├── tapeq/                    # NEW directory
│   ├── __init__.py           # NEW — registers TapEqPanel for tap-eq
│   └── band_spec.py          # NEW — BandSpec for tap-eq (8 bands, freq + gain)
├── tapeqbw/                  # NEW directory
│   ├── __init__.py           # NEW — registers TapEqBwPanel for tap-eqbw
│   └── band_spec.py          # NEW — BandSpec for tap-eqbw (8 bands, freq + gain + bw)
├── zamgeq31/                 # NEW directory
│   ├── __init__.py           # NEW — registers ZamGEQ31Panel for urn:zamaudio:ZamGEQ31
│   └── band_spec.py          # NEW — GraphicBandSpec for ZamGEQ31 (29 bands)
├── capseq10/                 # NEW directory
│   ├── __init__.py           # NEW — registers CapsEq10Panel for caps-Eq10
│   └── band_spec.py          # NEW — GraphicBandSpec for caps-Eq10 (10 bands)
├── graphiceq/                # NEW directory
│   ├── __init__.py           # NEW — registers GxGraphicEqPanel for gx_graphiceq
│   └── band_spec.py          # NEW — GraphicBandSpec for gx_graphiceq (10 bands)
├── barkgraphiceq/            # NEW directory
│   ├── __init__.py           # NEW — registers GxBarkGraphicEqPanel for gx_barkgraphiceq
│   └── band_spec.py          # NEW — GraphicBandSpec for gx_barkgraphiceq (24 bands)
├── __init__.py               # REFACTOR — add import lines for 8 plugin packages (fil4 + 7 new)
└── customization.py          # UNCHANGED — PluginCustomization registry
```

## Implementation Order

### Phase 1: Core — BandSpec + ParametricEqPanel (ABC) + Fil4Panel
1. Create `plugins/eq/band_spec.py` with `BandSpec`, `BandKind`, `GraphicBandSpec` (frozen dataclasses; `BandSpec` uses `Optional[str]` for `enable_sym`/`q_sym`/`gain_sym` with `None` meaning "this band has no such control"; `shelf_side: Literal["low","high"] | None` for shelf side; `color: tuple[int,int,int]`)
2. Move fil4 band data to `plugins/fil4/band_spec.py` — replace the module-level `BANDS` tuple (currently in `plugins/eq/bands.py`) with `BAND_SPECS` list of `BandSpec`, carrying `BAND_COLORS` into each spec's `color` field, setting `shelf_side` for each shelf band (fil4 LS=`"low"`, HS=`"high"`). Delete `plugins/eq/bands.py`.
3. Refactor `plugins/eq/panel.py`:
   - Extract the existing fil4-specific `EqPanel` into a concrete `Fil4Panel` (new file `plugins/fil4/panel.py`) that provides `build_band_specs()` returning `BAND_SPECS`
   - What remains in `plugins/eq/panel.py` is the abstract `ParametricEqPanel(PluginPanel[EqState])` — generic over `BandSpec`-driven panels: it owns `GraphWidget`, `ReadoutWidget`, `BandSelectable`, and the chrome; subclasses only supply band specs
   - Make `_BandSelectable` → `BandSelectable` (public)
   - `ParametricEqPanel.snapshot_state()` reads symbols from `self.bands` (the subclass-provided `BandSpec` list); guards `is None` for missing `enable_sym`/`q_sym`/`gain_sym`
   - `ParametricEqPanel.on_encoder_rotation()` uses band specs for symbol lookup; guards `is None` for `gain_sym` (consume-but-noop) and `q_sym` (consume-but-noop)
   - `ParametricEqPanel._band_readout_fields()` uses band specs
   - `ParametricEqPanel.build_widgets()` iterates `self.bands` for nav targets
   - Add `build_band_specs() -> Sequence[BandSpec]` abstract method (subclasses override)
   - `_on_band_click`: guard `if band.enable_sym is None: return` before `set_param`
   - `_on_band_long`: skip `enable_sym`/`q_sym` in the reset loop when `None`
4. Create `plugins/fil4/__init__.py` — register `Fil4Panel` for `FIL4_MONO_URI` and `FIL4_STEREO_URI` (move `FIL4_URIS` constants here from `plugins/eq/__init__.py`)
5. Refactor `plugins/eq/curve.py`:
   - `CurveCache.compute(bands: Sequence[BandSpec], state: EqState)` — iterate passed bands instead of module-level `BANDS`
   - `_stage_db(band: BandSpec, params: BandParams)` — select low/high shelf via `band.shelf_side` (not `band.name.startswith("L")`)
6. Write unit tests for the generic `ParametricEqPanel` contract (band spec validation, `None`-guard behavior) — no snapshot tests against a synthetic plugin; the existing `tests/v3/test_eq_panel.py` already snapshot-tests `Fil4Panel` as the parametric exemplar
7. Update `tests/v3/test_eq_panel.py` — import `Fil4Panel` from `plugins/fil4.panel` instead of `EqPanel` from `plugins.eq.panel`; the `make_fil4_plugin` helper already guards `if b.gain_sym is not None` which is correct for the `None` sentinel

### Phase 2: GraphicEqPanel (ABC) + BarWidget
8. Create `plugins/eq/graphic.py` with:
   - `_graphic_palette(n)` — HSV hue-sweep helper
   - `BarWidget(Widget)` — new vertical-bar visualization (does **not** reuse `GraphWidget`):
     - Bars positioned by `freq_hz` on the log x-axis, height mapped from gain within `[gain_min, gain_max]`
     - Frequency labels along the top, dB readout along the bottom
     - Selected band gets a white halo; unselected bands render at reduced alpha
   - `GraphicEqState` — separate state class with `bands: dict[str, GraphicBandParams]` (gain-only, no curve)
   - `GraphicEqPanel(PluginPanel[GraphicEqState])` (ABC):
     - Owns `BarWidget` + readout + chrome
     - `GraphicBandSelectable` for nav targets (CLICK returns False / no-op; LONG_CLICK resets the band)
     - `on_encoder_rotation()` only adjusts gain (Tweak1); Tweak2/3 absorbed (consume-but-noop)
     - `build_band_specs() -> Sequence[GraphicBandSpec]` abstract method
9. Add snapshot tests for `CapsEq10Panel` (the graphic EQ exemplar) — covers gain adjustment, nav cycling, bypass/reset, and exercises the `BarWidget` rendering path

### Phase 3: Plugin-specific band specs
10. Create `plugins/distaq/` — distrho-a-eq band spec + `DistrhoAEqPanel` registration
11. Create `plugins/zameq2/` — ZamEQ2 band spec + `ZamEQ2Panel` registration
12. Create `plugins/tapeq/` — tap-eq band spec + `TapEqPanel` registration
13. Create `plugins/tapeqbw/` — tap-eqbw band spec + `TapEqBwPanel` registration
14. Create `plugins/capseq10/` — caps-Eq10 graphic band spec + `CapsEq10Panel` registration
15. Create `plugins/graphiceq/` — gx_graphiceq graphic band spec + `GxGraphicEqPanel` registration
16. Create `plugins/barkgraphiceq/` — gx_barkgraphiceq graphic band spec + `GxBarkGraphicEqPanel` registration
17. Create `plugins/zamgeq31/` — ZamGEQ31 graphic band spec + `ZamGEQ31Panel` registration

### Phase 4: Wiring + tests
18. Update `plugins/__init__.py` — add `import` lines for fil4 + the 7 new plugin packages (8 total)
19. Add snapshot tests for `CapsEq10Panel` (graphic EQ exemplar, alongside the existing `Fil4Panel` tests for parametric)
20. Add tests for each new band spec (validate symbols, ranges, band count, `shelf_side`)
21. Run full test suite: `uv run pytest`

## Key Code Changes

### `plugins/eq/band_spec.py` (NEW — shared types)
```python
from dataclasses import dataclass
from typing import Literal

BandKind = Literal["peak", "shelf", "hp", "lp"]


@dataclass(frozen=True)
class BandSpec:
    name: str
    kind: BandKind
    enable_sym: str | None   # None = no per-band enable
    freq_sym: str
    q_sym: str | None         # None = no Q control
    gain_sym: str | None      # None = no gain (fil4 HP/LP)
    shelf_side: Literal["low", "high"] | None  # None for non-shelf bands
    freq_min: float
    freq_max: float
    q_min: float
    q_max: float
    gain_min: float = -18.0
    gain_max: float = 18.0
    color: tuple[int, int, int] = (255, 255, 255)


@dataclass(frozen=True)
class GraphicBandSpec:
    name: str
    freq_hz: float
    gain_sym: str
    gain_min: float = -18.0
    gain_max: float = 18.0
    color: tuple[int, int, int] = (255, 255, 255)
```

### `plugins/fil4/band_spec.py` (NEW — fil4-specific band data, moved out of `plugins/eq/bands.py`)
```python
from plugins.eq.band_spec import BandSpec

BAND_SPECS: tuple[BandSpec, ...] = (
    BandSpec("HP", "hp", "HighPass", "HPfreq", "HPQ", None, None,
             20.0, 1250.0, 0.0, 1.4, color=(255, 110, 110)),
    BandSpec("LS", "shelf", "LSsec", "LSfreq", "LSq", "LSgain", "low",
             25.0, 400.0, 0.0625, 4.0, color=(255, 180, 80)),
    # ... etc, carrying over existing BAND_COLORS into color
)
```

### `plugins/eq/panel.py` (REFACTOR — extract fil4-specific code)
- What remains is the abstract `ParametricEqPanel(PluginPanel[EqState])` — generic over `BandSpec`-driven panels
- Owns `GraphWidget`, `ReadoutWidget`, `BandSelectable`, chrome; subclasses supply band specs via `build_band_specs()`
- `snapshot_state()`: iterate `self.bands`, read params via band spec symbols; guard `is None` for missing `enable_sym`/`q_sym`/`gain_sym`
- `on_encoder_rotation()`: symbol lookup via band spec; Tweak1 guards `gain_sym is None` (consume-but-noop); Tweak3 guards `q_sym is None` (consume-but-noop)
- `_band_readout_fields()`: read from band spec, no gain for HP/LP (already handled via `gain_sym is None`)
- `build_widgets()`: iterate `self.bands` for nav targets
- Add `build_band_specs() -> Sequence[BandSpec]` abstract method (subclasses override)
- `_on_band_click`: guard `if band.enable_sym is None: return` before `set_param`
- `_on_band_long`: skip `enable_sym`/`q_sym` in the reset loop when `None`
- `_BandSelectable` → `BandSelectable` (public)

### `plugins/fil4/panel.py` (NEW — concrete fil4 panel)
- `Fil4Panel(ParametricEqPanel)` — provides `build_band_specs()` returning `BAND_SPECS` from `plugins/fil4/band_spec.py`
- Any fil4-specific snapshot/readout overrides (if needed) live here

### `plugins/fil4/__init__.py` (NEW — registration + URI constants)
```python
from plugins.customization import PluginCustomization, register
from plugins.fil4.panel import Fil4Panel

FIL4_MONO_URI = "http://gareus.org/oss/lv2/fil4#mono"
FIL4_STEREO_URI = "http://gareus.org/oss/lv2/fil4#stereo"
FIL4_URIS = (FIL4_MONO_URI, FIL4_STEREO_URI)

register(*FIL4_URIS, customization=PluginCustomization(panel_cls=Fil4Panel))
```

### `plugins/eq/curve.py` (REFACTOR)
- `CurveCache.compute(bands: Sequence[BandSpec], state: EqState)` — iterate passed bands instead of module-level `BANDS`
- `_stage_db(band: BandSpec, params: BandParams)` — select low/high shelf via `band.shelf_side` (not `band.name.startswith("L")`)

### `plugins/eq/graphic.py` (NEW)
```python
class BarWidget(Widget):
    """Vertical-bar visualization for graphic EQs.
    Bars positioned by freq_hz on a log x-axis; height mapped from gain
    within [gain_min, gain_max]. Frequency labels along the top, dB readout
    along the bottom. Does NOT reuse GraphWidget (the parametric curve renderer).
    """
    ...

class GraphicEqState:
    """State for graphic EQ panels — gain per band, no curve."""
    bands: dict[str, GraphicBandParams]  # keyed by GraphicBandSpec.name

class GraphicEqPanel(PluginPanel[GraphicEqState]):
    """Abstract base for graphic EQ panels. Subclasses provide
    build_band_specs() -> Sequence[GraphicBandSpec]."""
    # Owns BarWidget + readout + chrome
    # GraphicBandSelectable for nav targets
    #   - CLICK: no-op (returns False)
    #   - LONG_CLICK: reset selected band to pedalboard snapshot
    # on_encoder_rotation(): Tweak1 = gain; Tweak2/3 = consume-but-noop
    # _graphic_palette(n) for auto-colouring
```

### `plugins/__init__.py` (REFACTOR)
```python
import plugins.eq.panel       # noqa: F401  # ParametricEqPanel (ABC) + GraphWidget etc.
import plugins.eq.graphic     # noqa: F401  # GraphicEqPanel (ABC) + BarWidget
import plugins.fil4           # noqa: F401
import plugins.nam            # noqa: F401
import plugins.notes.panel    # noqa: F401
import plugins.distaq         # noqa: F401
import plugins.zameq2         # noqa: F401
import plugins.tapeq          # noqa: F401
import plugins.tapeqbw        # noqa: F401
import plugins.capseq10       # noqa: F401
import plugins.graphiceq      # noqa: F401
import plugins.barkgraphiceq  # noqa: F401
import plugins.zamgeq31       # noqa: F401
```

### Registration pattern (each plugin package)
```python
# plugins/distaq/__init__.py
from plugins.distaq.band_spec import DISTAQ_BAND_SPECS
from plugins.distaq.panel import DistrhoAEqPanel
from plugins.customization import PluginCustomization, register

register(
    "urn:distrho:a-eq",
    customization=PluginCustomization(
        panel_cls=DistrhoAEqPanel,
        display_name="DISTRHO Audio EQ",
    )
)
```

## Graphic EQ Port Mappings

### caps-Eq10 (10 bands, octave)
| Band | Symbol | Freq | Range |
|------|--------|------|-------|
| 31 Hz | `band31hz` | 31 | -48..+24 |
| 63 Hz | `band63hz` | 63 | -48..+24 |
| 125 Hz | `band125hz` | 125 | -48..+24 |
| 250 Hz | `band250hz` | 250 | -48..+24 |
| 500 Hz | `band500hz` | 500 | -48..+24 |
| 1 kHz | `band1khz` | 1000 | -48..+24 |
| 2 kHz | `band2khz` | 2000 | -48..+24 |
| 4 kHz | `band4khz` | 4000 | -48..+24 |
| 8 kHz | `band8khz` | 8000 | -48..+24 |
| 16 kHz | `band16khz` | 16000 | -48..+24 |

### gx_graphiceq (10 bands, ISO)
| Band | Symbol | Freq | Range |
|------|--------|------|-------|
| 31 Hz | `G10` | 31 | -30..+20 |
| 63 Hz | `G11` | 63 | -30..+20 |
| 125 Hz | `G1` | 125 | -30..+20 |
| 250 Hz | `G2` | 250 | -30..+20 |
| 500 Hz | `G3` | 500 | -30..+20 |
| 1 kHz | `G4` | 1000 | -30..+20 |
| 2 kHz | `G5` | 2000 | -30..+20 |
| 4 kHz | `G6` | 4000 | -30..+20 |
| 8 kHz | `G7` | 8000 | -30..+20 |
| 16 kHz | `G8` | 16000 | -30..+20 |

Note: gx_graphiceq exposes 11 input gain ports (`G10`, `G11`, `G1`-`G9`). The band spec keeps 10 bands (31 Hz-16 kHz) covering `G10, G11, G1-G8` and omits `G9` (20 kHz, above the 20 kHz display range). The port naming is non-sequential: `G10`=31 Hz, `G11`=63 Hz, `G1`=125 Hz, …, `G8`=16 kHz, `G9`=20 kHz.

### gx_barkgraphiceq (24 bands, Bark scale)
| Band | Symbol | Freq (approx) | Range |
|------|--------|---------------|-------|
| G1-G24 | `G1`..`G24` | 50..13500 Hz (Bark) | -30..+20 |

Bark-scale frequencies (approximate, from Bark scale literature — the TTL manifest declares only gain ports `G1`-`G24` with no frequency metadata; the center frequencies are a convention of the Bark scale): 50, 150, 250, 350, 450, 570, 700, 840, 1000, 1170, 1370, 1600, 1850, 2150, 2500, 2900, 3400, 4000, 4800, 5800, 7000, 8500, 10500, 13500 Hz.

### ZamGEQ31 (29 bands, 1/3-octave ISO)
| Band | Symbol | Freq | Range |
|------|--------|------|-------|
| 32Hz | `band1` | 32 | -12..+12 |
| 40Hz | `band2` | 40 | -12..+12 |
| 50Hz | `band3` | 50 | -12..+12 |
| 63Hz | `band4` | 63 | -12..+12 |
| 79Hz | `band5` | 79 | -12..+12 |
| 100Hz | `band6` | 100 | -12..+12 |
| 126Hz | `band7` | 126 | -12..+12 |
| 158Hz | `band8` | 158 | -12..+12 |
| 200Hz | `band9` | 200 | -12..+12 |
| 251Hz | `band10` | 251 | -12..+12 |
| 316Hz | `band11` | 316 | -12..+12 |
| 398Hz | `band12` | 398 | -12..+12 |
| 501Hz | `band13` | 501 | -12..+12 |
| 631Hz | `band14` | 631 | -12..+12 |
| 794Hz | `band15` | 794 | -12..+12 |
| 999Hz | `band16` | 999 | -12..+12 |
| 1257Hz | `band17` | 1257 | -12..+12 |
| 1584Hz | `band18` | 1584 | -12..+12 |
| 1997Hz | `band19` | 1997 | -12..+12 |
| 2514Hz | `band20` | 2514 | -12..+12 |
| 3165Hz | `band21` | 3165 | -12..+12 |
| 3986Hz | `band22` | 3986 | -12..+12 |
| 5017Hz | `band23` | 5017 | -12..+12 |
| 6318Hz | `band24` | 6318 | -12..+12 |
| 7963Hz | `band25` | 7963 | -12..+12 |
| 10032Hz | `band26` | 10032 | -12..+12 |
| 12662Hz | `band27` | 12662 | -12..+12 |
| 16081Hz | `band28` | 16081 | -12..+12 |
| 20801Hz | `band29` | 20801 | -12..+12 |

Also has a `master` gain port (-30..+30 dB) — not a band, handled as a global control.

The 29th band (20801 Hz) sits above the 20 kHz display edge; `BarWidget` clamps it to the rightmost column. No special handling needed.

## Parametric EQ Port Mappings

### distrho-a-eq (6 bands: L, 1-4, H)
| Band | Kind | enable_sym | freq_sym | q_sym | gain_sym | Freq range | Q range | Gain range |
|------|------|-----------|----------|-------|----------|------------|---------|------------|
| L | shelf | `filtogl` | `freql` | — | `gl` | 20-20000 | — | -20..+20 |
| 1 | peak | `filtog1` | `freq1` | `bw1` | `g1` | 20-20000 | 0.1-4.0 | -20..+20 |
| 2 | peak | `filtog2` | `freq2` | `bw2` | `g2` | 20-20000 | 0.1-4.0 | -20..+20 |
| 3 | peak | `filtog3` | `freq3` | `bw3` | `g3` | 20-20000 | 0.1-4.0 | -20..+20 |
| 4 | peak | `filtog4` | `freq4` | `bw4` | `g4` | 20-20000 | 0.1-4.0 | -20..+20 |
| H | shelf | `filtogh` | `freqh` | — | `gh` | 20-20000 | — | -20..+20 |

Note: L and H are shelving filters with no Q control. The panel should absorb Tweak3 for these bands. Also has `master` and `enable` global ports.

### ZamEQ2 (4 bands: L, 1, 2, H)
| Band | Kind | enable_sym | freq_sym | q_sym | gain_sym | Freq range | Q range | Gain range |
|------|------|-----------|----------|-------|----------|------------|---------|------------|
| L | shelf | — | `fl` | — | `boostl` | 20-14000 | — | -50..+20 |
| 1 | peak | — | `f1` | `bw1` | `boost1` | 20-14000 | 0.1-6.0 | -50..+20 |
| 2 | peak | — | `f2` | `bw2` | `boost2` | 20-14000 | 0.1-6.0 | -50..+20 |
| H | shelf | — | `fh` | — | `boosth` | 20-14000 | — | -50..+20 |

Note: ZamEQ2 has no per-band enable toggles. Has `master` (-12..+12) and `peaks` (toggle) global ports.

### TAP EQ (8 bands, parametric)
| Band | Kind | enable_sym | freq_sym | q_sym | gain_sym | Freq range | Gain range |
|------|------|-----------|----------|-------|----------|------------|------------|
| 1-8 | peak | — | `Band{1-8}FreqHz` | — | `Band{1-8}GainDb` | varies | -50..+20 |

Note: TAP EQ has no per-band enable toggles and no Q control. Freq ranges per band: 40-280, 100-500, 200-1000, 400-2800, 1000-5000, 3000-9000, 6000-18000, 10000-20000 Hz.

### TAP EQ/BW (8 bands, parametric with bandwidth)
Same as TAP EQ but with `Band{1-8}BandwidthOctaves` (0.1-5.0) as Q control.

## Risks & Mitigations

| Risk | Mitigation |
|------|-----------|
| HP/LP bands have no gain_sym — encoder Tweak1 needs to detect missing symbol | `gain_sym: str \| None`; panel checks `is None`, consumes-but-noops |
| Graphic EQ visualization differs from parametric (bars vs curve) | Separate `BarWidget` in `plugins/eq/graphic.py`; no `GraphWidget` reuse |
| distrho-a-eq uses a non-obvious URI (`urn:distrho:a-eq`) | Verified against `lv2plugins.tar.gz` manifest.ttl |
| ZamEQ2 / ZamGEQ31 URIs need the `urn:` prefix | Verified: `urn:zamaudio:ZamEQ2`, `urn:zamaudio:ZamGEQ31` |
| Some parametric EQs lack per-band enable toggles (ZamEQ2, TAP EQ) | `enable_sym: str \| None`; `_on_band_click` guards `is None` |
| Some parametric EQs lack Q control (distrho L/H, TAP EQ, ZamEQ2 L/H) | `q_sym: str \| None`; `on_encoder_rotation` Tweak3 guards `is None` (consume-but-noop) |
| Shelf low/high selection was coupled to band name prefix | New `shelf_side: Literal["low","high"] \| None` field on `BandSpec`; `_stage_db` reads it directly |
| gx_graphiceq port naming is non-sequential (G10=31Hz, G11=63Hz, G1=125Hz) | BandSpec maps logical names to actual symbols; the spec table documents the mapping |
| Existing fil4 tests break after refactor | `tests/v3/test_eq_panel.py` imports `Fil4Panel` from `plugins.fil4.panel`; `make_fil4_plugin` already guards `gain_sym is None` |
| gx_barkgraphiceq band frequencies are not in the LV2 manifest | Frequencies sourced from Bark scale literature; band_spec.py documents the provenance |

## Testing Strategy

Snapshot tests cover the two exemplar panels — `Fil4Panel` (parametric) and `CapsEq10Panel` (graphic) — which exercise the `ParametricEqPanel` and `GraphicEqPanel` ABCs plus their `GraphWidget`/`BarWidget` rendering. The other plugin panels share these code paths; their band specs get unit-tested for symbol/range correctness but do not get snapshot tests.

- **Existing `tests/v3/test_eq_panel.py` (parametric exemplar):** Update imports — `Fil4Panel` now lives at `plugins.fil4.panel`, band data at `plugins.fil4.band_spec`. The `make_fil4_plugin` helper already guards `if b.gain_sym is not None` (correct for the `None` sentinel); its imports of `BANDS`/`BAND_COLORS`/`PLUGIN_ENABLE_SYM` change to `BAND_SPECS` from the new location.
- **New `tests/v3/test_caps_eq10_panel.py` (graphic exemplar):** Snapshot sagas for gain adjustment, nav cycling, bypass/reset. Builds a synthetic caps-Eq10 plugin fixture analogous to `make_fil4_plugin`.
- **New unit tests for each band spec (all plugins):** Validate symbol names, ranges, band count, and (for parametric) `shelf_side` correctness.
- **Integration tests:** Load each EQ plugin via REST, open panel, verify rendering.
