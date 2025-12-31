# This file is part of pi-stomp.
#
# pi-stomp is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# pi-stomp is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with pi-stomp.  If not, see <https://www.gnu.org/licenses/>.

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
    def __init__(self, update_type: UpdateType, target, timestamp: float = None):
        self.update_type = update_type
        self.target = target
        self.timestamp = timestamp or time.time()

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
                # Crash the whole service on LCD thread errors
                raise

        logging.info(
            f"LCD update thread stopped. "
            f"Processed: {self.updates_processed}, "
            f"Dropped (stale): {self.updates_dropped_stale}, "
            f"Dropped (superseded): {self.updates_dropped_superseded}"
        )

    def _execute_command(self, cmd: LcdUpdateCommand):
        """Execute update command. Runs in LCD thread.

        IMPORTANT: This only renders widgets. All state changes must happen
        in the main thread before enqueueing the refresh command.
        """
        if cmd.update_type == UpdateType.SHUTDOWN:
            self.running = False
            return

        if cmd.update_type == UpdateType.WIDGET_REFRESH:
            # Widget refresh - just render current state
            if cmd.target and hasattr(cmd.target, 'refresh'):
                cmd.target.refresh()

        elif cmd.update_type == UpdateType.PANEL_REFRESH:
            # Panel/PanelStack refresh
            if cmd.target and hasattr(cmd.target, 'refresh'):
                cmd.target.refresh()
                # Clear the update flag if it's a PanelStack
                if hasattr(cmd.target, 'lcd_needs_update'):
                    cmd.target.lcd_needs_update = False

        elif cmd.update_type == UpdateType.TICK:
            # Text scrolling animation
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
