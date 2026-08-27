"""Pure reconciliation logic -- no database access, so it's directly unit-testable.

Per tasks/quant-reconcile.md: for one (bar, field group), decide whether a candidate provider's
value can be promoted to fact_market_data_1min, and if so via which tier/path. Two entry points:

- resolve_automatic(): the automatic pass, Tiers 1-3 (completeness / agreement / boundary-fix).
  Returns None if the group is still unresolved (Tier 4 -- left in staging).
- resolve_finalize(): --finalize's fallback, promoting the preferred provider's raw value for
  whatever resolve_automatic() couldn't resolve.
"""

from __future__ import annotations

from dataclasses import dataclass

FIELD_GROUP_OHLC = "ohlc"

ROLE_CANDIDATE = "candidate"
ROLE_WHISTLEBLOWER = "whistleblower"

DATA_QUALITY_ACCEPTED = "accepted"
DATA_QUALITY_INCOMPLETE = "incomplete"
DATA_QUALITY_REJECTED = "rejected"

RESOLUTION_COMPLETENESS = "completeness"
RESOLUTION_AGREEMENT = "agreement"
RESOLUTION_BOUNDARY_FIX = "boundary_fix"
RESOLUTION_UNADJUDICATED = "unadjudicated"
RESOLUTION_HISTORICAL_MAD_AGREEMENT = "historical_mad_agreement"
RESOLUTION_FINALIZED = "finalized"
RESOLUTION_MANUAL_OVERRIDE = "manual_override"

FLOOR_TYPE_ABSOLUTE = "absolute"
FLOOR_TYPE_BPS_OF_REFERENCE = "bps_of_reference"

# A ticker below this many matched bars (every configured provider reported real, data_quality =
# accepted data for that minute) sits in staging completely unevaluated -- no Tier 1-4 attempt, no
# partial stats update -- until it graduates in one batch (croicu/quant-data#28).
GRADUATION_THRESHOLD_MATCHED_BARS = 1400

_GROUP_FIELDS: dict[str, list[str]] = {
    FIELD_GROUP_OHLC: ["open", "high", "low", "close"],
}


@dataclass
class ProviderBar:
    provider_id: int
    provider_name: str
    role: str  # ROLE_CANDIDATE or ROLE_WHISTLEBLOWER
    open: float
    high: float
    low: float
    close: float
    volume: float
    data_quality: str


@dataclass
class Resolution:
    winning_provider_id: int
    resolution_path: str


@dataclass
class DisagreementStats:
    sample_count: int
    running_mean: float
    running_m2: float


@dataclass
class FieldTolerance:
    """One field's Tier 2/3 input: the measured stddev feeding k * stddev * reference_value, plus
    an optional materiality floor (see tasks/materiality_floor_tolerance.md) bounding that computed
    tolerance below. floor_value defaults to 0.0 -- no floor, current behavior unchanged --
    matching a (provider, ticker, field) with no materiality_floor row."""

    stddev: float
    floor_value: float = 0.0
    floor_type: str = FLOOR_TYPE_ABSOLUTE


@dataclass
class FieldMadBand:
    """One field's pooled, fixed conditional-MAD band for the historical (no-whistleblower)
    period -- see tasks/ibkr_massive_mad_calibration.md (E0-E8) and
    tasks/retroactive_revision.md. `conditional_mad_scaled` is `1.4826 * median(|d|)` computed
    only among nonzero candidate-pair differences (excludes the exact-match point mass, same
    "conditional MAD" convention the calibration task used throughout); `k` multiplies it into an
    actual tolerance. Deliberately pooled over the full historical range and fixed, not rolled per
    period -- rolling this value would make drift structurally undetectable (see that task's R3a
    finding)."""

    conditional_mad_scaled: float
    k: float


def fields_for_group(field_group: str) -> list[str]:
    return _GROUP_FIELDS[field_group]


def _reference_value(bar: ProviderBar, field_group: str) -> float:
    fields = fields_for_group(field_group)
    total = 0.0
    for field_name in fields:
        total += getattr(bar, field_name)
    return total / len(fields)


