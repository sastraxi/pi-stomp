# This file is part of pi-stomp.
#
# pi-stomp is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# pi-stomp is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY of even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with pi-stomp.  If not, see <https://www.gnu.org/licenses/>.

"""Nav-encoder traversal pacing policy. See input/README.md."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class _NavRun:
    sign: int  # +1 or -1
    count: int  # > 0


class NavQueue:
    """Pacing policy for nav-encoder selector traversal.

    Enqueue signed detent counts; drain capped steps per flush. Reversals
    create new tail runs (theatre, not netting). Runs are never dropped;
    the queue drains FIFO and the final run always lands exactly.

    Two drain modes:
    - Scanning (pending <= max_jump): pop one detent per flush → the
      selector visibly scans across intermediate states.
    - Catching up (pending > max_jump): pop up to max_jump detents total
      across runs → coalesces intermediates to bound latency.
    """

    __slots__ = ("_runs", "_max_jump")

    def __init__(self, max_jump: int) -> None:
        assert max_jump >= 1
        self._max_jump = max_jump
        self._runs: list[_NavRun] = []

    def enqueue(self, count: int) -> None:
        if count == 0:
            return
        sign = 1 if count > 0 else -1
        n = abs(count)
        if self._runs and self._runs[-1].sign == sign:
            self._runs[-1].count += n
        else:
            self._runs.append(_NavRun(sign, n))

    @property
    def _pending(self) -> int:
        return sum(r.count for r in self._runs)

    def drain(self) -> list[tuple[int, int]]:
        if not self._runs:
            return []
        if self._pending <= self._max_jump:
            run = self._runs[0]
            run.count -= 1
            out = [(run.sign, 1)]
            if run.count == 0:
                self._runs.pop(0)
            return out
        out: list[tuple[int, int]] = []
        budget = self._max_jump
        i = 0
        while budget > 0 and i < len(self._runs):
            run = self._runs[i]
            k = run.count if run.count <= budget else budget
            out.append((run.sign, k))
            run.count -= k
            if run.count == 0:
                i += 1
            else:
                break
            budget -= k
        if i:
            del self._runs[:i]
        return out

    @property
    def has_pending(self) -> bool:
        return bool(self._runs)

    def clear(self) -> None:
        self._runs.clear()