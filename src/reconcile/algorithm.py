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
RESOLUTION_FINALIZED = "finalized"
RESOLUTION_MANUAL_OVERRIDE = "manual_override"

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


def fields_for_group(field_group: str) -> list[str]:
    return _GROUP_FIELDS[field_group]


def _reference_value(bar: ProviderBar, field_group: str) -> float:
    fields = fields_for_group(field_group)
    total = 0.0
    for field_name in fields:
        total += getattr(bar, field_name)
    return total / len(fields)


def _tolerance(candidate: ProviderBar, field_group: str, stddev: float, k: float) -> float:
    return k * stddev * _reference_value(candidate, field_group)


def _agrees_within_tolerance(
    candidate: ProviderBar,
    whistleblower: ProviderBar,
    field_group: str,
    field_tolerances: dict[str, float],
    k: float,
) -> bool:
    """Every field independently within its own tolerance -- not "max diff across the group
    within one tolerance" (see croicu/quant-data#28's "Pooled across fields" finding). OHLC stays
    one atomic promotion unit; only this comparison is per-field."""
    for field_name in fields_for_group(field_group):
        stddev = field_tolerances.get(field_name)
        if stddev is None:
            return False
        diff = abs(getattr(candidate, field_name) - getattr(whistleblower, field_name))
        if diff > _tolerance(candidate, field_group, stddev, k):
            return False
    return True


def _find_whistleblower(bars: list[ProviderBar]) -> ProviderBar | None:
    for bar in bars:
        if bar.role == ROLE_WHISTLEBLOWER:
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


def _resolve_agreement(
    bars: list[ProviderBar],
    field_group: str,
    tolerances: dict[int, dict[str, float]],
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
    field_tolerances: dict[str, float],
    k: float,
) -> bool:
    candidate_bar = candidate_window[1]
    if candidate_bar is None:
        return False

    for field_name in fields_for_group(field_group):
        stddev = field_tolerances.get(field_name)
        if stddev is None:
            return False
        candidate_avg = _windowed_average(candidate_window, field_name)
        whistleblower_avg = _windowed_average(whistleblower_window, field_name)
        if candidate_avg is None or whistleblower_avg is None:
            return False
        diff = abs(candidate_avg - whistleblower_avg)
        if diff > _tolerance(candidate_bar, field_group, stddev, k):
            return False

    return True


def _resolve_boundary_fix(
    bars: list[ProviderBar],
    windows: dict[int, list[ProviderBar | None]],
    field_group: str,
    tolerances: dict[int, dict[str, float]],
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


def resolve_automatic(
    bars: list[ProviderBar],
    field_group: str,
    windows: dict[int, list[ProviderBar | None]],
    tolerances: dict[int, dict[str, float]],
    k: float,
    preferred_provider_id: int | None,
) -> Resolution | None:
    """Tiers 1-3, first one that resolves wins. None means still stuck (Tier 4) -- left in
    staging for a person to look at before --finalize.

    `windows` maps provider_id -> [bar at t-1, bar at t, bar at t+1] (None entries where that
    neighbor minute has no staging row for that provider) -- only consulted by Tier 3.
    """
    resolution = _resolve_completeness(bars)
    if resolution is not None:
        return resolution

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
