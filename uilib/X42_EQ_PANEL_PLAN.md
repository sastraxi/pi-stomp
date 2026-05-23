# x42-eq Full-Screen Panel — Implementation Plan

Replace the generic parameter menu (longpress on a plugin tile) with a custom
full-screen panel for `http://gareus.org/oss/lv2/fil4#mono` (x42-eq mono).

## Goals

- Tweak the EQ live without persisting changes to the pedalboard bundle.
- Visual: black background with a real-time frequency-response curve and a
  selectable circular "node" per band.
- Use the same `Panel`/`pstack` pattern as the tuner.

## Interaction Model — flat Nav list, sticky-on-band

Nav (rotation) cycles **11 targets**:

```
HP → LS → B1 → B2 → B3 → B4 → HS → LP → Bypass → Back → Reset → (wraps)
```

| Nav position           | Tweak1 (gain) | Tweak2 (freq) | Tweak3 (Q) | Nav shortpress                  | Nav longpress                    |
|------------------------|---------------|---------------|------------|---------------------------------|----------------------------------|
| Band: HP               | (inert)       | `HPfreq` log  | `HPQ`      | toggle `HighPass`               | reset HP to pedalboard-saved     |
| Band: LP               | (inert)       | `LPfreq` log  | `LPQ`      | toggle `LowPass`                | reset LP to pedalboard-saved     |
| Band: LS               | `LSgain` ±18  | `LSfreq` log  | `LSq`      | toggle `LSsec`                  | reset LS to pedalboard-saved     |
| Band: HS               | `HSgain` ±18  | `HSfreq` log  | `HSq`      | toggle `HSsec`                  | reset HS to pedalboard-saved     |
| Band: B1–B4            | `gainN` ±18   | `freqN` log   | `qN`       | toggle `secN`                   | reset BN to pedalboard-saved     |
| Bypass                 | —             | —             | —          | toggle plugin `enable`          | —                                |
| Back                   | —             | —             | —          | close panel                     | —                                |
| Reset                  | —             | —             | —          | reset **all** to plugin defaults| —                                |

Notes:
- HP/LP have no gain — Tweak1 inert there.
- "Reset to pedalboard-saved" uses the parameter values captured at panel-open
  (snapshot of the LILV-parsed defaults for that pedalboard).
- "Reset all" uses each parameter's plugin-default (`.default` from LILV).

## Visual Layout (320×240)

```
┌────────────────────────────────────────────────┐
│ HP   freq: 80 Hz   Q: 0.71   gain: —           │  ← readout row (current Nav target)
├────────────────────────────────────────────────┤
│ +18dB  ·  ·  ·  ·  ·  ·  ·  ·  ·  ·  ·  ·  ·  │  ← graph area
│        ·  ·  ·  ·  ·  ·  ·  ·  ·  ·  ·  ·  ·  │
│        ·     ╱╲ ·  ·     ·     ·     ·     ·  │
│ ───●───·────╱──╲────●────●──────────●──────── │  ← centre 0 dB
│        ·   ●     ╲ ╱  ·     ·     ·     ·     │
│        ·  ·  ·  ·  V·  ·  ·  ·  ·  ·  ·  ·    │
│ -18dB  ·  ·  ·  ·  ·  ·  ·  ·  ·  ·  ·  ·  ·  │
│                                                │
│              [Bypass]   [Back]   [Reset]       │  ← chrome (selectable via Nav)
└────────────────────────────────────────────────┘
```

- **No band strip row** — each band is selected by Nav landing on its
  circular node on the graph itself; the node's halo is the only
  selection affordance needed.
- **Readout row at top** (~y=0–18): single line showing the currently
  Nav-targeted band/control's name + current values. For chrome
  buttons (Bypass / Back / Reset) the readout shows a short hint.
- Graph area roughly y=20–200 (180 px tall), full width 320. More room
  now that the strip is gone.
- Chrome buttons (Bypass / Back / Reset) sit in a row at the bottom,
  ~y=210–235, selectable by Nav like everything else.
- Vertical scale: ±18 dB matches param ranges; clip at ±24 for safety.
- Horizontal scale: log-spaced 20 Hz – 20 kHz across 320 columns.
- Each band drawn as a **filled circle, diameter 4** at its
  `(freq, gain)` point on the curve. When selected by Nav: add a
  **diameter-8 outline circle** around it.
- HP/LP nodes pinned to y=0 (no gain axis); selection ring still draws.

## Background grid

Dimmed grid lines drawn once at panel open into a background layer:

- **Vertical (frequency)** lines at log-doubling intervals across 20 Hz – 20 kHz:
  `20, 40, 80, 160, 320, 640, 1280 (~1k), 2560 (~2k), 5120 (~5k), 10240 (~10k), 20000`.
  Even spacing on a log axis. (Final values may snap to `20/50/100/200/500/1k/2k/5k/10k/20k`
  per the x42-eq research — to be confirmed in step 1.)
