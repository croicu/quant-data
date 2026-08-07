from __future__ import annotations

from reconcile.outlier_detection import OutlierBar, OutlierThresholds, is_bar_rejected


def _bar(time_id: int, close: float, open_: float = 10.0, high: float = 10.0, low: float = 10.0) -> OutlierBar:
    return OutlierBar(time_id=time_id, open=open_, high=high, low=low, close=close)


_QUIET_STEP_CYCLE = (0.01, 0.02, 0.015, 0.025)


def _quiet_window(target_time_id: int, half_span: int = 15, field: str = "close") -> dict[int, OutlierBar]:
    """A wide window of background bars around target_time_id with a jittered-but-small step
    (cycling through 4 distinct values), so the MAD-based reference scale is a genuine small
    positive number rather than degenerately zero (a perfectly uniform step would collapse MAD to
    0, same as test_smooth_bars_not_rejected's deliberate all-identical case below). A 2-value
    alternating step isn't robust enough for this: deleting a neighbor (the missing-neighbor tests
    below) can shift the parity so one value becomes a strict majority, collapsing MAD to 0 by
    coincidence -- a 4-value cycle makes that require losing nearly all instances of 3 values, not
    just a 1-bar deletion. target_time_id's own bar and its immediate neighbors are left at
    whatever this generates -- callers that want to inject a specific back/forward diff at the
    target should overwrite window[target_time_id] and window[target_time_id + 1] afterwards;
    window[target_time_id + 2] onward already continues from whatever value ends up at
    target_time_id + 1, so a caller-supplied forward diff still only affects the two excluded
    (target-touching) pairs, not the real background sample.
    """
    values: dict[int, float] = {}
    value = 100.0
    cycle_index = 0
    for time_id in range(target_time_id - half_span, target_time_id + half_span + 1):
        values[time_id] = value
        value += _QUIET_STEP_CYCLE[cycle_index % len(_QUIET_STEP_CYCLE)]
        cycle_index += 1

    window: dict[int, OutlierBar] = {}
    for time_id, v in values.items():
        kwargs = {"open": 10.0, "high": 10.0, "low": 10.0, "close": 10.0}
        kwargs[field] = v
        window[time_id] = OutlierBar(time_id=time_id, **kwargs)
    return window


def _inject_target_diffs(window: dict[int, OutlierBar], target_time_id: int, diff_back: float, diff_fwd: float, field: str) -> None:
    """Overrides the target and its immediate forward neighbor so the target's own back/forward
    diffs are exactly diff_back/diff_fwd, then re-chains every bar after target+1 onward from the
    new forward-neighbor value at the *same* step cycle (phase reset to the start of the cycle) so
    the background sample beyond the target stays internally consistent (real small-step diffs,
    not silently contaminated by the injected spike)."""
    back_one_value = getattr(window[target_time_id - 1], field)
    target_value = back_one_value + diff_back
    fwd_one_value = target_value + diff_fwd

    def _with_field(time_id: int, value: float) -> OutlierBar:
        kwargs = {"open": 10.0, "high": 10.0, "low": 10.0, "close": 10.0}
        kwargs[field] = value
        return OutlierBar(time_id=time_id, **kwargs)

    window[target_time_id] = _with_field(target_time_id, target_value)
    window[target_time_id + 1] = _with_field(target_time_id + 1, fwd_one_value)

    max_time_id = max(window.keys())
    value = fwd_one_value
    cycle_index = 0
    for time_id in range(target_time_id + 2, max_time_id + 1):
        value += _QUIET_STEP_CYCLE[cycle_index % len(_QUIET_STEP_CYCLE)]
        cycle_index += 1
        window[time_id] = _with_field(time_id, value)


def test_reversal_spike_is_rejected():
    # A 3.0 up-then-full-reversal spike (opposite-sign diffs -- reversal shape) against a quiet
    # background whose MAD is ~0.005: ratio ~600, comfortably clearing k_reversal_oc=300.
    window = _quiet_window(target_time_id=102)
    _inject_target_diffs(window, target_time_id=102, diff_back=3.0, diff_fwd=-3.0, field="close")

    assert is_bar_rejected(window, target_time_id=102, thresholds=OutlierThresholds()) is True


def test_smooth_bars_not_rejected():
    # A perfectly uniform step (no jitter at all, including at the target) collapses the
    # background MAD to exactly 0 -- skipped, not rejected, regardless of k.
    window: dict[int, OutlierBar] = {}
    close = 100.00
    for time_id in range(92, 113):
        window[time_id] = _bar(time_id, close=close)
        close += 0.01

    assert is_bar_rejected(window, target_time_id=102, thresholds=OutlierThresholds()) is False


