from __future__ import annotations

import math

from reconcile.algorithm import (
    FIELD_GROUP_OHLC,
    RESOLUTION_AGREEMENT,
    RESOLUTION_BOUNDARY_FIX,
    RESOLUTION_COMPLETENESS,
    RESOLUTION_FINALIZED,
    ROLE_CANDIDATE,
    ROLE_WHISTLEBLOWER,
    DisagreementStats,
    ProviderBar,
    relative_diffs_for_stats_update,
    resolve_automatic,
    resolve_finalize,
    stddev_from_stats,
    welford_update,
)

IBKR = 1
YFINANCE = 2


def _uniform_tolerance(stddev: float) -> dict[str, float]:
    return {"open": stddev, "high": stddev, "low": stddev, "close": stddev}


def _bar(
    provider_id: int,
    role: str,
    open_: float = 100.0,
    high: float = 101.0,
    low: float = 99.0,
    close: float = 100.5,
    volume: float = 1000,
    data_quality: str = "accepted",
) -> ProviderBar:
    return ProviderBar(
        provider_id=provider_id,
        provider_name="ibkr" if provider_id == IBKR else "yfinance",
        role=role,
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=volume,
        data_quality=data_quality,
    )


def test_completeness_promotes_candidate_when_whistleblower_incomplete():
    bars = [
        _bar(IBKR, ROLE_CANDIDATE),
        _bar(YFINANCE, ROLE_WHISTLEBLOWER, data_quality="incomplete"),
    ]

    resolution = resolve_automatic(bars, FIELD_GROUP_OHLC, windows={}, tolerances={IBKR: _uniform_tolerance(0.001)}, k=3.0, preferred_provider_id=None)

    assert resolution is not None
    assert resolution.winning_provider_id == IBKR
    assert resolution.resolution_path == RESOLUTION_COMPLETENESS


def test_completeness_does_not_resolve_when_candidate_incomplete():
    # Whistleblower can never win via completeness -- candidate being the incomplete one just
    # falls through (to agreement, which also fails here since the candidate's garbage value
    # genuinely diverges from the whistleblower's).
    bars = [
        _bar(IBKR, ROLE_CANDIDATE, open_=0.0, high=0.0, low=0.0, close=0.0, data_quality="incomplete"),
        _bar(YFINANCE, ROLE_WHISTLEBLOWER, open_=100.0, high=101.0, low=99.0, close=100.5),
    ]

    resolution = resolve_automatic(bars, FIELD_GROUP_OHLC, windows={}, tolerances={IBKR: _uniform_tolerance(0.001)}, k=3.0, preferred_provider_id=None)

    assert resolution is None


def test_agreement_promotes_candidate_within_tolerance():
    bars = [
        _bar(IBKR, ROLE_CANDIDATE, open_=100.0, high=101.0, low=99.0, close=100.5),
        _bar(YFINANCE, ROLE_WHISTLEBLOWER, open_=100.01, high=101.01, low=99.01, close=100.51),
    ]

    resolution = resolve_automatic(bars, FIELD_GROUP_OHLC, windows={}, tolerances={IBKR: _uniform_tolerance(0.01)}, k=3.0, preferred_provider_id=None)

    assert resolution is not None
    assert resolution.winning_provider_id == IBKR
    assert resolution.resolution_path == RESOLUTION_AGREEMENT


def test_agreement_fails_outside_tolerance_and_falls_through():
    bars = [
        _bar(IBKR, ROLE_CANDIDATE, open_=100.0, high=101.0, low=99.0, close=100.5),
        _bar(YFINANCE, ROLE_WHISTLEBLOWER, open_=110.0, high=111.0, low=109.0, close=110.5),
    ]

    resolution = resolve_automatic(bars, FIELD_GROUP_OHLC, windows={}, tolerances={IBKR: _uniform_tolerance(0.0001)}, k=3.0, preferred_provider_id=None)

    assert resolution is None


def test_agreement_fails_when_only_one_field_exceeds_its_own_tolerance():
    # open/low/close are all within a tight learned tolerance; high alone is well outside its own
    # (also tight) tolerance -- must fail Tier 2 entirely, since OHLC stays one atomic promotion
    # unit even though the comparison itself is now per-field (croicu/quant-data#28).
    bars = [
        _bar(IBKR, ROLE_CANDIDATE, open_=100.0, high=105.0, low=99.0, close=100.5),
        _bar(YFINANCE, ROLE_WHISTLEBLOWER, open_=100.0, high=101.0, low=99.0, close=100.5),
    ]

    resolution = resolve_automatic(bars, FIELD_GROUP_OHLC, windows={}, tolerances={IBKR: _uniform_tolerance(0.001)}, k=3.0, preferred_provider_id=None)

    assert resolution is None