def _materiality_floor(candidate: ProviderBar, field_group: str, field_tolerance: FieldTolerance) -> float:
    if field_tolerance.floor_type == FLOOR_TYPE_BPS_OF_REFERENCE:
        reference_value = _reference_value(candidate, field_group)
        return field_tolerance.floor_value * reference_value / 10000.0
    return field_tolerance.floor_value


def _tolerance(candidate: ProviderBar, field_group: str, field_tolerance: FieldTolerance, k: float) -> float:
    reference_value = _reference_value(candidate, field_group)
    computed = k * field_tolerance.stddev * reference_value
    floor = _materiality_floor(candidate, field_group, field_tolerance)
    return max(computed, floor)


def _agrees_within_tolerance(
    candidate: ProviderBar,
    whistleblower: ProviderBar,
    field_group: str,
    field_tolerances: dict[str, FieldTolerance],
    k: float,
) -> bool:
    """Every field independently within its own tolerance -- not "max diff across the group
    within one tolerance" (see croicu/quant-data#28's "Pooled across fields" finding). OHLC stays
    one atomic promotion unit; only this comparison is per-field."""
    for field_name in fields_for_group(field_group):
        field_tolerance = field_tolerances.get(field_name)
        if field_tolerance is None:
            return False
        diff = abs(getattr(candidate, field_name) - getattr(whistleblower, field_name))
        if diff > _tolerance(candidate, field_group, field_tolerance, k):
            return False
    return True


def _find_whistleblower(bars: list[ProviderBar]) -> ProviderBar | None:
    """Only an ACCEPTED whistleblower counts as a usable adjudicator -- an outlier-REJECTED or
    confirmed-absent/INCOMPLETE whistleblower row is a known-bad or synthetic placeholder value,
    never a real reference. With exactly one candidate this distinction was moot (Tier 1
    completeness always resolved the bar first, before Tier 2/3 could ever look at the
    whistleblower's own data_quality); with two or more candidates present it stops being moot --
    see resolve_automatic's use of this filtered result to fall through to
    RESOLUTION_UNADJUDICATED instead of ever comparing a candidate against a value already known
    to be wrong."""
    for bar in bars:
        if bar.role == ROLE_WHISTLEBLOWER and bar.data_quality == DATA_QUALITY_ACCEPTED:
            return bar
    return None


def _candidates(bars: list[ProviderBar]) -> list[ProviderBar]:
    result: list[ProviderBar] = []
    for bar in bars:
        if bar.role == ROLE_CANDIDATE:
            result.append(bar)
    return result


def _pick_preferred(agreeing: list[ProviderBar], preferred_provider_id: int | None) -> ProviderBar:
    if preferred_provider_id is not None:
        for bar in agreeing:
            if bar.provider_id == preferred_provider_id:
                return bar
    return agreeing[0]


def _resolve_completeness(bars: list[ProviderBar]) -> Resolution | None:
    valid: list[ProviderBar] = []
    invalid: list[ProviderBar] = []
    for bar in bars:
        if bar.data_quality != DATA_QUALITY_ACCEPTED:
            invalid.append(bar)
        else:
            valid.append(bar)

    if len(valid) == 1 and invalid:
        winner = valid[0]
        if winner.role == ROLE_CANDIDATE:
            return Resolution(winning_provider_id=winner.provider_id, resolution_path=RESOLUTION_COMPLETENESS)
    return None


