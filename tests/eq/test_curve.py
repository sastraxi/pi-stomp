"""Sanity tests for pistomp.eq.curve magnitude math."""

import math

import numpy as np
import pytest

from pistomp.eq import bands
from pistomp.eq.curve import (
    GRAPH_FREQS,
    GRAPH_W,
    BandParams,
    CurveCache,
    EqState,
    db_to_y,
    freq_to_x,
)


def _flat_state(plugin_enabled=True, global_gain_db=0.0) -> EqState:
    """All bands present, all disabled — should produce a flat 0 dB curve."""
    return EqState(
        plugin_enabled=plugin_enabled,
        global_gain_db=global_gain_db,
        bands={b.name: BandParams(enabled=False, freq=1000.0, q=0.707, gain_db=0.0)
               for b in bands.BANDS},
    )


def test_all_disabled_curve_is_flat_zero() -> None:
    cache = CurveCache()
    curve = cache.compute(_flat_state())
    assert curve.shape == (GRAPH_W,)
    assert np.allclose(curve, 0.0)


def test_global_gain_is_flat_offset() -> None:
    cache = CurveCache()
    curve = cache.compute(_flat_state(global_gain_db=6.0))
    assert np.allclose(curve, 6.0)


def test_peak_band_hits_target_gain_near_center_freq() -> None:
    """A peaking band at 1 kHz with +12 dB should give close to +12 dB
    at the column nearest 1 kHz when isolated."""
    state = _flat_state()
    new_bands = dict(state.bands)
    new_bands["B3"] = BandParams(enabled=True, freq=1000.0, q=1.0, gain_db=12.0)
    state = EqState(state.plugin_enabled, state.global_gain_db, new_bands)

    curve = CurveCache().compute(state)
    cx = freq_to_x(1000.0)
    assert abs(curve[cx] - 12.0) < 0.5, f"expected +12 dB at 1 kHz, got {curve[cx]:.2f}"


def test_peak_band_negative_gain_near_center() -> None:
    state = _flat_state()
    new_bands = dict(state.bands)
    new_bands["B3"] = BandParams(enabled=True, freq=1000.0, q=1.0, gain_db=-9.0)
    state = EqState(state.plugin_enabled, state.global_gain_db, new_bands)

    curve = CurveCache().compute(state)
    cx = freq_to_x(1000.0)
    assert abs(curve[cx] - (-9.0)) < 0.5


def test_cascade_is_sum_of_stages_in_db() -> None:
    """Two enabled peak bands at well-separated frequencies should sum: at
    each band's center the other contributes ~0 dB, so total ≈ that band's gain."""
    state = _flat_state()
    nb = dict(state.bands)
    nb["B1"] = BandParams(enabled=True, freq=80.0,  q=1.0, gain_db=+6.0)
    nb["B4"] = BandParams(enabled=True, freq=8000.0, q=1.0, gain_db=-6.0)
    state = EqState(True, 0.0, nb)

    curve = CurveCache().compute(state)
    assert abs(curve[freq_to_x(80.0)]   - 6.0) < 0.7
    assert abs(curve[freq_to_x(8000.0)] - (-6.0)) < 0.7


def test_disabled_band_contributes_zero() -> None:
    state = _flat_state()
    nb = dict(state.bands)
    nb["B3"] = BandParams(enabled=False, freq=1000.0, q=1.0, gain_db=18.0)
    state = EqState(True, 0.0, nb)

    curve = CurveCache().compute(state)
    assert np.allclose(curve, 0.0)


def test_cache_reuses_unchanged_stages() -> None:
    cache = CurveCache()
    s1 = _flat_state()
    nb = dict(s1.bands)
    nb["B2"] = BandParams(enabled=True, freq=500.0, q=1.0, gain_db=3.0)
    s1 = EqState(True, 0.0, nb)
    c1 = cache.compute(s1)

    # Mutate a different band — B2's cached array must remain.
    nb2 = dict(s1.bands)
    nb2["B3"] = BandParams(enabled=True, freq=2000.0, q=1.0, gain_db=4.0)
    s2 = EqState(True, 0.0, nb2)
    c2 = cache.compute(s2)

    # c2 at B2's centre should reflect both B2 (+3) and any tail of B3 (≈0).
    cx = freq_to_x(500.0)
    assert abs(c2[cx] - c1[cx]) < 0.5


def test_db_to_y_mapping() -> None:
    curve = np.array([18.0, 0.0, -18.0])
    y = db_to_y(curve, y_top=20, y_bot=200, db_max=18.0)
    assert y[0] == 20
    assert y[1] == 110
    assert y[2] == 200


def test_freq_to_x_endpoints() -> None:
    assert freq_to_x(20.0) == 0
    assert freq_to_x(20000.0) == GRAPH_W - 1


def test_graph_freqs_are_log_spaced() -> None:
    log_diffs = np.diff(np.log(GRAPH_FREQS))
    assert np.allclose(log_diffs, log_diffs[0])