def test_agreement_checks_each_field_against_its_own_learned_tolerance():
    # high/low have a wide learned tolerance (noisy field), open/close have a tight one (stable
    # field) -- the candidate's high/low diverge more than open/close would tolerate, but agrees
    # because each field is checked against its own band, not a pooled group-wide one.
    bars = [
        _bar(IBKR, ROLE_CANDIDATE, open_=100.0, high=102.0, low=98.0, close=100.5),
        _bar(YFINANCE, ROLE_WHISTLEBLOWER, open_=100.0, high=101.0, low=99.0, close=100.5),
    ]

    tolerances = {IBKR: {"open": 0.0001, "high": 0.02, "low": 0.02, "close": 0.0001}}
    resolution = resolve_automatic(bars, FIELD_GROUP_OHLC, windows={}, tolerances=tolerances, k=3.0, preferred_provider_id=None)

    assert resolution is not None
    assert resolution.winning_provider_id == IBKR
    assert resolution.resolution_path == RESOLUTION_AGREEMENT


def test_agreement_fails_when_a_field_has_no_learned_tolerance_yet():
    # Only open/high/low have stats so far; close is missing -- must fail closed (no data means
    # no agreement), not silently skip the ungrounded field.
    bars = [
        _bar(IBKR, ROLE_CANDIDATE, open_=100.0, high=101.0, low=99.0, close=100.5),
        _bar(YFINANCE, ROLE_WHISTLEBLOWER, open_=100.0, high=101.0, low=99.0, close=100.5),
    ]

    tolerances = {IBKR: {"open": 0.01, "high": 0.01, "low": 0.01}}
    resolution = resolve_automatic(bars, FIELD_GROUP_OHLC, windows={}, tolerances=tolerances, k=3.0, preferred_provider_id=None)

    assert resolution is None


def test_boundary_fix_promotes_candidates_raw_value_when_windowed_averages_agree():
    # Raw values disagree (a boundary-misalignment artifact), but the 3-bar windowed average
    # matches within tolerance -- Tier 3 should promote the candidate's RAW (unaveraged) value.
    candidate_t = _bar(IBKR, ROLE_CANDIDATE, open_=100.0, high=101.0, low=99.0, close=100.5)
    whistleblower_t = _bar(YFINANCE, ROLE_WHISTLEBLOWER, open_=105.0, high=106.0, low=104.0, close=105.5)
    bars = [candidate_t, whistleblower_t]

    candidate_window = [
        _bar(IBKR, ROLE_CANDIDATE, open_=102.5, high=103.5, low=101.5, close=103.0),
        candidate_t,
        _bar(IBKR, ROLE_CANDIDATE, open_=105.0, high=106.0, low=104.0, close=105.5),
    ]
    whistleblower_window = [
        _bar(YFINANCE, ROLE_WHISTLEBLOWER, open_=100.0, high=101.0, low=99.0, close=100.5),
        whistleblower_t,
        _bar(YFINANCE, ROLE_WHISTLEBLOWER, open_=102.5, high=103.5, low=101.5, close=103.0),
    ]
    windows = {IBKR: candidate_window, YFINANCE: whistleblower_window}

    resolution = resolve_automatic(bars, FIELD_GROUP_OHLC, windows=windows, tolerances={IBKR: _uniform_tolerance(0.01)}, k=3.0, preferred_provider_id=None)

    assert resolution is not None
    assert resolution.winning_provider_id == IBKR
    assert resolution.resolution_path == RESOLUTION_BOUNDARY_FIX