def test_missing_backward_neighbor_uses_one_sided_boundary_check():
    # No entry at 101 (e.g. target is the first bar of a session segment) -- can't classify
    # reversal/trend with only one side, so this falls back to a one-sided check against
    # k_boundary_oc using just the forward diff. -5.0 against the ~0.005 background MAD gives
    # ratio ~1000, comfortably clearing k_boundary_oc=20 (real-data calibrated, see
    # outlier_detection.py's DEFAULT_K_BOUNDARY_* comment).
    window = _quiet_window(target_time_id=102)
    _inject_target_diffs(window, target_time_id=102, diff_back=0.0, diff_fwd=-5.0, field="close")
    del window[101]

    assert is_bar_rejected(window, target_time_id=102, thresholds=OutlierThresholds()) is True


def test_missing_forward_neighbor_uses_one_sided_boundary_check():
    # No entry at 103 (e.g. target is the last bar of a session segment) -- one-sided check using
    # just the backward diff. Same 5.0 magnitude/ratio as the backward case above.
    window = _quiet_window(target_time_id=102)
    _inject_target_diffs(window, target_time_id=102, diff_back=5.0, diff_fwd=0.0, field="close")
    del window[103]

    assert is_bar_rejected(window, target_time_id=102, thresholds=OutlierThresholds()) is True


def test_one_sided_boundary_check_not_rejected_for_small_diff():
    # Same missing-neighbor shape as above, but a small 0.05 diff (ratio ~10) stays under
    # k_boundary_oc=20 -- proving the one-sided path is a real threshold check, not an automatic
    # rejection whenever a neighbor happens to be missing.
    window = _quiet_window(target_time_id=102)
    _inject_target_diffs(window, target_time_id=102, diff_back=0.05, diff_fwd=0.0, field="close")
    del window[103]

    assert is_bar_rejected(window, target_time_id=102, thresholds=OutlierThresholds()) is False


def test_both_neighbors_missing_not_rejected():
    # Neither side available at all -- nothing to compare the target against, regardless of how
    # implausible its own value might look in isolation.
    window = {102: _bar(102, close=100.0)}

    assert is_bar_rejected(window, target_time_id=102, thresholds=OutlierThresholds()) is False


def test_trend_shape_uses_looser_threshold_than_reversal():
    # Same-sign diffs (a continuing trend, not a spike-then-revert): magnitude 2.0 against the
    # same ~0.005 background MAD gives ratio ~400 -- clears k_reversal_oc=300 but stays under the
    # looser k_trend_oc=600, so the shape classification (not just the move size) is what matters.
    window = _quiet_window(target_time_id=102)
    _inject_target_diffs(window, target_time_id=102, diff_back=2.0, diff_fwd=1.8, field="close")

    assert is_bar_rejected(window, target_time_id=102, thresholds=OutlierThresholds()) is False


def test_any_single_field_spiking_rejects_the_whole_bar():
    # Only `low` spikes (same reversal shape as test_reversal_spike_is_rejected, sized to clear
    # HL's looser k_reversal_hl=400); open/high/close stay on their own separate quiet baseline.
    window = _quiet_window(target_time_id=102, field="low")
    _inject_target_diffs(window, target_time_id=102, diff_back=4.0, diff_fwd=-4.0, field="low")

    assert is_bar_rejected(window, target_time_id=102, thresholds=OutlierThresholds()) is True


def test_custom_thresholds_are_actually_used():
    window = _quiet_window(target_time_id=102)
    _inject_target_diffs(window, target_time_id=102, diff_back=3.0, diff_fwd=-3.0, field="close")

    # Same spike as test_reversal_spike_is_rejected, which flags under the defaults -- an
    # absurdly loose custom k_reversal_oc should suppress it, proving the thresholds parameter
    # (not a hardcoded constant) actually governs the decision.
    loose_thresholds = OutlierThresholds(k_reversal_oc=1_000_000.0)
    assert is_bar_rejected(window, target_time_id=102, thresholds=loose_thresholds) is False


def test_insufficient_background_sample_not_rejected():
    # Only the 3-bar minimum (back_one, target, fwd_one) -- no wider background at all. Must not
    # crash, and can't be evaluated (MIN_BACKGROUND_SAMPLE unmet), so this returns False even
    # though the move itself (a full spike-then-revert) would clear every threshold if it could be
    # evaluated -- proving the guard is about sample sufficiency, not the move being genuinely
    # implausible-looking.
    window = {
        101: _bar(101, close=100.00),
        102: _bar(102, close=103.00),
        103: _bar(103, close=100.00),
    }

    assert is_bar_rejected(window, target_time_id=102, thresholds=OutlierThresholds()) is False
