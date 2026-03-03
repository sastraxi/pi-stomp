# Guide for piStomp Development

## Contributing Code

**Code Style**:
- Terse, minimal comments - docstrings for public methods only
- Comment only when surprising or justifying an approach
- Type hints everywhere - use actual types, not `Any`
- No `TYPE_CHECKING` unless there's an actual circular import

**Architecture**:
- No unnecessary "defensive programming" - fail fast!
- No premature branches - don't handle cases that don't exist yet
- Question complexity - "why do we need this branch?" should have a clear answer
- Read existing code before writing - understand patterns first

**Development**:
- Phase changes - don't try to do everything at once
- Migrate incrementally - one subsystem at a time
- Type system catches errors at development time, not runtime

## Remote Development

**SSH Access**: `ssh pistomp@pistomp.local`

## Service Management

```bash
# Restart piStomp service
sudo systemctl restart mod-ala-pi-stomp

# View logs (live)
sudo journalctl -u mod-ala-pi-stomp -f

# View recent logs
sudo journalctl -u mod-ala-pi-stomp -n 50
```

### Startup Order

Services start in dependency chain: `jack → mod-host → mod-ui → mod-ala-pi-stomp`

- piStomp service has `After=mod-ui.service` + `Requires=mod-ui.service`
- REST API has graceful fallback if mod-ui not ready (loads default pedalboard)
- WebSocket retries with exponential backoff (1s → 30s max)

### Initial Snapshot State

- piStomp defaults to snapshot 0 at startup
- Syncs correctly if pedalboard loads after piStomp starts (via WebSocket `loading_end` event)
- If piStomp restarts while MOD-UI already running: assumes snapshot 0 until next change

## Deployment Workflow

```bash
# 1. Edit files locally in /Users/cam/dev/pi-stomp/
# 2. Copy Python files to device
scp modalapi/*.py pistomp@pistomp.local:/home/pistomp/pi-stomp/modalapi/

# 3. Restart the service (Python takes care of re-creating __pycache__ files)
ssh pistomp@pistomp.local "sudo systemctl restart mod-ala-pi-stomp"
```

## Key Data Paths

### Application
- **Code**: `/home/pistomp/pi-stomp/`
- **Service**: `/lib/systemd/system/mod-ala-pi-stomp.service`
- **System Binaries**: `/usr/local/bin/{mod-host,mod-ui,jackd}`

### User Data
- **Root**: `/home/pistomp/data/`
- **Config**: `/home/pistomp/data/config/`
- **Pedalboards**: `/home/pistomp/data/.pedalboards/`
- **User Files**: `/home/pistomp/data/user-files/` (samples, SFZ/SF2 instruments, IRs)
- **LV2 Plugins**: `/home/pistomp/.lv2/` (scanned by mod-host)

### State Files
- **Last Pedalboard**: `/home/pistomp/data/last.json` (auto-loads on startup - clear to prevent crashes)
- **Banks**: `/home/pistomp/data/banks.json` (pedalboard grouping)
- **Settings**: `/home/pistomp/data/config/settings.yml` (system settings)
- **ALSA State**: `/var/lib/alsa/asound.state` (audio mixer levels)

## Testing Changes

```bash
# Test pedalboard switch via API
curl -X POST http://localhost:80/pedalboard/load_bundle/ \
  -d 'bundlepath=/home/pistomp/data/.pedalboards/AmpBud.pedalboard'

# List pedalboards
curl -s http://localhost:80/pedalboard/list | python3 -m json.tool
```

## Hardware Versions

- **v1/v2**: Uses `modalapi/mod.py`
- **v3**: Uses `modalapi/modhandler.py` (current device)

## Python Environment

- Service runs as `root` with Python 3.11
- Uses unbuffered mode (`python3 -u`) for proper logging
- Dependencies installed system-wide via `pip3`

## MIDI Routing Architecture

### Single MIDI Source Design

piStomp uses **ALSA MIDI Through port 14** for all MIDI routing:

```
Hardware Controls (Footswitches, Rotary Encoders, Expression Pedals)
    ↓
ALSA MIDI Through (port 14:0)
    ↓
JACK (bridges via `-X seq`)
    ↓
    ├─→ mod-host:midi_in (MIDI Learn for parameter control)
    │   Auto-connected in separated mode via PortRegistration callback
    │
    └─→ Available in MOD-UI for manual wiring
        ↓
        LV2 MIDI plugins (CC Map, Channel Map, Filter, etc.)
        ↓
        External MIDI Devices (C4, HX Stomp, etc.)
```