- **Horizontal (dB)** lines at `±18, ±12, ±6, 0` dB. 0 dB drawn slightly brighter.
- Color: dim grey (e.g. `#222` or `#1a1a1a`) on black background.

**Background helper**: a pure function `bg_color(x, y) -> rgb` returns the
correct background color for any graph pixel — accounting for grid lines —
without needing a backing buffer. The redraw path uses this whenever it
**erases** a pixel (curve pixel that moved off, node old position, selection
ring), so the grid stays intact after surgical updates.

```python
def bg_color(x: int, y: int) -> tuple[int,int,int]:
    if y in DB_GRID_ROWS:     return GRID_0DB if y == ZERO_DB_ROW else GRID_DIM
    if x in FREQ_GRID_COLS:   return GRID_DIM
    return BG_BLACK
```

`DB_GRID_ROWS` and `FREQ_GRID_COLS` are precomputed `set[int]` for O(1)
lookup. The curve draw uses this same helper for every pixel it touches.

## Curve Math — fil4 is NOT standard RBJ

For the on-device curve to match what the user actually hears, we must
use **fil4's exact filter topologies**, not the textbook RBJ cookbook.
Each filter type has its own quirks (cited at `github.com/x42/fil4.lv2`):

- **Cascade order** (`src/lv2.c`): input → global `gain` → HP → LP → LS →
  sec1 → sec2 → sec3 → sec4 → HS. Series cascade, so total magnitude
  response = **sum in dB** of each enabled stage. Global `gain` is a flat
  dB offset.
- **Peaking bands (sec1–4)** — Fons Adriaensen's *paramsect* (`src/filters.h:55–77`),
  not RBJ. The bandwidth scaling is:
  ```
  s1 = -cos(2π·f);              # f = freq/fs
  a  = 0.5 · (g - 1);           # g = linear gain
  b *= 7 · f / sqrt(g);         # <-- proportional-Q, narrows as |gain| rises
  s2 = (1 - b) / (1 + b);
  ```
  Port `Q` feeds `b` — so a fixed port-Q produces a bandwidth that
  **depends on both freq and gain** (constant-area behavior — symmetric
  cut/boost). Do NOT substitute RBJ Q here.
- **Shelves (LS / HS)** — RBJ-style (`src/iir.h`) but port-Q is remapped
  before use (`src/lv2.c`): `q = 0.2129 + port_Q / 2.25`. Use RBJ shelf
  formulas with `A = sqrt(linear_gain)`, `α = sin(ω0) / (2·q_remapped)`.
- **HP** — custom 1-pole cascade with feedback (`src/hip.h`), NOT
  Butterworth/RBJ. ~12 dB/oct. Q remap: `q2 = 0.7 + 0.78·tanh(1.82·(Q-0.8))`
  clamped to [0, 1.6]; `α = exp(-2π·f/fs)`; gain comp `g = 1 + ω + 2q·ω`.
- **LP** — 4-pole cascade (`src/lop.h`), ~24 dB/oct. Resonance remap:
  `fb = 3·Q^3.20772` clamped to [0, 9]. Cutoff is pre-warped:
  `fs_eff = freq / sqrt(1 + fb)`. Includes a fixed corrective high-shelf at
  `fs/3, q=0.444, gain=0.5` (`LP_EXTRA_SHELF`) — include this in the curve.
- **dB↔linear**: `linear = 10^(dB/20)`.

**Recommended implementation** (`pistomp/eq/curve.py`):

No FFT, no impulse response, no scipy. For each enabled stage, derive its
transfer function `H(z) = B(z) / A(z)` once from the formulas above
(small rational in `z^-1` — biquad-shaped for shelves/peaks, one-pole
products for HP/LP), then **evaluate `|H(e^jω)|` analytically** at the
320 graph frequencies using vectorized numpy complex arithmetic:

```python
w  = 2 * np.pi * freqs_hz / fs       # shape (320,)
z1 = np.exp(-1j * w)                  # z^-1
z2 = z1 * z1                          # z^-2
H  = (b0 + b1*z1 + b2*z2) / (1.0 + a1*z1 + a2*z2)   # shape (320,)
mag_db = 20 * np.log10(np.abs(H) + 1e-12)
```

That's ~10 µs per stage on a Pi. Sum dB across enabled stages; add the
flat global `gain` dB offset. Assume `fs = 48000`. Cache per-stage `mag_db`
arrays keyed by (stage_id, params_tuple) so only the changed band
recomputes on each encoder tick.

