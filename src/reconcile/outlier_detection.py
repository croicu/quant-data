"""Pure per-provider staging-quality (outlier) detection -- no database access, directly
unit-testable. See tasks/yahoo_data_sanitization.md for the full converged design.

A field (open/high/low/close) is flagged implausible if its immediate backward and forward
bar-to-bar diffs are large relative to a MAD-based local scale -- a tight threshold when the two
diffs point in opposite directions (a reversal/spike shape, the pattern behind every confirmed
case so far), a loose one when they point the same direction (a persisting trend, more likely a
real price move than a bad tick). A bar is REJECTED if any of its four fields is flagged.

The MAD reference scale is deliberately built from a wide (+/- BACKGROUND_HALF_WINDOW_MINUTES)
neighborhood of *background* consecutive-pair diffs, excluding the two diffs that touch the
target itself (back_one->target, target->fwd_one -- the ones being tested). An earlier, narrower
design (+/-2 minutes, target's own diffs included in the reference) was self-contaminating: a
genuinely large target move inflated the very scale used to judge it, and two coincidentally
similar background diffs elsewhere could collapse the scale to near-zero -- both failure modes
produced real false positives/negatives on live data (see tasks/yahoo_data_sanitization.md's
2026-08-06/07 recalibration). A properly external, properly sized reference sample fixes both at
once, without needing any per-ticker exemption.

A bar with only one usable immediate neighbor (the other side missing -- either because it's the
literal first/last bar of a session segment, so the other side belongs to a different segment by
construction, or because that specific minute happens to be absent/incomplete) is evaluated with a
one-sided check against k_boundary_* instead of being skipped outright: no reversal/trend shape
can be classified with a single diff, so this uses a single, dedicated threshold rather than
either of the two-sided ones. reconcile/cli.py's window-building step is what actually keeps this
from ever comparing across a real session boundary (9:30 open / 16:00 close) -- see its
"frozen tail window" logic for the last/first BACKGROUND_HALF_WINDOW_MINUTES of each segment.

Deliberately intra-provider only -- never compares against another provider's value. The whole
point is vetting the whistleblower's own signal independently of the thing it's meant to check.
"""

from __future__ import annotations

from dataclasses import dataclass
from statistics import median

DEFAULT_K_REVERSAL_OC = 300.0
DEFAULT_K_TREND_OC = 600.0
DEFAULT_K_REVERSAL_HL = 400.0
DEFAULT_K_TREND_HL = 800.0
# Much smaller than the two-sided constants above despite guarding the same kind of bad tick --
# real-data calibration (2026-08-07, the last/first bar of every session segment, hl field) put a
# confirmed bad case (SPY 2026-07-29 16:00 ET, a stuck 'high') at the p99 ratio (~40), with the
# bulk of genuine boundary bars sitting at p97.5 (~16) or below. The two-sided constants were
# tuned against a *self-referential* background (the target bar's own diffs partly form the
# scale being judged against); the one-sided background here is purely external -- 30-40 real
# same-segment diffs, not the 2-4-sample estimate the two-sided constants had to be loose enough
# to tolerate -- so a much tighter multiplier is both possible and necessary to catch anything.
# k_boundary_hl lowered 25 -> 20 the same day after a second confirmed case (SPY 2026-07-28 16:00
# ET, a bad 'low') scored only ~21 -- below 25, above p97.5 (~16). That case was independently
# confirmed bad against DataBento well before this detector existed (tasks/finalize_targeted_promotion.md),
# so it's real signal, not noise; accepting a higher false-positive rate on this narrower boundary
# check (roughly the p95-p97.5 band, ~2.5-5%, up from ~1%) to catch it was a deliberate choice.
DEFAULT_K_BOUNDARY_OC = 20.0
DEFAULT_K_BOUNDARY_HL = 20.0

BACKGROUND_HALF_WINDOW_MINUTES = 20
MIN_BACKGROUND_SAMPLE = 3


@dataclass
class OutlierThresholds:
    k_reversal_oc: float = DEFAULT_K_REVERSAL_OC
    k_trend_oc: float = DEFAULT_K_TREND_OC
    k_reversal_hl: float = DEFAULT_K_REVERSAL_HL
    k_trend_hl: float = DEFAULT_K_TREND_HL
    k_boundary_oc: float = DEFAULT_K_BOUNDARY_OC
    k_boundary_hl: float = DEFAULT_K_BOUNDARY_HL


@dataclass
class OutlierBar:
    """One provider's staging value at one minute, for outlier-window purposes."""

    time_id: int
    open: float
    high: float
    low: float
    close: float


def _median_absolute_deviation(values: list[float]) -> float:
    center = median(values)
    deviations: list[float] = []
    for value in values:
        deviations.append(abs(value - center))
    return median(deviations)


def is_bar_rejected(window: dict[int, OutlierBar], target_time_id: int, thresholds: OutlierThresholds) -> bool:
    """window maps time_id -> OutlierBar for whatever same-session-segment bars are available in
    a +/- BACKGROUND_HALF_WINDOW_MINUTES neighborhood around the target. The caller is responsible
    for only including entries that are (a) real, present data (not a gap) and (b) in the same
    session segment as the target -- never crossing the 9:30 open or 16:00 close transitions even
    when real data exists on both sides, since that price step is a real regime change, not noise
    (see reconcile/cli.py's window-building step).
    """
    target = window.get(target_time_id)
    if target is None:
        return False

    back_one = window.get(target_time_id - 1)
    fwd_one = window.get(target_time_id + 1)
    if back_one is None and fwd_one is None:
        return False  # no usable neighbor on either side -- nothing to compare against

    sorted_ids = sorted(window.keys())

    field_specs = (
        ("open", thresholds.k_reversal_oc, thresholds.k_trend_oc, thresholds.k_boundary_oc),
        ("close", thresholds.k_reversal_oc, thresholds.k_trend_oc, thresholds.k_boundary_oc),
        ("high", thresholds.k_reversal_hl, thresholds.k_trend_hl, thresholds.k_boundary_hl),
        ("low", thresholds.k_reversal_hl, thresholds.k_trend_hl, thresholds.k_boundary_hl),
    )

    for field_name, k_reversal, k_trend, k_boundary in field_specs:
        target_value = getattr(target, field_name)
        diff_back = target_value - getattr(back_one, field_name) if back_one is not None else None
        diff_fwd = getattr(fwd_one, field_name) - target_value if fwd_one is not None else None

        background: list[float] = []
        for index in range(len(sorted_ids) - 1):
            left_id = sorted_ids[index]
            right_id = sorted_ids[index + 1]
            if right_id != left_id + 1:
                continue  # a gap in the window, not a real consecutive pair
            if left_id == target_time_id - 1 or left_id == target_time_id:
                continue  # excludes the diff(s) that touch the target itself
            background.append(getattr(window[right_id], field_name) - getattr(window[left_id], field_name))

        if len(background) < MIN_BACKGROUND_SAMPLE:
            continue  # not enough real background data to trust the MAD estimate

        mad = _median_absolute_deviation(background)
        if mad <= 0:
            continue

        if diff_back is not None and diff_fwd is not None:
            is_reversal = (diff_back > 0) != (diff_fwd > 0)
            k = k_reversal if is_reversal else k_trend
            magnitude = max(abs(diff_back), abs(diff_fwd))
        else:
            # Only one side available -- no reversal/trend shape to classify, so judge the single
            # available diff against the dedicated (looser) boundary threshold instead.
            k = k_boundary
            magnitude = abs(diff_back) if diff_back is not None else abs(diff_fwd)

        if magnitude > k * mad:
            return True

    return False
