"""NavQueue — pure pacing policy for nav-encoder selector traversal.

See pistomp/input/nav_queue.py and pistomp/input/README.md.
"""

from __future__ import annotations

from pistomp.input.nav_queue import NavQueue


# ---------------------------------------------------------------------------
# enqueue / has_pending
# ---------------------------------------------------------------------------


def test_empty_by_default():
    q = NavQueue(max_jump=4)
    assert not q.has_pending
    assert q.drain() == []


def test_enqueue_zero_is_noop():
    q = NavQueue(max_jump=4)
    q.enqueue(0)
    assert not q.has_pending
    assert q.drain() == []


def test_same_sign_coalesces_into_tail_run():
    q = NavQueue(max_jump=4)
    q.enqueue(2)
    q.enqueue(1)
    assert q.has_pending
    # pending(3) <= max_jump(4) → scanning: one per flush
    assert q.drain() == [(+1, 1)]
    assert q.drain() == [(+1, 1)]
    assert q.drain() == [(+1, 1)]
    assert not q.has_pending


def test_reversal_creates_new_tail_run():
    q = NavQueue(max_jump=8)
    q.enqueue(3)
    q.enqueue(-1)
    # pending(4) <= max_jump(8) → scanning, one per flush in arrival order
    assert q.drain() == [(+1, 1)]
    assert q.drain() == [(+1, 1)]
    assert q.drain() == [(+1, 1)]
    assert q.drain() == [(-1, 1)]
    assert not q.has_pending


# ---------------------------------------------------------------------------
# drain: scanning mode (pending <= max_jump → one detent per flush)
# ---------------------------------------------------------------------------


def test_scanning_drains_one_per_flush():
    q = NavQueue(max_jump=4)
    q.enqueue(3)
    assert q.drain() == [(+1, 1)]
    assert q.has_pending
    assert q.drain() == [(+1, 1)]
    assert q.has_pending
    assert q.drain() == [(+1, 1)]
    assert not q.has_pending


def test_scanning_transitions_to_catching_up_when_backlog_grows():
    q = NavQueue(max_jump=4)
    q.enqueue(3)
    assert q.drain() == [(+1, 1)]
    q.enqueue(4)  # now pending 6 > 4 → catching up
    assert q.drain() == [(+1, 4)]
    # pending 2 <= 4 → back to scanning
    assert q.drain() == [(+1, 1)]
    assert q.drain() == [(+1, 1)]
    assert not q.has_pending


# ---------------------------------------------------------------------------
# drain: catching-up mode (pending > max_jump → coalesce up to max_jump)
# ---------------------------------------------------------------------------


def test_catching_up_caps_total_per_flush():
    q = NavQueue(max_jump=4)
    q.enqueue(10)
    assert q.drain() == [(+1, 4)]
    assert q.drain() == [(+1, 4)]
    # pending 2 <= 4 → scanning
    assert q.drain() == [(+1, 1)]
    assert q.drain() == [(+1, 1)]
    assert not q.has_pending


def test_catching_up_spans_runs():
    q = NavQueue(max_jump=4)
    q.enqueue(3)
    q.enqueue(-3)
    # pending 6 > 4 → catching up
    assert q.drain() == [(+1, 3), (-1, 1)]
    # pending 2 <= 4 → scanning
    assert q.drain() == [(-1, 1)]
    assert q.drain() == [(-1, 1)]
    assert not q.has_pending


def test_catching_up_stops_at_head_remainder():
    q = NavQueue(max_jump=4)
    q.enqueue(10)
    q.enqueue(-3)
    assert q.drain() == [(+1, 4)]
    assert q.drain() == [(+1, 4)]
    # pending 5 > 4 → catching up; +2 then -3 capped at 4
    assert q.drain() == [(+1, 2), (-1, 2)]
    # pending 1 <= 4 → scanning
    assert q.drain() == [(-1, 1)]
    assert not q.has_pending


# ---------------------------------------------------------------------------
# target-frame exactness (total drained matches input)
# ---------------------------------------------------------------------------


def test_target_frame_drains_exact_total():
    q = NavQueue(max_jump=8)
    q.enqueue(20)
    drained = 0
    while q.has_pending:
        for sign, k in q.drain():
            assert sign == +1
            drained += k
    assert drained == 20
    assert not q.has_pending


def test_target_frame_after_reversal_drains_exact():
    q = NavQueue(max_jump=4)
    q.enqueue(5)
    q.enqueue(-3)
    total = 0
    while q.has_pending:
        for sign, k in q.drain():
            total += sign * k
    assert total == 2  # +5 - 3
    assert not q.has_pending


# ---------------------------------------------------------------------------
# never drop runs
# ---------------------------------------------------------------------------


def test_never_drops_intermediate_run():
    q = NavQueue(max_jump=8)
    q.enqueue(20)
    q.enqueue(-1)  # intermediate wiggle
    q.enqueue(5)
    seen: list[tuple[int, int]] = []
    while q.has_pending:
        seen.extend(q.drain())
    assert sum(s * k for s, k in seen) == 24  # +20 - 1 + 5
    assert (-1, 1) in seen


# ---------------------------------------------------------------------------
# clear
# ---------------------------------------------------------------------------


def test_clear_empties_queue():
    q = NavQueue(max_jump=4)
    q.enqueue(5)
    q.enqueue(-3)
    assert q.has_pending
    q.clear()
    assert not q.has_pending
    assert q.drain() == []


def test_clear_after_partial_drain():
    q = NavQueue(max_jump=4)
    q.enqueue(10)
    q.drain()
    assert q.has_pending
    q.clear()
    assert not q.has_pending


# ---------------------------------------------------------------------------
# edge cases
# ---------------------------------------------------------------------------


def test_max_jump_one_always_scans():
    q = NavQueue(max_jump=1)
    q.enqueue(3)
    for _ in range(3):
        assert q.drain() == [(+1, 1)]
    assert not q.has_pending


def test_enqueue_after_partial_drain_same_sign_extends_remainder():
    q = NavQueue(max_jump=4)
    q.enqueue(6)
    assert q.drain() == [(+1, 4)]  # 2 left
    q.enqueue(1)  # same sign → remainder becomes 3
    assert q.drain() == [(+1, 1)]  # scanning (3 <= 4)
    assert q.drain() == [(+1, 1)]
    assert q.drain() == [(+1, 1)]
    assert not q.has_pending


def test_enqueue_after_partial_drain_reversal_creates_new_tail():
    q = NavQueue(max_jump=4)
    q.enqueue(6)
    assert q.drain() == [(+1, 4)]  # +2 left
    q.enqueue(-1)  # reversal → new tail
    # pending 3 <= 4 → scanning
    assert q.drain() == [(+1, 1)]
    assert q.drain() == [(+1, 1)]
    assert q.drain() == [(-1, 1)]
    assert not q.has_pending