### Which Controls Send MIDI?

- ✅ Expression Pedal (CC 75) - sends to virtual port
- ✅ Footswitches (CC 60-63) - send to virtual port when pressed
- ✅ Rotary Encoder Rotation (Tweak1=CC70, Tweak2=CC71) - send to virtual port
- ✅ Encoder Button Presses - Can optionally send MIDI via `shortpress` config (see below)

### Encoder Button Configuration (v3 only)

Encoder buttons support configurable `shortpress` callbacks:

```yaml
encoders:
  - id: 1
    midi_CC: 70  # Rotation
    longpress: previous_snapshot
    shortpress: universal_encoder_sw  # Default if omitted

  - id: 2
    midi_CC: 71
    shortpress:
      callback: send_midi_cc
      args: {cc: 72}  # Button sends CC 72 to virtual port
```

Shortpress accepts string (callback name) or object with `callback` and `args` (expanded as kwargs).

#### Implementation Details

| Control | Shortpress | Longpress |
|---------|------------|-----------|
| Encoder | String or `{callback, args}` via `encoderconfig.parse_shortpress_config()` | String only (no args) |
| Footswitch | Hardcoded (toggle/MIDI) - no config | String or list (group names) - no args |
| `GpioSwitch` | `callback_arg` (dict→kwargs, value→arg, None) | `longpress_callback_arg` (dict→kwargs, value→arg, None) |
| `AnalogSwitch` | Single `callback(state)` - no separate longpress | Same callback, state=LONGPRESSED |

### External Device Sync

- Pedalboard load triggers MIDI messages to external devices (e.g., Source Audio C4)
- Configured via `hardware.external_midi` in default config and per-pedalboard config.yml
- **UI Integration**: routed controls get synthetic `Parameter` objects (INTEGER, 0-127) for LCD feedback.
- See `setup/config_templates/default_config_pistomptre.yml` for example configuration

### Analog Control State Sync

- On pedalboard load, all analog controls (expression pedals, etc.) send current position to MIDI Through
- MIDI flows to MIDI Through port 14:0 → available to LV2 MIDI plugins in pedalboard
- Prevents state mismatch - no need to wiggle pedals after switching pedalboards
- Implemented via `Hardware.sync_analog_controls()` → `AnalogMidiControl.send_current_value()`
- Works for both v1/v2 (`mod.py`) and v3 (`modhandler.py`) hardware

## Key Development Principles

### Hardware-First Design
- **Polling over events** - Fixed-frequency loops for predictable timing (10ms critical path)
- **Direct hardware access** - No HAL layer, direct SPI/GPIO/MIDI interaction
- **Real-time constraints** - Never block in critical path, separate frequencies by priority
- **Hardware reality drives architecture** - Embrace limitations (ADC polling, SPI timing)
- **ADC endpoint clamping** - Analog controls clamp ADC values within `tolerance` of 0 or 1023 to exact endpoints, ensuring expression pedals always reach 0 and 127. Without this, the tolerance deadband prevents the final small movement from triggering.

### Version Handling
- **Explicit version routing** - Factory pattern with known version checks, not capability detection
- **Shared base class** - Common functionality in `Hardware`, version-specific in subclasses
- **No breaking changes** - New features extend, don't replace (v1/v2/v3 all supported)

### Configuration Philosophy
- **Overlay, don't replace** - Pedalboard config merges with defaults at field level
- **Minimal config files** - Users specify only what changes from default
- **Config-driven behavior** - Callbacks by name, extensible without code changes
- **Safe defaults always** - Missing config keys use sensible defaults

### State Management
- **Incremental updates** - `reinit()` pattern updates objects in-place, no recreation
- **Shared class state where needed** - Footswitch groups coordinate via class-level dicts
- **Explicit state machines** - Encoder modes (v1/v2) use clear state enums
- **Event-driven sync** - WebSocket messages for real-time pedalboard/snapshot changes

### MOD Integration
- **Direct REST calls** - No SDK abstraction, just `requests` to `localhost:80`
- **WebSocket events** - Typed protocol (`ws_protocol.py`) for real-time change detection
- **LILV for local parsing** - Parse `.ttl` bundles locally for performance and rich data
- **Trust MOD for audio** - piStomp is controller interface, not audio processor
- **Unbounded WebSocket queue** - Never drop parameter messages. Blend mode can produce 9+ params per 10ms tick; a bounded queue causes silent message loss and "stuck" parameters.
- **MOD-UI designated ports** - mod-ui rejects `param_set` for plugin bypass/enable ports (`BYPASS`, `Bypass`, `PluginEnabled`, `enable`). These are "designated" ports managed by mod-ui internally. Blend mode must not send these via WebSocket `param_set`.