def test_boundary_fix_does_not_apply_when_a_neighbor_bar_is_missing():
    candidate_t = _bar(IBKR, ROLE_CANDIDATE, open_=100.0, high=101.0, low=99.0, close=100.5)
    whistleblower_t = _bar(YFINANCE, ROLE_WHISTLEBLOWER, open_=110.0, high=111.0, low=109.0, close=110.5)
    bars = [candidate_t, whistleblower_t]

    # Missing t+1 for the candidate (e.g. last bar of the session) -- disagreement is real (not a
    # boundary artifact) so this must stay unresolved rather than being masked by a loose window.
    windows = {
        IBKR: [_bar(IBKR, ROLE_CANDIDATE, open_=100.0), candidate_t, None],
        YFINANCE: [
            _bar(YFINANCE, ROLE_WHISTLEBLOWER, open_=110.0),
            whistleblower_t,
            _bar(YFINANCE, ROLE_WHISTLEBLOWER, open_=110.0, high=111.0, low=109.0, close=110.5),
        ],
    }

    resolution = resolve_automatic(bars, FIELD_GROUP_OHLC, windows=windows, tolerances={IBKR: _uniform_tolerance(0.01)}, k=3.0, preferred_provider_id=None)

    assert resolution is None


def test_agreement_tie_breaks_via_preferred_provider():
    # Two candidates both agreeing with the whistleblower -- preferredProvider should win, not
    # simply whichever happens to be first.
    other_candidate_id = 3
    bars = [
        _bar(IBKR, ROLE_CANDIDATE, open_=100.0, high=101.0, low=99.0, close=100.5),
        ProviderBar(
            provider_id=other_candidate_id,
            provider_name="other",
            role=ROLE_CANDIDATE,
            open=100.0,
            high=101.0,
            low=99.0,
            close=100.5,
            volume=1000,
            data_quality="accepted",
        ),
        _bar(YFINANCE, ROLE_WHISTLEBLOWER, open_=100.0, high=101.0, low=99.0, close=100.5),
    ]

    resolution = resolve_automatic(
        bars,
        FIELD_GROUP_OHLC,
        windows={},
        tolerances={IBKR: _uniform_tolerance(0.01), other_candidate_id: _uniform_tolerance(0.01)},
        k=3.0,
        preferred_provider_id=other_candidate_id,
    )

    assert resolution is not None
    assert resolution.winning_provider_id == other_candidate_id


def test_resolve_finalize_promotes_preferred_provider():
    bars = [
        _bar(IBKR, ROLE_CANDIDATE, open_=100.0),
        _bar(YFINANCE, ROLE_WHISTLEBLOWER, open_=110.0),
    ]

    resolution = resolve_finalize(bars, preferred_provider_id=IBKR)

    assert resolution is not None
    assert resolution.winning_provider_id == IBKR
    assert resolution.resolution_path == RESOLUTION_FINALIZED


def test_resolve_finalize_returns_none_when_preferred_provider_did_not_report():
    bars = [_bar(YFINANCE, ROLE_WHISTLEBLOWER)]

    resolution = resolve_finalize(bars, preferred_provider_id=IBKR)

    assert resolution is None


def test_welford_update_matches_known_variance():
    # Population variance of [1, 2, 3, 4] is 1.25, stddev = sqrt(1.25).
    stats = DisagreementStats(sample_count=0, running_mean=0.0, running_m2=0.0)
    for observation in [1.0, 2.0, 3.0, 4.0]:
        stats = welford_update(stats, observation)

    assert stats.sample_count == 4
    assert math.isclose(stats.running_mean, 2.5)
    assert math.isclose(stddev_from_stats(stats), math.sqrt(1.25))


def test_stddev_from_stats_is_zero_with_no_samples():
    assert stddev_from_stats(DisagreementStats(sample_count=0, running_mean=0.0, running_m2=0.0)) == 0.0


def test_relative_diffs_for_stats_update_computes_one_per_field():
    candidate = _bar(IBKR, ROLE_CANDIDATE, open_=101.0, high=102.0, low=100.0, close=101.5)
    whistleblower = _bar(YFINANCE, ROLE_WHISTLEBLOWER, open_=100.0, high=100.0, low=100.0, close=100.0)

    diffs = relative_diffs_for_stats_update(candidate, whistleblower, FIELD_GROUP_OHLC)

    assert len(diffs) == 4


def test_relative_diffs_for_stats_update_avoids_division_by_zero():
    candidate = _bar(IBKR, ROLE_CANDIDATE, open_=0.0, high=0.0, low=0.0, close=0.0)
    whistleblower = _bar(YFINANCE, ROLE_WHISTLEBLOWER, open_=0.0, high=0.0, low=0.0, close=0.0)

    diffs = relative_diffs_for_stats_update(candidate, whistleblower, FIELD_GROUP_OHLC)

    assert diffs == []