The work is the **algebra** — for each topology (paramsect, RBJ shelf,
custom HP, custom LP+extra-shelf), expand its difference equation into
`(b0, b1, b2, a1, a2)` coefficients (or a product of such, for the
multi-pole HP/LP). Unit-test each against a few known points (e.g.
peaking at center freq with `gain=12` should give +12 dB at `freq`).

Verify against fil4 in MOD-Desktop: load the same pedalboard, take a
screenshot of its native graph, overlay ours, confirm peak locations and
shelf knees match to within a few dB.

## Live vs. Persisted

- All edits go through `blend/parameter_setter.py::ParameterSetter` →
  `modalapi/websocket_bridge.py::send_parameter()` (already proven).
- This is `param_set` over WS to mod-host: **runtime only**, not saved.
- No "Persist" button in v1 — user can manually Save in MOD-UI if they want
  to keep changes. (Add later if needed.)

## Entry Point

Replace the generic parameter-menu longpress for this specific plugin only:

- `pistomp/lcd320x240.py:463` — `plugin_event(event, widget, plugin)` on
  `LONG_CLICK` currently calls `self.draw_parameter_menu(plugin)`.
- Branch: if `plugin.plugin_dict.get('uri') == 'http://gareus.org/oss/lv2/fil4#mono'`
  (and `#stereo` later), call `self.show_eq_panel(plugin)` instead.
- All other plugins keep the existing generic menu.

## Files to Add / Touch

**New:**
- `pistomp/eq/__init__.py`
- `pistomp/eq/panel.py` — `EqPanel(Panel)` (full-screen, owns widgets)
- `pistomp/eq/curve.py` — biquad magnitude math (pure numpy, no I/O)
- `pistomp/eq/bands.py` — band descriptor table: name → {gain_sym, freq_sym,
  q_sym, enable_sym, gain_range, freq_range, q_range, has_gain}

**Modify:**
- `pistomp/lcd320x240.py`:
  - Add `show_eq_panel(plugin)` / `hide_eq_panel()` — mirror tuner's
    `show_tuner_panel` / `hide_tuner_panel` at lines 242–253.
  - Tick hook in `poll_updates()` (mirror line 200–201) so the curve can
    redraw between input events if needed.
  - Branch in `plugin_event` LONG_CLICK at line 463 on plugin URI.
- `modalapi/modhandler.py`:
  - (Optional) add `toggle_eq_enable(plugin)` analogous to
    `toggle_tuner_enable` at line 1205 if we want a callback name path too
    — not required for the longpress entry point.

**Reuse unchanged:**
- `blend/parameter_setter.py` — de-duped param sends.
- `modalapi/websocket_bridge.py` — WS transport.
- `uilib/` widget primitives.

## Build Order (smallest verifiable steps)

1. **`pistomp/eq/bands.py`** — pure data table, no deps. Unit-testable.
2. **`pistomp/eq/curve.py`** — biquad magnitude in dB, given band states.
   Unit test: gain-of-0 / Q-of-0.707 peaking band at 1 kHz → ~0 dB at 100 Hz
   and 10 kHz, +0 dB at 1 kHz with `gain=0`, etc.
3. **`pistomp/eq/panel.py`** — static panel: black bg, axes, all-bands node
   layout, curve draw on open. No input yet. Show via temporary debug entry.
   Snapshot test.
4. **Input wiring** — Nav rotation moves selection; selection ring on node /
   chrome highlight. Tweak1-3 fire `ParameterSetter.send_parameter`.
5. **Live curve update** — on each param change, recompute, diff, surgically
   redraw changed columns + the moved node + its selection ring.
6. **Nav shortpress** — per-target actions (toggle enable, close, reset-all).
7. **Nav longpress** — per-band reset to pedalboard-saved snapshot.
8. **Entry-point branch** in `plugin_event` for the x42-eq URI.
9. **Stereo variant** — once mono works, add `#stereo` to URI branch and
   verify symbols match.

## Open Questions / Risks

- **SPI bandwidth**: the diff-and-surgical-redraw is the mitigation. If a
  single tweak changes ~50 of 320 columns and we paint 2 px per column,
  that's 100 pixel ops per encoder tick — should be fine, but worth
  measuring with the tuner's partial-refresh tooling.
- **Selection ring repaint**: when Nav moves, erase the old ring (redraw the
  4 px under it from the cached curve column data) and draw the new ring.
  Avoid full-panel refresh.
- **`peakreset`**: ignored in v1 — it's a momentary control, not visualised.
- **`gain` (master)**: ignored in v1 — could be Tweak1 when Nav is on
  Bypass, but skip for now to keep the model clean.
- **Sample rate for curve math**: assume 48 kHz; visually negligible for
  display purposes. Revisit only if curve looks wrong vs. MOD-UI.