### Code Organization
- **Factories for versioning** - `Handlerfactory` and `Hardwarefactory` route versions
- **Handlers = business logic** - `mod.py`/`modhandler.py` orchestrate system
- **Hardware = physical** - Hardware classes only talk to GPIO/SPI/ADC
- **Callbacks for extensibility** - Handler methods exposed by name in config

### MIDI Architecture
- **Single MIDI sink** - All hardware controls send to ALSA MIDI Through port 14:0
- **Direct routing** - Hardware controls → MIDI Through → JACK → mod-host
- **Lazy port initialization** - External MIDI ports opened on first use
- **Sync on pedalboard load** - Send analog positions + external MIDI messages

### Development Guidelines
- **Pragmatic over perfect** - Simple solutions over complex abstractions
- **Explicit over implicit** - Clear code paths, minimal magic
- **Configuration over compilation** - Users customize via YAML, not Python
- **Fail gracefully** - Log warnings, continue operation where possible
- **Consider log volume before adding logging in a loop** - A single `logging.warning()` in a 10ms loop produces 100 messages/second. In a 200ms loop, 5/second. The strategy we use to tackle this while still providing observability is context-dependent.
- **Logs answer "what happened?", code answers "what can happen?"** - Check logs first to understand the problem, then shift to reading code when designing solutions. Staying in the logs too long during solutioning leads to chasing symptoms instead of fixing causes.

### When Extending
- **New hardware version?** Add factory branch, inherit from `Hardware`
- **New footswitch action?** Add handler method, reference by name in config
- **New config field?** Add to TypedDict, handle in `reinit()` or `update_config()`
- **New MIDI routing?** Modify `MidiOut` or `ExternalMidiManager`
- **Performance issue?** Check polling loop frequency first

## System Architecture

### Entry Point & Main Loop

**`modalapistomp.py`** - System initialization and polling loop

```python
# Startup sequence
1. Parse CLI args (log level, host type)
2. Initialize audio card (early for audio pass-through)
3. Create MIDI output to ALSA MIDI Through port 14:0
4. Create handler (Mod or Modhandler) via Handlerfactory
5. Create hardware (Pistomp/Core/Tre) via Hardwarefactory with midiout
6. Load pedalboards from MOD API (parsed via LILV)
7. Load current pedalboard and initialize hardware
```

**Polling Loop (Different Frequencies)**:
- `10ms`: `poll_controls()` - Read hardware inputs (critical path)
- `20ms`: `poll_indicators()` - Update LEDs/VU meters
- `200ms`: `poll_lcd_updates()` - Render LCD
- `1000ms`: `poll_modui_changes()` - Sync with MOD UI (WebSocket messages, banks.json, blend snapshots.json)
- `2000ms`: `poll_wifi()` - Update WiFi status
- `60s`: `poll_system_info()` - System health (CPU, throttling)

### Hardware Version Selection

**Factory Pattern** routes version-specific implementations:

```python
# Handlerfactory (business logic)
< 2.0     → Mod (v1)
>= 2.0    → Modhandler (v2/v3)

# Hardwarefactory (physical interface)
< 2.0     → Pistomp (v1: dual encoders, 3 switches, mono LCD)
>= 2.0 < 3.0 → Pistompcore (v2: single encoder, color LCD, relay)
>= 3.0    → Pistomptre (v3: 4 encoders, LED strip, VU meters)
```

**All inherit from `Hardware` base class** - provides common functionality:
- `reinit(cfg)` - Reload config on pedalboard change
- `poll_controls()` - Read all inputs
- `sync_analog_controls()` - Send current positions on pedalboard load
- SPI/ADC communication
- Controller dictionary: `{channel:CC}` → controller object

### Configuration System

**Two-Layer Config Overlay**:

```
Default Config (global)
  ↓ loaded at startup
Hardware objects created
  ↓ pedalboard load
Pedalboard Config (overlay)
  ↓ hardware.reinit(cfg)
Config merged and applied
```

**Config Files**:
- Global: `/home/pistomp/data/config/default_config.yml` (or built-in templates)
- Per-pedalboard: `{pedalboard}.pedalboard/config.yml`

**Overlay Strategy**: Pedalboard config overrides only specified fields
- Example: Change footswitch MIDI CC for specific pedalboard
- Fields not specified keep default values