def _candidates_disagree_materially(
    winner: ProviderBar,
    agreeing: list[ProviderBar],
    field_group: str,
    tolerances: dict[int, dict[str, FieldTolerance]],
) -> bool:
    """True if any other agreeing candidate diverges from the winner by more than the winner's own
    materiality floor for that field (croicu/quant-data#50). Both candidates already individually
    passed their own tolerance-vs-whistleblower check -- this catches the case where that's true
    but they still disagree with *each other* by an economically meaningful amount, which the
    preferredProvider tiebreak would otherwise paper over silently. Reuses the winner's own
    already-computed materiality floor rather than a new stat -- a floor of 0.0 (unconfigured for
    this (winner, ticker, field)) means the check simply doesn't engage for that field, preserving
    today's tiebreak behavior wherever no floor was ever seeded."""
    winner_field_tolerances = tolerances.get(winner.provider_id)
    if winner_field_tolerances is None:
        return False
    for other in agreeing:
        if other.provider_id == winner.provider_id:
            continue
        for field_name in fields_for_group(field_group):
            field_tolerance = winner_field_tolerances.get(field_name)
            if field_tolerance is None:
                continue
            floor = _materiality_floor(winner, field_group, field_tolerance)
            if floor <= 0:
                continue
            diff = abs(getattr(other, field_name) - getattr(winner, field_name))
            if diff > floor:
                return True
    return False


def _resolve_agreement(
    bars: list[ProviderBar],
    field_group: str,
    tolerances: dict[int, dict[str, FieldTolerance]],
    k: float,
    preferred_provider_id: int | None,
) -> Resolution | None:
    whistleblower = _find_whistleblower(bars)
    if whistleblower is None:
        return None

    agreeing: list[ProviderBar] = []
    for candidate in _candidates(bars):
        field_tolerances = tolerances.get(candidate.provider_id)
        if field_tolerances is None:
            continue
        if _agrees_within_tolerance(candidate, whistleblower, field_group, field_tolerances, k):
            agreeing.append(candidate)

    if not agreeing:
        return None
    winner = _pick_preferred(agreeing, preferred_provider_id)
    if len(agreeing) > 1 and _candidates_disagree_materially(winner, agreeing, field_group, tolerances):
        return None
    return Resolution(winning_provider_id=winner.provider_id, resolution_path=RESOLUTION_AGREEMENT)


def _windowed_average(bars: list[ProviderBar | None], field_name: str) -> float | None:
    if len(bars) != 3 or None in bars:
        return None
    total = 0.0
    for bar in bars:
        total += getattr(bar, field_name)
    return total / 3


def _windowed_agrees(
    candidate_window: list[ProviderBar | None],
    whistleblower_window: list[ProviderBar | None],
    field_group: str,
    field_tolerances: dict[str, FieldTolerance],
    k: float,
) -> bool:
    candidate_bar = candidate_window[1]
    if candidate_bar is None:
        return False

    for field_name in fields_for_group(field_group):
        field_tolerance = field_tolerances.get(field_name)
        if field_tolerance is None:
            return False
        candidate_avg = _windowed_average(candidate_window, field_name)
        whistleblower_avg = _windowed_average(whistleblower_window, field_name)
        if candidate_avg is None or whistleblower_avg is None:
            return False
        diff = abs(candidate_avg - whistleblower_avg)
        if diff > _tolerance(candidate_bar, field_group, field_tolerance, k):
            return False

    return True


def _resolve_boundary_fix(
    bars: list[ProviderBar],
    windows: dict[int, list[ProviderBar | None]],
    field_group: str,
    tolerances: dict[int, dict[str, FieldTolerance]],
    k: float,
) -> Resolution | None:
    whistleblower = _find_whistleblower(bars)
    if whistleblower is None:
        return None
    whistleblower_window = windows.get(whistleblower.provider_id)
    if whistleblower_window is None:
        return None

    for candidate in _candidates(bars):
        field_tolerances = tolerances.get(candidate.provider_id)
        if field_tolerances is None:
            continue
        candidate_window = windows.get(candidate.provider_id)
        if candidate_window is None:
            continue
        if _windowed_agrees(candidate_window, whistleblower_window, field_group, field_tolerances, k):
            return Resolution(winning_provider_id=candidate.provider_id, resolution_path=RESOLUTION_BOUNDARY_FIX)

    return None


