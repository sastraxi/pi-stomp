# LCD Threading Architecture

## Problem Statement

### Current Bottleneck

The main polling loop (modalapistomp.py) runs at 10ms intervals for critical hardware control polling. However, LCD updates block this loop:

```python
# modalapistomp.py:148
time.sleep(0.01)  # lower to increase responsiveness, but can cause conflict with LCD if too low
```

**Issues:**
- `poll_lcd_updates()` called every 200ms (5Hz)
- LCD panel refreshes block for 30-50ms during SPI transfer
- During blocking, main thread cannot:
  - Poll hardware controls (10ms critical path)
  - Handle WebSocket messages
  - Update LEDs/VU meters
- **Result:** Input lag, delayed WebSocket responses, jittery indicators

### New Features Amplify Problem

Recent additions increase LCD update frequency needs:
- **Text scrolling:** Smooth animation requires frequent updates
- **Progress bars:** Analog controls/encoders need real-time visual feedback (20Hz+)
- **Clip indicators:** Audio clipping detection needs fast response

Updating these at 20Hz in main thread would block controls ~40% of the time (20 updates/sec × 2ms each).

## Why Asyncio Doesn't Solve This

**Asyncio requires async-capable I/O:**
- ✅ Network sockets (WebSocket bridge) - uses `select()`/`epoll()`
- ❌ SPI hardware - blocking I/O via `spidev` kernel driver

**SPI transfers are synchronous:**
```python
spi.writebytes(data)  # Blocks until transfer completes (~1-50ms)
```

No async SPI implementation exists because hardware is inherently synchronous.

## Why Threading DOES Solve This

### GIL Release During I/O