### Parameter Persistence

**Plugin Parameters** (`common/parameter.py`):
- Source: LV2 TTL files in pedalboard bundles
- Persisted: MOD-UI manages via pedalboard state (snapshots)
- Read/Write: REST API (`/effect/parameter/pi_stomp_set`)
- Never persisted by piStomp - delegated to MOD

**Audio Parameters** (volume, input gain, EQ):
- Source: ALSA mixer controls (audiocard-specific)
- Persisted: `/var/lib/alsa/asound.state` (automatic via ALSA)
- Read: `audiocard.get_volume_parameter(symbol)` → `amixer sget`
- Write: `audiocard.set_volume_parameter(symbol, value)` → `amixer sset`
- Symbols: `MASTER`, `CAPTURE_VOLUME`, `EQ_1`-`EQ_5` (defined per card)

**System Settings** (VU calibration, bank selection):
- Persisted: `/home/pistomp/data/config/settings.yml`
- Read/Write: `settings.get_setting(key)` / `set_setting(key, value)`

### MOD Integration

**HTTP REST API** to `localhost:80`:

```bash
# Pedalboard operations
GET  /pedalboard/list                    # List all pedalboards
GET  /pedalboard/current                 # Get current pedalboard bundle path
POST /pedalboard/load_bundle/            # Load pedalboard
POST /pedalboard/save                    # Save state

# Snapshot/preset operations
GET /snapshot/list                       # Get all snapshots
GET /snapshot/load?id={n}                # Load snapshot n

# Parameter control
POST /effect/parameter/pi_stomp_set//graph{id}/{symbol}  # Set parameter
GET  /effect/parameter/pi_stomp_get//graph{id}/:bypass   # Get bypass state

# Tempo
POST /set_bpm                            # Set tap tempo
GET  /get_bpm                            # Get current BPM
```

**Pedalboard Data Loading** via LILV (LV2 bundle parser):
1. Parse `.ttl` files in pedalboard bundle
2. Extract plugin chain (tail-chase audio connections)
3. For each plugin: instance ID, parameters (min/max/value), MIDI bindings
4. Create `Pedalboard` object with `Plugin` and `Parameter` objects

**Change Detection**:
- WebSocket messages from MOD-UI (`loading_end`, `pedal_snapshot`)
- Typed protocol in `ws_protocol.py` parses messages
- `loading_end` stages snapshot ID in `next_pedalboard_preset_index` until pedalboard change detected
- `pedal_snapshot` updates staged ID if pending, otherwise updates current pedalboard
- Monitors `last.json` mtime to detect pedalboard changes, reads bundle path from file

**Banks** (v3 only):
- Pedalboard grouping/ordering managed by MOD-UI
- File: `/home/pistomp/data/banks.json` (read-only to piStomp)
- Polled via mtime check (1000ms) in `poll_modui_changes()`
- Structure: `{bank_name: [pedalboard_titles]}`
- Current selection persisted in `settings.yml`
- Filters pedalboard menu if bank selected, shows all if None

### Core Components

**Footswitches** (`pistomp/footswitch.py`):
- **Modes**: MIDI CC, Relay Bypass, Preset Change, Tap Tempo
- **Longpress Groups**: Shared class-level state for multi-switch actions
  - Two switches in group pressed within 0.4s → group callback
  - Examples: `next_snapshot`, `previous_snapshot`, `toggle_bypass`
- **Config Overlay**: Per-pedalboard override of MIDI CC, bypass, preset, color
- **Physical**: GPIO-based (`gpioswitch.py`) or ADC-based (`analogswitch.py`)

**Encoders** (`pistomp/encoder.py`, `pistomp/encoder_controller.py`):
- **Base**: Quadrature decoding, GPIO interrupts, debounce (v1/v2)
- **EncoderController** (v3): Speed-based amplification + parameter quantization
  - Speed detection: 4+ rotations=8× steps, 2-3=4× steps, 1=1× step
  - Resolution: 128 (MIDI CC), 256 (continuous), exact (INTEGER/ENUMERATION/TOGGLED)
- **Volume Encoder**: EncoderController bound to synthetic audio Parameter (v3 only)
- **Buttons**: Configurable shortpress (callback + args) and longpress
- **State Machines** (v1/v2 only): `TopEncoderMode`, `BotEncoderMode`, `UniversalEncoderMode`