def _resolve_unadjudicated(bars: list[ProviderBar], preferred_provider_id: int | None) -> Resolution | None:
    """Fires only when Tier 1 didn't resolve (more than one valid candidate present, so
    completeness alone can't pick a winner) and no ACCEPTED whistleblower exists to adjudicate
    between them (checked by the caller via _find_whistleblower). Falls through to
    settings.reconcile.preferredProvider's raw value -- the same fallback --finalize's
    resolve_finalize uses, but tagged with its own resolution_path (RESOLUTION_UNADJUDICATED, not
    RESOLUTION_AGREEMENT) since no comparison against a reference value was ever attempted: reusing
    'agreement' here would corrupt provider_pair_disagreement's Welford variance (which only
    genuine Tier 2 in-band agreements should feed) and fact_reconciliation_participant's reputation
    trail (a non-winning candidate here didn't lose a comparison, it was never compared) with
    observations that carry no information. Returns None (still stuck, Tier 4) if
    preferredProvider itself didn't report as a candidate for this bar -- same defensive shape as
    resolve_finalize."""
    if preferred_provider_id is None:
        return None
    for bar in bars:
        if bar.provider_id == preferred_provider_id and bar.role == ROLE_CANDIDATE:
            return Resolution(winning_provider_id=bar.provider_id, resolution_path=RESOLUTION_UNADJUDICATED)
    return None


def _has_full_mad_band(field_group: str, mad_bands: dict[str, FieldMadBand]) -> bool:
    for field_name in fields_for_group(field_group):
        if field_name not in mad_bands:
            return False
    return True


def _resolve_historical_mad_agreement(
    bars: list[ProviderBar],
    field_group: str,
    mad_bands: dict[str, FieldMadBand],
    preferred_provider_id: int | None,
) -> Resolution | None:
    """Fires only when no ACCEPTED whistleblower exists (checked by the caller) and this ticker
    has a fully-seeded pooled conditional-MAD band for every field in field_group (checked by the
    caller via _has_full_mad_band) -- the historical-period stand-in validated by
    tasks/ibkr_massive_mad_calibration.md (E0-E8) and integrated per tasks/retroactive_revision.md.

    Deliberately requires exactly two candidates: the validated band is a pairwise formula
    (d = (a-b)/midpoint, matching .exp/budget/k_sweep.py exactly), not a generalized N-provider
    one -- with any other candidate count this returns None (stays stuck, Tier 4) rather than
    guessing at an ungeneralized formula. Today's production candidate set (ibkr, massive) is
    always exactly two, so this is not a practical limitation, just an honest one.

    Promotes preferredProvider on agreement -- the same winner _resolve_unadjudicated would have
    picked, now actually checked -- under its own resolution_path, distinct from 'agreement'
    (which feeds provider_pair_disagreement's Welford variance; this must not, since there is no
    whistleblower observation to record) and from 'unadjudicated' (no check attempted at all).

    Unlike _resolve_unadjudicated, disagreement beyond the band does NOT fall back to blind
    promotion -- it returns None so the bar stays in staging for a person to review via
    --finalize, same as any other Tier 4 bar."""
    candidates = _candidates(bars)
    if len(candidates) != 2:
        return None

    first, second = candidates
    for field_name in fields_for_group(field_group):
        band = mad_bands[field_name]
        first_value = getattr(first, field_name)
        second_value = getattr(second, field_name)
        reference = (first_value + second_value) / 2.0
        diff = abs(first_value - second_value) / reference
        if diff > band.k * band.conditional_mad_scaled:
            return None

    if preferred_provider_id is None:
        return None
    for candidate in candidates:
        if candidate.provider_id == preferred_provider_id:
            return Resolution(winning_provider_id=candidate.provider_id, resolution_path=RESOLUTION_HISTORICAL_MAD_AGREEMENT)
    return None