**Key insight:** C extension I/O operations release the GIL:
- ✅ **SPI transfers** (via `spidev`) - **CONFIRMED:** Uses `Py_BEGIN_ALLOW_THREADS` around all `ioctl()` calls ([source](https://github.com/doceme/py-spidev/blob/master/spidev_module.c))
- ✅ **PIL operations** - **PARTIAL:** Releases GIL for filters/large ops via `ImagingSectionEnter/Leave` ([PR #2670](https://github.com/python-pillow/Pillow/issues/2635))
- ⚠️ Python code execution holds GIL (but minimal in refresh path)

**This means:** While LCD thread waits for SPI transfer, **main thread continues polling controls!**

**Impact:** SPI is primary blocker (30-50ms for panels, 1-2ms for widgets), so GIL release during transfers provides major benefit regardless of PIL behavior.

### Existing Thread Safety

The codebase already has thread-safe LCD updates:
- `lcd_ili9341.py:36` - `threading.Lock` serializes all LCD writes
- Widget refresh path is stateless (reads widget state, writes to LCD)
- No shared mutable state between main and LCD threads

**We just need to move LCD work to another thread.**

## Solution Sketch

### Architecture

```
Main Thread (10ms critical path):
├─ poll_controls() - Hardware polling (never blocks)
├─ poll_indicators() - LED/VU updates
├─ poll_fast_lcd_updates() @ 50ms (20Hz) - Enqueue dynamic widgets
├─ poll_slow_lcd_updates() @ 200ms (5Hz) - Enqueue static widgets
└─ poll_modui_changes() - WebSocket handling

LCD Update Thread (continuous):
├─ Dequeue next command (with deduplication)
├─ Drop stale commands (>200ms old)
├─ Execute: widget.refresh() - SPI transfer (releases GIL)
└─ Repeat
```

### Deduplicating Queue

**Problem:** If updates arrive faster than LCD can render, queue grows unbounded.

**Solution:** Hash-based deduplication
- Key: `(widget_id, update_type)`
- Value: Latest `LcdUpdateCommand`
- **Enqueue:** Replace existing command for same widget
- **Result:** Only latest state queued, automatic backpressure

**Age-based dropping:**
- Discard commands older than 200ms
- Prevents rendering stale state after queue backlog clears

### Dual-rate Updates

**Fast updates @ 20Hz (50ms):**
- Progress bars (analog controls, encoders)
- Clip indicators
- Real-time visual feedback

**Slow updates @ 5Hz (200ms):**
- Text scrolling (tick animations)
- Panel refreshes (menus, dialogs)
- Infrequent state changes

## Implementation Plan

### Phase 1: Core Threading Infrastructure
1. Create `pistomp/lcd_update_thread.py`
   - `LcdUpdateCommand` class
   - `LcdUpdateThread` with deduplicating queue
   - Age-based dropping logic
2. Add thread initialization to `lcd320x240.py.__init__()`
3. Add thread shutdown to `lcd320x240.py.cleanup()`

### Phase 2: Command Queueing
1. Split `poll_lcd_updates()` into:
   - `poll_fast_lcd_updates()` - 20Hz enqueuing
   - `poll_slow_lcd_updates()` - 5Hz enqueuing
2. Convert direct `widget.refresh()` calls to `lcd_thread.enqueue()`
3. Update main loop in `modalapistomp.py` for dual-rate polling

### Phase 3: Widget Updates
1. Modify `Icon.set_progress()` to not call `refresh()` internally
2. Update clip indicator logic for thread-safe color changes
3. Ensure text scrolling `tick()` is safe to call from LCD thread

### Phase 4: Testing & Tuning
1. Verify main thread never blocks on LCD (add timing metrics)
2. Measure queue depth during heavy updates
3. Tune `max_age_ms` based on observed latencies
4. Add debug logging for dropped/superseded updates

## Implementation Details

### 1. LCD Update Thread (`pistomp/lcd_update_thread.py`)

```python
import time
import threading
import logging
from enum import Enum
from typing import Optional, Dict, Tuple

class UpdateType(Enum):
    WIDGET_REFRESH = 1  # Small bounding box (fast)
    PANEL_REFRESH = 2   # Full panel (slow)
    TICK = 3            # Text scroll animation
    SHUTDOWN = 4

class LcdUpdateCommand:
    def __init__(self, update_type: UpdateType, target, timestamp: float = None, **kwargs):
        self.update_type = update_type
        self.target = target
        self.timestamp = timestamp or time.time()
        self.kwargs = kwargs  # Extra data (e.g., progress value)

    def get_key(self) -> Tuple[int, UpdateType]:
        """Return unique key for deduplication.

        Same target + update type = superseding update.
        Example: "refresh widget X" replaces previous "refresh widget X"
        """
        return (id(self.target), self.update_type)

    def age_ms(self) -> float:
        """Get age of command in milliseconds."""
        return (time.time() - self.timestamp) * 1000

    def is_stale(self, max_age_ms: float) -> bool:
        """Check if command is too old to execute."""
        return self.age_ms() > max_age_ms

class LcdUpdateThread(threading.Thread):
    """
    Thread for non-blocking LCD updates with:
    - Deduplication: Only latest update per widget matters
    - Age-based dropping: Discard commands older than max_age_ms
    - Backpressure: Replacing old commands prevents queue growth
    """

    def __init__(self, max_age_ms: float = 200):
        super().__init__(name="LCDUpdate", daemon=True)
        self.pending_updates: Dict[Tuple, LcdUpdateCommand] = {}
        self.lock = threading.Lock()  # Protects pending_updates dict
        self.running = True
        self.max_age_ms = max_age_ms

        # Metrics
        self.updates_processed = 0
        self.updates_dropped_stale = 0
        self.updates_dropped_superseded = 0

    def enqueue(self, cmd: LcdUpdateCommand) -> bool:
        """
        Enqueue update command, superseding any existing update for same target.

        Returns:
            True if command was queued (possibly replacing old one)
            False only on shutdown
        """
        if not self.running:
            return False

        with self.lock:
            key = cmd.get_key()

            # Check if we're replacing an existing command
            if key in self.pending_updates:
                old_cmd = self.pending_updates[key]
                self.updates_dropped_superseded += 1
                logging.debug(
                    f"LCD: Superseding {old_cmd.update_type.name} "
                    f"(age={old_cmd.age_ms():.1f}ms) with new update"
                )

            # Replace with latest state
            self.pending_updates[key] = cmd

        return True

    def _dequeue_next(self) -> Optional[LcdUpdateCommand]:
        """
        Get next command to process, removing stale ones.

        Returns None if queue is empty or all commands are stale.
        """
        with self.lock:
            if not self.pending_updates:
                return None

            # Python 3.7+ dicts maintain insertion order
            # Pop oldest inserted command (FIFO for non-superseded updates)
            key = next(iter(self.pending_updates))
            cmd = self.pending_updates.pop(key)

        # Check staleness outside lock
        if cmd.is_stale(self.max_age_ms):
            self.updates_dropped_stale += 1
            logging.debug(
                f"LCD: Dropping stale {cmd.update_type.name} "
                f"(age={cmd.age_ms():.1f}ms > {self.max_age_ms}ms)"
            )
            return None

        return cmd

    def run(self):
        """LCD update loop - processes commands until shutdown."""
        logging.info("LCD update thread started")

        while self.running:
            cmd = self._dequeue_next()

            if cmd is None:
                time.sleep(0.001)  # 1ms sleep if queue empty
                continue

            try:
                self._execute_command(cmd)
                self.updates_processed += 1
            except Exception as e:
                logging.error(f"LCD update thread error: {e}", exc_info=True)

        logging.info(
            f"LCD update thread stopped. "
            f"Processed: {self.updates_processed}, "
            f"Dropped (stale): {self.updates_dropped_stale}, "
            f"Dropped (superseded): {self.updates_dropped_superseded}"
        )

    def _execute_command(self, cmd: LcdUpdateCommand):
        """Execute update command. Runs in LCD thread."""
        if cmd.update_type == UpdateType.SHUTDOWN:
            self.running = False
            return

        # All refresh types just call refresh() - unified!
        if cmd.update_type in (UpdateType.WIDGET_REFRESH, UpdateType.PANEL_REFRESH):
            if cmd.target and hasattr(cmd.target, 'refresh'):
                # Apply any kwargs first (e.g., set_progress)
                if 'progress' in cmd.kwargs:
                    cmd.target.set_progress(cmd.kwargs['progress'])
                if 'color' in cmd.kwargs:
                    cmd.target.set_foreground(cmd.kwargs['color'])
                    cmd.target.set_outline(1, cmd.kwargs['color'])
                cmd.target.refresh()

        elif cmd.update_type == UpdateType.TICK:
            if cmd.target and hasattr(cmd.target, 'tick'):
                cmd.target.tick()

    def get_queue_depth(self) -> int:
        """Get current number of pending updates."""
        with self.lock:
            return len(self.pending_updates)

    def shutdown(self):
        """Signal thread to stop and wait for completion."""
        self.enqueue(LcdUpdateCommand(UpdateType.SHUTDOWN, target=None))
        self.join(timeout=1.0)
```

### 2. Main Loop Updates (`modalapistomp.py`)

```python
# main loop
period = 0
while True:
    handler.poll_controls()
    time.sleep(0.01)  # 10ms base rate - no more LCD conflicts!

    period += 1

    if period % 2 == 0:  # 20ms
        handler.poll_indicators()

    if period % 5 == 0:  # 50ms = 20Hz - FAST LCD UPDATES
        handler.poll_fast_lcd_updates()

    if period % 20 == 0:  # 200ms = 5Hz - SLOW LCD UPDATES
        handler.poll_slow_lcd_updates()

    if period % 100 == 0:  # 1000ms
        handler.poll_modui_changes()

    if period % 200 == 0:  # 2000ms
        handler.poll_wifi()

    if period > 6000:  # 60s
        handler.poll_system_info()
        period = 0
```

### 3. LCD Updates (`pistomp/lcd320x240.py`)

#### Initialization

```python
class Lcd(abstract_lcd.Lcd):
    def __init__(self, cwd, handler=None, flip=False):
        # ... existing init code ...

        # Start LCD update thread
        from pistomp.lcd_update_thread import LcdUpdateThread
        self.lcd_thread = LcdUpdateThread(max_age_ms=200)
        self.lcd_thread.start()

        # Track last clip state for change detection
        self._last_clip_state = (False, False)
```

#### Fast Updates (20Hz)

```python
def poll_fast_lcd_updates(self):
    """
    Fast LCD updates @ 20Hz (50ms).

    Enqueues commands for frequently-changing widgets:
    - Progress bars (analog controls, encoders)
    - Clip indicators (if clipping state changed)
    """
    from pistomp.lcd_update_thread import LcdUpdateCommand, UpdateType

    # Progress bar updates
    for icon in self.w_controls:
        if icon.object is None:
            continue

        midi_value = None
        if isinstance(icon.object, AnalogMidiControl):
            midi_value = as_midi_value(icon.object.last_read)
        elif isinstance(icon.object, EncoderMidiControl):
            midi_value = icon.object.midi_value
        elif isinstance(icon.object, CollageMode):
            pedal = icon.object.pedal_controller.controlled_pedal
            if pedal:
                position = pedal.last_read / 1023.0
                midi_value = int(position * 127)

        if midi_value is not None:
            progress = midi_value / 127.0
            # Only enqueue if changed (dedup handles rest)
            if icon.progress != progress:
                self.lcd_thread.enqueue(
                    LcdUpdateCommand(
                        UpdateType.WIDGET_REFRESH,
                        target=icon,
                        progress=progress
                    )
                )

    # Clip indicators (already checked at 20ms in poll_indicators)
    if self.handler and self.handler.clipping_monitor:
        if self.handler.clipping_monitor.enabled:
            clip_left, clip_right = self.handler.clipping_monitor.check_clipping()
            self._enqueue_clip_updates(clip_left, clip_right)

def _enqueue_clip_updates(self, clip_left, clip_right):
    """Enqueue clip indicator color changes if state changed."""
    from pistomp.lcd_update_thread import LcdUpdateCommand, UpdateType

    if (clip_left, clip_right) != self._last_clip_state:
        self._last_clip_state = (clip_left, clip_right)

        # Enqueue updates with color data in kwargs
        self.lcd_thread.enqueue(
            LcdUpdateCommand(
                UpdateType.WIDGET_REFRESH,
                target=self.w_clip_left,
                color=(255, 0, 0) if clip_left else (80, 80, 80)
            )
        )
        self.lcd_thread.enqueue(
            LcdUpdateCommand(
                UpdateType.WIDGET_REFRESH,
                target=self.w_clip_right,
                color=(255, 0, 0) if clip_right else (80, 80, 80)
            )
        )
```

#### Slow Updates (5Hz)

```python
def poll_slow_lcd_updates(self):
    """
    Slow LCD updates @ 5Hz (200ms).

    Enqueues commands for slower-changing elements:
    - Text scrolling (tick animation)
    - Panel refreshes (menus, dialogs)
    - Panel stack updates
    """
    from pistomp.lcd_update_thread import LcdUpdateCommand, UpdateType

    # Panel stack (menu/dialog changes)
    if self.pstack.lcd_needs_update:
        self.lcd_thread.enqueue(
            LcdUpdateCommand(UpdateType.PANEL_REFRESH, target=self.pstack)
        )

    # Text scrolling
    if self.w_preset:
        self.lcd_thread.enqueue(
            LcdUpdateCommand(UpdateType.TICK, target=self.w_preset)
        )
    if self.w_pedalboard:
        self.lcd_thread.enqueue(
            LcdUpdateCommand(UpdateType.TICK, target=self.w_pedalboard)
        )
```

#### Cleanup

```python
def cleanup(self):
    """Shutdown LCD thread and cleanup resources."""
    if hasattr(self, 'lcd_thread'):
        self.lcd_thread.shutdown()

    # ... existing cleanup ...
```

### 4. Widget Modifications (`uilib/icon.py`)

```python
def set_progress(self, progress):
    """Set progress value (0.0-1.0). Does not trigger refresh.

    This allows decoupling value update from LCD refresh,
    enabling thread-safe updates via command queue.
    """
    self.progress = max(0.0, min(1.0, progress)) if progress is not None else None
    # Removed: self.refresh() - caller controls when to refresh
```

## Benefits

### Performance

- **Main thread never blocks:** Enqueue is O(1) dict update with lock
- **20Hz progress bars:** Smooth visual feedback without impacting controls
- **No input lag:** 10ms control polling never interrupted
- **Automatic backpressure:** Deduplication prevents queue growth

### Reliability

- **Age-based dropping:** Prevents rendering stale state
- **Metrics built-in:** Track processed/dropped for debugging
- **Thread-safe by design:** Existing lock serializes LCD writes
- **Graceful degradation:** Dropped frames invisible to user

### Maintainability

- **Unified refresh logic:** All update types call `refresh()`
- **Minimal widget changes:** Just remove internal `refresh()` calls
- **Clean separation:** Main thread = data collection, LCD thread = rendering
- **Observable:** Queue depth and metrics expose system health

## Testing Strategy

1. **Visual inspection:** Progress bars should be smooth at 20Hz
2. **Timing metrics:** Log main loop cycle time (should stay ~10ms)
3. **Queue depth:** Monitor `get_queue_depth()` during heavy updates
4. **Dropped frames:** Check `updates_dropped_*` counters (should be low)
5. **Stress test:** Rapidly change all controls simultaneously
6. **Thread safety:** Run with Python `-X dev` mode to detect issues

## Future Optimizations

### Priority Queue

Currently uses FIFO for non-superseded updates. Could prioritize:
- `WIDGET_REFRESH` (fast) over `PANEL_REFRESH` (slow)
- Clip indicators over progress bars (safety-critical)

### Adaptive Rates

Monitor queue depth and adjust polling rates:
- If queue consistently empty → increase fast update rate to 30Hz
- If queue consistently full → decrease fast update rate to 10Hz

### Batched Transfers

Group multiple widget updates into single SPI transfer:
- Collect dirty rectangles
- Compose into single frame
- Transfer once
- Requires more complex rendering pipeline