**Analog Controls** (`pistomp/analogmidicontrol.py`):
- Read 10-bit ADC via MCP3008 SPI chip
- Convert to MIDI CC (0-127) with threshold-based change detection
- Types: `KNOB`, `EXPRESSION`
- `send_current_value()` forces sync on pedalboard load

**LCD System**:
- **v1**: `lcdgfx.py` - Monochrome text display
- **v2/v3**: `lcd320x240.py` - Color GUI with widget-based UI library (`uilib/`)
  - ILI9341 controller, 320×240 RGB, configurable SPI speed (`uilib/lcd_ili9341.py`)
  - Builder pattern constructs UI from pedalboard data
  - Event-driven updates via `link_data()`
  - **ParameterDialog**: Driven by `Parameter` object (encapsulates formatting/taper)
  - Auto-dismiss timeout: `PARAMETER_DIALOG_TIMEOUT = 1.0` seconds

**Control Progress Visualization**:
- Real-time progress bars for analog controls and encoders (v2/v3)
- Icon widgets display fill effect based on control position (0-127 MIDI range)
- `poll_updates()` (200ms) reads `AnalogMidiControl.last_read` (ADC) or `EncoderMidiControl.midi_value` (MIDI)
- Progress bar fills column width, text inverts in filled area
- Icon boxes sized to full column width/height for visual consistency

**LCD Performance**:
- **SPI Speed**: User-configurable via System Menu → LCD Speed (restarts service)
  - 24 MHz (ILI9341 spec, safe) → 80ms full refresh
  - 48 MHz → 40ms full refresh
  - 56 MHz → 30ms full refresh
  - 80 MHz → 24ms full refresh
- **Polling**: Adapts automatically to SPI speed (`lcd.poll_divisor`)
- **Optimization**: Use `widget.refresh()` for low-latency updates (parameter value, footswitch state)
  - Full panel refresh still too slow for real-time - partial updates critical
  - Widget-only refresh: <10ms even at slow speeds
- **Thread safety**: `lcd_ili9341.py` uses lock - avoid blocking in refresh path

**Controller Architecture** (`pistomp/controller.py`):
- **RoutingInfo**: Dataclass with `RoutingDestination` enum (VIRTUAL, EXTERNAL)
  - Factory methods: `RoutingInfo.virtual()`, `RoutingInfo.external(port_name)`
  - Controllers expose routing via `get_routing_info()` - no type checking needed
- **DisplayInfo**: TypedDicts for LCD rendering (`AnalogDisplayInfo`, `FootswitchDisplayInfo`)
  - Contains type, id, category, and optionally port_name/midi_cc for external routing
  - Controllers expose display data via `get_display_info()`