def resolve_automatic(
    bars: list[ProviderBar],
    field_group: str,
    windows: dict[int, list[ProviderBar | None]],
    tolerances: dict[int, dict[str, FieldTolerance]],
    k: float,
    preferred_provider_id: int | None,
    mad_bands: dict[str, FieldMadBand] | None = None,
) -> Resolution | None:
    """Tiers 1-3, first one that resolves wins. None means still stuck (Tier 4) -- left in
    staging for a person to look at before --finalize.

    `windows` maps provider_id -> [bar at t-1, bar at t, bar at t+1] (None entries where that
    neighbor minute has no staging row for that provider) -- only consulted by Tier 3.

    `mad_bands` (field name -> FieldMadBand) is this bar's ticker's historical MAD band, if any
    has been seeded (tasks/retroactive_revision.md) -- defaults to None/empty for callers that
    don't pass one, which preserves today's behavior exactly (falls straight through to
    _resolve_unadjudicated, same as before this parameter existed).
    """
    resolution = _resolve_completeness(bars)
    if resolution is not None:
        return resolution

    if _find_whistleblower(bars) is None:
        # No ACCEPTED whistleblower to adjudicate between two or more valid candidates (Tier 1
        # already ruled out the single-candidate case above) -- Tiers 2/3 would only ever compare
        # against a known-bad or synthetic placeholder value, so skip straight to either the
        # historical MAD band (if this ticker has one) or the preferredProvider fallback, rather
        # than risk a false agreement/disagreement against the whistleblower.
        if mad_bands is not None and _has_full_mad_band(field_group, mad_bands):
            return _resolve_historical_mad_agreement(bars, field_group, mad_bands, preferred_provider_id)
        return _resolve_unadjudicated(bars, preferred_provider_id)

    resolution = _resolve_agreement(bars, field_group, tolerances, k, preferred_provider_id)
    if resolution is not None:
        return resolution

    return _resolve_boundary_fix(bars, windows, field_group, tolerances, k)


def resolve_finalize(bars: list[ProviderBar], preferred_provider_id: int) -> Resolution | None:
    """--finalize's fallback: promote preferredProvider's raw value outright, no tolerance
    check. None only if preferredProvider itself never reported for this bar (shouldn't happen
    given ingest waits for every configured provider, but defended against regardless)."""
    for bar in bars:
        if bar.provider_id == preferred_provider_id and bar.role == ROLE_CANDIDATE:
            return Resolution(winning_provider_id=bar.provider_id, resolution_path=RESOLUTION_FINALIZED)
    return None


def relative_diffs_for_stats_update(candidate: ProviderBar, whistleblower: ProviderBar, field_group: str) -> list[float]:
    """One relative (candidate - whistleblower) / reference_value diff per field in the group --
    only meaningful to call after an RESOLUTION_AGREEMENT resolution (see
    tasks/quant-reconcile.md's "Only Tier 2 observations update the rolling variance")."""
    reference_value = _reference_value(candidate, field_group)
    if reference_value == 0:
        return []

    diffs: list[float] = []
    for field_name in fields_for_group(field_group):
        diffs.append((getattr(candidate, field_name) - getattr(whistleblower, field_name)) / reference_value)
    return diffs


def welford_update(stats: DisagreementStats, observation: float) -> DisagreementStats:
    n = stats.sample_count + 1
    delta = observation - stats.running_mean
    new_mean = stats.running_mean + delta / n
    delta2 = observation - new_mean
    new_m2 = stats.running_m2 + delta * delta2
    return DisagreementStats(sample_count=n, running_mean=new_mean, running_m2=new_m2)


def stddev_from_stats(stats: DisagreementStats) -> float:
    if stats.sample_count == 0:
        return 0.0
    variance = stats.running_m2 / stats.sample_count
    return variance**0.5


def batch_stats(observations: list[float]) -> DisagreementStats:
    """Computes mean/variance over a full batch in one pass -- used only at graduation, when a
    ticker's first stats are a real batch of GRADUATION_THRESHOLD_MATCHED_BARS actually-observed
    bars rather than an incrementally-seeded value (croicu/quant-data#28's "no seeds, anywhere,
    ever"). Built on welford_update so the result is identical to updating one observation at a
    time -- order of arrival doesn't matter."""
    stats = DisagreementStats(sample_count=0, running_mean=0.0, running_m2=0.0)
    for observation in observations:
        stats = welford_update(stats, observation)
    return stats