- **Separation of concerns**: Handler queries controllers, prepares display data, LCD consumes it
  - No cross-layer imports (LCD doesn't import `ExternalMidiOut`)
  - No isinstance checks outside controller layer

### Data Flow Examples

**Expression Pedal Movement**:

```
poll_controls() (10ms)
  → AnalogMidiControl.refresh()
    → ADC read (0-1023) → MIDI CC (0-127)
      → midiout.send_message([0xB0|ch, 75, value])
        → ALSA MIDI Through (port 14:0)
          → JACK (bridged via -X seq)
            ├→ mod-host:midi_in (MIDI Learn / parameter control)
            └→ Available in MOD-UI for wiring to LV2 MIDI plugins
                → External MIDI devices (if wired through plugins)
```

**Pedalboard Change (via MOD UI)**:

```
MOD-UI sends WebSocket 'loading_end' message
  → poll_modui_changes() receives via ws_bridge (1000ms)
    → parse_message() → LoadingEndMessage
      → Store snapshot_id in next_pedalboard_preset_index
    → pedalboard_monitor.check_for_change() detects last.json mtime change
      → get_current_pedalboard_bundle() reads bundle path from last.json
        → LILV parses TTL → creates Pedalboard object
          → set_current_pedalboard(pb)
            → Use next_pedalboard_preset_index for preset_index, clear staging
            → Load {bundle}/config.yml
            → hardware.reinit(cfg) - overlay config
            → bind_current_pedalboard() - map controllers to parameters
            → external_midi.send_messages_for_pedalboard()
            → hardware.sync_analog_controls()
            → update_lcd()
```

**Footswitch Press → Plugin Bypass**:

```
poll_controls()
  → Footswitch.poll() → detect press
    → footswitch.pressed()
      → Toggle self.enabled
      → Update LED
      → Send MIDI CC (if configured)
      → Update bound parameter.value (e.g., :bypass)
      → refresh_callback(footswitch=self)
        → Handler.update_lcd_fs()
          → LCD.update_footswitch() - redraw indicator
```

### Key Files

**Entry & Factories**:
- `modalapistomp.py` - Main entry point and polling loop
- `pistomp/handlerfactory.py` - Handler version selection
- `pistomp/hardwarefactory.py` - Hardware version selection

**Handlers** (Business Logic):
- `pistomp/handler.py` - Abstract base
- `modalapi/mod.py` - v1/v2 handler
- `modalapi/modhandler.py` - v3 handler

**Hardware** (Physical Interface):
- `pistomp/hardware.py` - Base abstraction
- `pistomp/pistomp.py` - v1 implementation
- `pistomp/pistompcore.py` - v2 implementation
- `pistomp/pistomptre.py` - v3 implementation

**Controls**:
- `pistomp/controller.py` - Base class, RoutingInfo/DisplayInfo data structures
- `pistomp/footswitch.py` - Footswitch logic, longpress groups
- `pistomp/encoder.py` - Rotary encoder decoding (v1/v2 use base class directly)
- `pistomp/encoder_controller.py` - Speed amplification + quantization (v3)
- `pistomp/analogmidicontrol.py` - ADC-based MIDI controller

**MIDI**:
- `pistomp/midiout.py` - MIDI output to ALSA MIDI Through
- `modalapi/external_midi.py` - External device sync

**MOD API**:
- `modalapi/pedalboard.py` - LILV parser
- `modalapi/plugin.py` - Plugin representation
- `modalapi/websocket_bridge.py` - Async WebSocket client
- `modalapi/ws_protocol.py` - Typed message parsing

**Config & State**:
- `pistomp/config.py` - Config loading/validation
- `pistomp/settings.py` - Persistent settings (YAML)
- `common/parameter.py` - Parameter representation & formatting
- `common/token.py` - Token constants
- `common/util.py` - Utility functions

**Display**:
- `pistomp/lcd320x240.py` - Color LCD (v2/v3)
- `pistomp/lcdgfx.py` - Mono LCD (v1)
- `uilib/*` - Widget library (v3)

## Blend Mode

Analog input-driven snapshot interpolation. Smoothly blend between snapshots using expression pedals or tweak encoders.

**Multiple Blend Snapshots**: Each pedalboard can have multiple blend snapshots, each controlled by a different analog input. Each blend snapshot defines its own set of stops and interpolation curve.

**Snapshot Activation**: Auto-switches to the FIRST blend snapshot on pedalboard load. Switching between blend snapshots activates/deactivates them automatically.

**Stop Modification Detection**: Monitors `snapshots.json` timestamp (1000ms poll). On change, recreates blend snapshots and reinitializes diff maps without pedalboard reload.

**MIDI Parameter Exclusion**: Parameters with MIDI bindings are automatically excluded from interpolation to prevent conflicts with the blend input.

### Config

```yaml
blend_snapshots:
  - name: "Clean to Fuzz"           # Required: blend snapshot name
    input_id: 0                     # Required: expression pedal (0) or encoder (1, 2)
    interpolation: ease_out_quad    # Optional: linear (default), hermite, catmull_rom, ease_*
    stops:
      "0.0": "Clean"                # Dict format: position -> snapshot
      "0.5": "Crunch"
      "1.0": "Fuzz"
  - name: "Volume Swell"
    input_id: 1                     # Tweak1 encoder (v3 only)
    stops: ["Quiet", "Loud"]        # List format: auto-spaced evenly
```

**Position keys**: Stringified floats [0.0-1.0], min separation 1/127.
**Snapshot values**: Index (int) or name (str, prefix match, case-insensitive).
**Interpolation**: Easing (ease_in_quad, etc.) or spline (hermite, catmull_rom).
**Context limit**: hermite/catmull_rom look 2 stops back/forward for smoothness.
**Stops formats**: Dict `{"0.0": "A", "1.0": "B"}` or list `["A", "B"]` (auto-spaced).

### Implementation

- Pre-computes diff maps per segment at load (optimized 10ms critical path)
- MIDI-level de-duplication skips redundant WebSocket sends
- Per-parameter interpolation with neighbor context for splines
- MIDI-bound parameters automatically excluded from interpolation
- Supports both expression pedals and tweak encoders (v3)
- Encoder callback override: skips display update when blend mode active
- `blend/` - easing.py, input_controller.py, interpolation.py, manager.py, parameter_setter.py, snapshot.py, stop.py, types.py
