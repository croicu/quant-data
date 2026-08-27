from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from datetime import time as time_type
from pathlib import Path
from zoneinfo import ZoneInfo

from quant_data._internal.shared.diagnostics import ConsoleLogSink, Logger
from quant_data._internal.shared.errors import AppError
from quant_data._internal.shared.postgres import ArchivedCandidateBarRow, MaterialityFloorRow, PostgresDatabase, ProviderRow, StagingRow
from quant_data._internal.shared.settings import PostgresSettings, Settings
from quant_data._internal.shared.transports import resolve_transport
from reconcile.algorithm import (
    DATA_QUALITY_ACCEPTED,
    DATA_QUALITY_INCOMPLETE,
    FIELD_GROUP_OHLC,
    GRADUATION_THRESHOLD_MATCHED_BARS,
    RESOLUTION_AGREEMENT,
    RESOLUTION_HISTORICAL_MAD_AGREEMENT,
    ROLE_CANDIDATE,
    ROLE_WHISTLEBLOWER,
    DisagreementStats,
    FieldMadBand,
    FieldTolerance,
    ProviderBar,
    batch_stats,
    fields_for_group,
    reevaluate_unadjudicated,
    relative_diffs_for_stats_update,
    resolve_automatic,
    resolve_finalize,
    stddev_from_stats,
    welford_update,
)
from reconcile.outlier_detection import BACKGROUND_HALF_WINDOW_MINUTES, OutlierBar, OutlierThresholds, is_bar_rejected

_EASTERN = ZoneInfo("America/New_York")
_MARKET_OPEN = time_type(9, 30)
_MARKET_CLOSE = time_type(16, 0)

CATEGORY_RECONCILE = "reconcile"


@dataclass
class CliArguments:
    finalize: bool = False
    reevaluate_unadjudicated: bool = False
    debug: bool = False


def parse_args(argv: list[str]) -> CliArguments:
    parser = argparse.ArgumentParser(
        prog="quant-reconcile",
        usage="quant-reconcile [--finalize | --reevaluate-unadjudicated] [--debug]",
        fromfile_prefix_chars="@",
        description=(
            "Reads staging_market_data_1min, resolves bars where every configured provider "
            "agrees (or can be automatically explained), and promotes them into "
            "fact_market_data_1min. Anything the automatic pass can't resolve is flagged in "
            "fact_pending_manual_resolution and skipped by future plain runs. --finalize "
            "force-resolves only what's flagged there, using "
            "settings.reconcile.preferredProvider's raw value -- it does not also evaluate "
            "not-yet-attempted bars. --reevaluate-unadjudicated retroactively re-checks existing "
            "resolution_path='unadjudicated' bars against a since-seeded historical MAD band "
            "(tasks/reevaluate_unadjudicated_bars.md); it is a one-off backlog pass, not part of "
            "either of the other two modes, and never changes a promoted bar's actual value."
        ),
    )
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--finalize",
        action="store_true",
        default=False,
        help="force-resolve only the pending-manual-resolution queue using settings.reconcile.preferredProvider",
    )
    mode_group.add_argument(
        "--reevaluate-unadjudicated",
        action="store_true",
        default=False,
        help="retroactively re-check existing 'unadjudicated' bars against a since-seeded historical MAD band",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        default=False,
        help="override settings.json's debug flag",
    )

    args = parser.parse_args(argv)
    return CliArguments(finalize=args.finalize, reevaluate_unadjudicated=args.reevaluate_unadjudicated, debug=args.debug)


def _default_database_factory(postgres_settings: PostgresSettings) -> PostgresDatabase:
    transport = resolve_transport(
        host=postgres_settings.host,
        port=postgres_settings.port,
        ssh_user=postgres_settings.ssh_user,
        ssh_key_path=postgres_settings.ssh_key_path,
    )
    return PostgresDatabase(
        transport=transport,
        user=postgres_settings.user,
        password=postgres_settings.password,
        dbname=postgres_settings.dbname,
    )


def _to_provider_bar(row: StagingRow, provider: ProviderRow) -> ProviderBar:
    return ProviderBar(
        provider_id=row.provider_id,
        provider_name=provider.name,
        role=provider.role,
        open=row.open,
        high=row.high,
        low=row.low,
        close=row.close,
        volume=row.volume,
        data_quality=row.data_quality,
    )


def _find_by_provider_id(bars: list[ProviderBar], provider_id: int) -> ProviderBar | None:
    for bar in bars:
        if bar.provider_id == provider_id:
            return bar
    return None


def _find_whistleblower_bar(bars: list[ProviderBar]) -> ProviderBar | None:
    for bar in bars:
        if bar.role == ROLE_WHISTLEBLOWER:
            return bar
    return None


def _is_matched_bar(rows: list[StagingRow], expected_provider_count: int) -> bool:
    """A bar where every currently-configured provider reported real, non-incomplete data --
    same definition already used for Tier 1/2 eligibility. Used only to gate per-ticker
    graduation (croicu/quant-data#28); an ungraduated ticker's matched bars still wait for the
    graduation batch before any Tier 1-4 attempt, so this is deliberately not the same thing as
    "eligible for completeness". Requires an explicit provider-count check (not just "nothing
    present is incomplete") since croicu/quant-data#31 relaxed fetch eligibility to admit bars
    where the whistleblower never reported at all -- rows for those bars only contains the
    candidate(s), which must not count as "matched" (no real whistleblower value exists to
    measure disagreement against)."""
    if len(rows) != expected_provider_count:
        return False
    for row in rows:
        if row.data_quality != DATA_QUALITY_ACCEPTED:
            return False
    return True


def _synthetic_absent_whistleblower_bar(provider: ProviderRow) -> ProviderBar:
    """Stands in for a whistleblower that never wrote a staging row at all for this bar, once
    ingestion_coverage confirms its date range was actually ingested (croicu/quant-data#31).
    _resolve_completeness already treats an incomplete-flagged bar as "reported but unusable," so
    this placeholder reuses that exact path with zero changes to algorithm.py -- a confirmed-absent
    whistleblower behaves identically to one that reported and was flagged incomplete, including
    the resolution_path ('completeness', not a new label). The dummy field values are never
    actually compared against: Tier 2/3 measure tolerance using the *candidate's* own reference
    value, never the whistleblower's, and both tiers fail closed (correctly) if they're ever
    reached with these placeholder zeros instead of Tier 1 catching it first."""
    return ProviderBar(
        provider_id=provider.provider_id,
        provider_name=provider.name,
        role=ROLE_WHISTLEBLOWER,
        open=0.0,
        high=0.0,
        low=0.0,
        close=0.0,
        volume=0,
        data_quality=DATA_QUALITY_INCOMPLETE,
    )


def _missing_graduation_keys(
    ticker_id: int,
    candidate_provider_ids: list[int],
    ohlc_field_ids: list[int],
    graduated_keys: set[tuple[int, int, int]],
) -> set[tuple[int, int, int]]:
    """Every (candidate_provider_id, ticker_id, field_id) combination not yet present in
    graduated_keys -- the graduation key is deliberately identical to stats_by_key's own key shape
    (provider_id, ticker_id, field_id), not projected down to ticker_id alone. A per-ticker-only
    gate would let one candidate's existing graduation on a ticker silently lock out a second,
    later-added candidate from ever graduating on that same ticker (croicu/quant-data#44)."""
    missing: set[tuple[int, int, int]] = set()
    for candidate_provider_id in candidate_provider_ids:
        for field_id in ohlc_field_ids:
            key = (candidate_provider_id, ticker_id, field_id)
            if key not in graduated_keys:
                missing.add(key)
    return missing


def _fetch_mad_bands_by_ticker(database: PostgresDatabase, field_ids_by_name: dict[str, int]) -> dict[int, dict[str, FieldMadBand]]:
    """tasks/retroactive_revision.md: pooled, fixed per-(ticker, field) historical MAD band, keyed
    by field *name* (not field_id) to match reconcile.algorithm.FieldMadBand's own dict shape -- a
    ticker missing from the result, or missing any one of its fields, has no band at all. Shared by
    both the live no-whistleblower tier (_run_automatic_pass) and tasks/reevaluate_unadjudicated_
    bars.md's retroactive re-check (_run_reevaluate_unadjudicated_pass), so both read the exact
    same seeded bands."""
    field_names_by_id: dict[int, str] = {}
    for field_name, field_id in field_ids_by_name.items():
        field_names_by_id[field_id] = field_name

    mad_bands_by_ticker: dict[int, dict[str, FieldMadBand]] = {}
    for mad_band_row in database.fetch_candidate_pair_mad_bands():
        field_name = field_names_by_id.get(mad_band_row.field_id)
        if field_name is None:
            continue
        if mad_band_row.ticker_id not in mad_bands_by_ticker:
            mad_bands_by_ticker[mad_band_row.ticker_id] = {}
        mad_bands_by_ticker[mad_band_row.ticker_id][field_name] = FieldMadBand(conditional_mad_scaled=mad_band_row.conditional_mad_scaled, k=mad_band_row.k)
    return mad_bands_by_ticker


def _is_date_covered(coverage_ranges: dict[tuple[int, int], list[tuple[int, int]]], ticker_id: int, provider_id: int, date_id: int) -> bool:
    ranges = coverage_ranges.get((ticker_id, provider_id))
    if ranges is None:
        return False
    for start_date_id, end_date_id in ranges:
        if start_date_id <= date_id <= end_date_id:
            return True
    return False


def _neighbor_window(
    rows_by_provider_ticker_timestamp: dict[tuple[int, int, object], StagingRow],
    providers_by_id: dict[int, ProviderRow],
    provider_id: int,
    ticker_id: int,
    timestamp,
) -> list[ProviderBar | None]:
    window: list[ProviderBar | None] = []
    for offset in (-1, 0, 1):
        neighbor_timestamp = timestamp + timedelta(minutes=offset)
        row = rows_by_provider_ticker_timestamp.get((ticker_id, provider_id, neighbor_timestamp))
        if row is None:
            window.append(None)
        else:
            window.append(_to_provider_bar(row, providers_by_id[provider_id]))
    return window


def _bar_still_needed_as_neighbor(
    ticker_id: int,
    timestamp,
    bar_key_by_ticker_timestamp: dict[tuple[int, object], tuple[int, int, int]],
    resolved_by_bar: dict[tuple[int, int, int], dict[int, int]],
    all_field_group_ids: set[int],
) -> bool:
    """True if the bar at t-1 or t+1 (same ticker) still has staging rows and isn't fully
    resolved -- it may still need this bar's raw data as a Tier-3 windowed-average neighbor on a
    future run, so purging this bar now would recreate the "missing neighbor" gap
    (tasks/quant_reconcile.md) for it. A neighbor that's absent (never existed, already purged, or
    -- during a --finalize pass -- simply not fetched because it isn't pending) or already fully
    resolved will never call the windowed check again, so it doesn't block."""
    for offset in (-1, 1):
        neighbor_timestamp = timestamp + timedelta(minutes=offset)
        neighbor_bar_key = bar_key_by_ticker_timestamp.get((ticker_id, neighbor_timestamp))
        if neighbor_bar_key is None:
            continue
        neighbor_resolutions = resolved_by_bar.get(neighbor_bar_key, {})
        if set(neighbor_resolutions.keys()) != all_field_group_ids:
            return True
    return False


def _find_quote_group_row(rows: list[StagingRow]) -> StagingRow | None:
    """The first staging row reporting any quote-group field (croicu/quant-data#61: avg_bid/
    avg_ask/midpoint_*) -- not gated by which provider won this bar's OHLC vote, unlike the trade
    group (wap/trade_count), which rides along with the ohlc winner's own row directly. Only IBKR
    reports these today, so "first" really means "the only provider that ever could" -- written
    generically so a second quote source later needs its own real Tier 1-4 resolution upstream,
    not a change here."""
    for row in rows:
        if row.avg_bid is not None or row.midpoint_open is not None:
            return row
    return None


def _promote_and_lazily_purge(
    database: PostgresDatabase,
    bars: dict[tuple[int, int, int], list[StagingRow]],
    resolved_by_bar: dict[tuple[int, int, int], dict[int, int]],
    bar_key_by_ticker_timestamp: dict[tuple[int, object], tuple[int, int, int]],
    all_field_group_ids: set[int],
    ohlc_field_group_id: int,
) -> None:
    """Shared by both the automatic pass and --finalize: promote every fully-resolved bar (volume
    rides along with the ohlc winner's own row -- tasks/volume_reconciliation.md), then purge its
    staging rows unless an adjacent bar still needs them as a Tier-3 neighbor."""
    for bar_key, rows in bars.items():
        ticker_id, date_id, time_id = bar_key
        current_bar_resolutions = resolved_by_bar[bar_key]
        if set(current_bar_resolutions.keys()) != all_field_group_ids:
            continue

        ohlc_row: StagingRow | None = None
        for row in rows:
            if row.provider_id == current_bar_resolutions[ohlc_field_group_id]:
                ohlc_row = row
                break
        if ohlc_row is None:
            continue

        # Supplement fields (croicu/quant-data#61). Trade group (wap/trade_count) rides along with
        # the OHLC winner's own row, same precedent as volume. Quote group (avg_bid/avg_ask/
        # midpoint_*) is not winner-gated -- copied from whichever row reports it, independent of
        # who won OHLC.
        quote_row = _find_quote_group_row(rows)
        avg_bid = None if quote_row is None else quote_row.avg_bid
        avg_ask = None if quote_row is None else quote_row.avg_ask
        midpoint_open = None if quote_row is None else quote_row.midpoint_open
        midpoint_high = None if quote_row is None else quote_row.midpoint_high
        midpoint_low = None if quote_row is None else quote_row.midpoint_low
        midpoint_close = None if quote_row is None else quote_row.midpoint_close

        database.promote_bar_to_fact(
            ticker_id,
            date_id,
            time_id,
            ohlc_row.timestamp,
            ohlc_row.open,
            ohlc_row.high,
            ohlc_row.low,
            ohlc_row.close,
            ohlc_row.volume,
            ohlc_row.data_quality,
            wap=ohlc_row.wap,
            trade_count=ohlc_row.trade_count,
            avg_bid=avg_bid,
            avg_ask=avg_ask,
            midpoint_open=midpoint_open,
            midpoint_high=midpoint_high,
            midpoint_low=midpoint_low,
            midpoint_close=midpoint_close,
        )

        if _bar_still_needed_as_neighbor(ticker_id, ohlc_row.timestamp, bar_key_by_ticker_timestamp, resolved_by_bar, all_field_group_ids):
            continue
        database.purge_staging_bar(ticker_id, date_id, time_id)


def _session_segment(timestamp_utc: datetime) -> int:
    """0 = premarket (before 9:30 ET), 1 = regular hours (9:30-16:00 ET inclusive), 2 =
    after-hours (after 16:00 ET). staging_market_data_1min.timestamp is stored naive but is UTC
    (docs/SCHEMA.md) -- must be tagged UTC before converting, or astimezone() would wrongly
    assume the local system timezone."""
    eastern_time = timestamp_utc.replace(tzinfo=timezone.utc).astimezone(_EASTERN).time()
    if eastern_time < _MARKET_OPEN:
        return 0
    if eastern_time <= _MARKET_CLOSE:
        return 1
    return 2


def _build_outlier_window(rows_by_time_id: dict[int, StagingRow], center_time_id: int, target_segment: int) -> dict[int, OutlierBar]:
    """A +/-BACKGROUND_HALF_WINDOW_MINUTES window of same-session-segment bars centered on
    center_time_id. Shared by both the normal per-bar (middle-of-session) case and the frozen
    tail-window case below, which just calls this with an anchor time_id instead of the bar
    actually being evaluated."""
    window: dict[int, OutlierBar] = {}
    for offset in range(-BACKGROUND_HALF_WINDOW_MINUTES, BACKGROUND_HALF_WINDOW_MINUTES + 1):
        neighbor = rows_by_time_id.get(center_time_id + offset)
        if neighbor is None:
            continue
        if _session_segment(neighbor.timestamp) != target_segment:
            continue
        window[neighbor.time_id] = OutlierBar(time_id=neighbor.time_id, open=neighbor.open, high=neighbor.high, low=neighbor.low, close=neighbor.close)
    return window


def _run_outlier_detection_pass(database: PostgresDatabase) -> int:
    """Runs the intra-provider outlier check (reconcile/outlier_detection.py) over every
    'accepted' whistleblower staging row, rejecting any bar it flags. Called before the Tier 1-4
    pass so a newly-rejected bar gets the chance to auto-promote its candidate in the same
    invocation (Tier 1 treats 'rejected' identically to 'incomplete'). Sweeps the entire staging
    table every run, not just new data -- the only mechanism that can ever touch the pre-existing
    stuck backlog, since ingest-time placement was ruled out for exactly that reason. Returns the
    number of bars newly rejected this run.

    Bars within the last/first BACKGROUND_HALF_WINDOW_MINUTES of a session segment (premarket,
    regular, afterhours) don't get their own freshly-recentered window -- that would either shrink
    as the segment edge approaches (a smaller, less trustworthy background sample right where a
    real spike is most plausible) or need to reach past the segment boundary (never allowed, since
    that would compare against a different session's regime -- a real step, not noise). Instead,
    each segment's tail reuses one shared window anchored at the last position where a full
    +/-BACKGROUND_HALF_WINDOW_MINUTES window still fits entirely inside that segment. The true
    first/last bar of a segment still only has one usable immediate neighbor even with the frozen
    window (its other side belongs to a different segment by construction) -- is_bar_rejected's
    one-sided k_boundary_* path handles that case."""
    thresholds_by_key: dict[tuple[int, int], OutlierThresholds] = {}
    for threshold_row in database.fetch_data_quality_thresholds():
        thresholds_by_key[(threshold_row.provider_id, threshold_row.ticker_id)] = OutlierThresholds(
            k_reversal_oc=threshold_row.k_reversal_oc,
            k_trend_oc=threshold_row.k_trend_oc,
            k_reversal_hl=threshold_row.k_reversal_hl,
            k_trend_hl=threshold_row.k_trend_hl,
        )

    rows_by_provider_ticker_date: dict[tuple[int, int, int], list[StagingRow]] = {}
    for row in database.fetch_whistleblower_accepted_staging_rows():
        key = (row.provider_id, row.ticker_id, row.date_id)
        if key not in rows_by_provider_ticker_date:
            rows_by_provider_ticker_date[key] = []
        rows_by_provider_ticker_date[key].append(row)

    rejected_keys: list[tuple[int, int, int, int]] = []
    for (provider_id, ticker_id, date_id), day_rows in rows_by_provider_ticker_date.items():
        rows_by_time_id: dict[int, StagingRow] = {}
        for row in day_rows:
            rows_by_time_id[row.time_id] = row

        thresholds = thresholds_by_key.get((provider_id, ticker_id), OutlierThresholds())

        rows_by_segment: dict[int, list[StagingRow]] = {}
        for row in day_rows:
            segment = _session_segment(row.timestamp)
            if segment not in rows_by_segment:
                rows_by_segment[segment] = []
            rows_by_segment[segment].append(row)

        for segment, segment_rows in rows_by_segment.items():
            segment_time_ids = sorted(row.time_id for row in segment_rows)
            segment_start = segment_time_ids[0]
            segment_end = segment_time_ids[-1]

            start_anchor = min(segment_end, segment_start + BACKGROUND_HALF_WINDOW_MINUTES)
            end_anchor = max(segment_start, segment_end - BACKGROUND_HALF_WINDOW_MINUTES)
            start_tail_window = _build_outlier_window(rows_by_time_id, start_anchor, segment)
            end_tail_window = _build_outlier_window(rows_by_time_id, end_anchor, segment)

            for row in segment_rows:
                if row.time_id <= start_anchor:
                    window = start_tail_window
                elif row.time_id >= end_anchor:
                    window = end_tail_window
                else:
                    window = _build_outlier_window(rows_by_time_id, row.time_id, segment)

                if is_bar_rejected(window, target_time_id=row.time_id, thresholds=thresholds):
                    rejected_keys.append((provider_id, ticker_id, date_id, row.time_id))

    database.mark_staging_bars_rejected(rejected_keys)
    return len(rejected_keys)


def _run_automatic_pass(database: PostgresDatabase, settings: Settings) -> tuple[int, int]:
    """Tiers 1-3 only, run per bar per not-yet-resolved field group; fetch_staging_rows_for_reconciliation
    already excludes anything with a fact_pending_manual_resolution row (tasks/quant_reconcile.md's
    "Updated (2026-08-03)" section), so this never re-touches a bar a prior run already gave up on.
    Anything still unresolved once the fixed-point loop below converges gets marked pending -- a
    new write path, since previously nothing was recorded for a stuck bar at all."""

    rejected_count = _run_outlier_detection_pass(database)
    if rejected_count:
        Logger.info(f"quant-reconcile: outlier check rejected {rejected_count} whistleblower bar(s).", category=CATEGORY_RECONCILE)

    providers = database.fetch_dim_providers()
    providers_by_id: dict[int, ProviderRow] = {}
    providers_by_name: dict[str, ProviderRow] = {}
    candidate_provider_names: list[str] = []
    candidate_provider_ids: list[int] = []
    whistleblower_provider_id: int | None = None
    for provider in providers:
        providers_by_id[provider.provider_id] = provider
        providers_by_name[provider.name] = provider
        if provider.role == ROLE_CANDIDATE:
            candidate_provider_names.append(provider.name)
            candidate_provider_ids.append(provider.provider_id)
        elif provider.role == ROLE_WHISTLEBLOWER:
            whistleblower_provider_id = provider.provider_id

    coverage_ranges: dict[tuple[int, int], list[tuple[int, int]]] = {}
    for coverage_row in database.fetch_ingestion_coverage():
        coverage_key = (coverage_row.ticker_id, coverage_row.provider_id)
        if coverage_key not in coverage_ranges:
            coverage_ranges[coverage_key] = []
        coverage_ranges[coverage_key].append((coverage_row.start_date_id, coverage_row.end_date_id))

    field_groups = database.fetch_dim_field_groups()
    field_group_ids_by_name: dict[str, int] = {}
    for field_group in field_groups:
        field_group_ids_by_name[field_group.name] = field_group.field_group_id
    all_field_group_ids: set[int] = set(field_group_ids_by_name.values())

    fields = database.fetch_dim_fields()
    field_ids_by_name: dict[str, int] = {}
    for field in fields:
        field_ids_by_name[field.name] = field.field_id

    preferred_provider = providers_by_name.get(settings.reconcile.preferred_provider)
    preferred_provider_id = preferred_provider.provider_id if preferred_provider is not None else None

    stats_by_key: dict[tuple[int, int, int], DisagreementStats] = {}
    for row in database.fetch_provider_pair_disagreement():
        stats_by_key[(row.provider_id, row.ticker_id, row.field_id)] = DisagreementStats(
            sample_count=row.sample_count,
            running_mean=row.running_mean,
            running_m2=row.running_m2,
        )

    floors_by_key: dict[tuple[int, int, int], MaterialityFloorRow] = {}
    for floor_row in database.fetch_materiality_floors():
        floors_by_key[(floor_row.provider_id, floor_row.ticker_id, floor_row.field_id)] = floor_row

    mad_bands_by_ticker = _fetch_mad_bands_by_ticker(database, field_ids_by_name)

    staging_rows = database.fetch_staging_rows_for_reconciliation(settings.providers, candidate_provider_names)

    bars: dict[tuple[int, int, int], list[StagingRow]] = {}
    rows_by_provider_ticker_timestamp: dict[tuple[int, int, object], StagingRow] = {}
    bar_key_by_ticker_timestamp: dict[tuple[int, object], tuple[int, int, int]] = {}
    for row in staging_rows:
        bar_key = (row.ticker_id, row.date_id, row.time_id)
        if bar_key not in bars:
            bars[bar_key] = []
        bars[bar_key].append(row)
        rows_by_provider_ticker_timestamp[(row.ticker_id, row.provider_id, row.timestamp)] = row
        bar_key_by_ticker_timestamp[(row.ticker_id, row.timestamp)] = bar_key

    # croicu/quant-data#31 (whistleblower) + croicu/quant-data#49 (generalized to every candidate
    # too): fetch_staging_rows_for_reconciliation now admits bar_keys missing the whistleblower
    # and/or one or more candidates -- only requires at least one candidate present at all. A
    # bar_key is only genuinely ready for evaluation once every *individually missing* required
    # provider (the whistleblower, or any candidate) is either present, or ingestion_coverage
    # confirms its absence is a real "nothing here," not "not ingested yet" -- a provider's
    # ingested data for a covered (ticker, date) is immutable once acquired, so a confirmed-absent
    # minute is a fact about that provider, not an ambiguous pending state. If even one required
    # provider is missing and unconfirmed, the whole bar_key is left alone exactly like a bar
    # missing any provider always was: not evaluated, not marked pending, picked up on a future run
    # once coverage resolves the ambiguity. algorithm.py needs no changes for this -- every tier
    # already iterates whatever candidates happen to be present in `bars`, so a bar_key that makes
    # it through this filter with (say) only `ibkr` and not `massive` present is automatically
    # resolved using `ibkr` alone, subject to the same whistleblower validation as any other bar.
    # Filtering here (not deferred) keeps every downstream step -- graduation counting, the
    # fixed-point loop, pending-marking -- automatically correct with no special-casing.
    required_provider_ids: list[int] = list(candidate_provider_ids)
    if whistleblower_provider_id is not None:
        required_provider_ids.append(whistleblower_provider_id)

    unready_bar_keys: list[tuple[int, int, int]] = []
    for bar_key, rows in bars.items():
        present_provider_ids: set[int] = set()
        for row in rows:
            present_provider_ids.add(row.provider_id)

        unready_ticker_id, unready_date_id, _unready_time_id = bar_key
        for required_provider_id in required_provider_ids:
            if required_provider_id in present_provider_ids:
                continue
            if not _is_date_covered(coverage_ranges, unready_ticker_id, required_provider_id, unready_date_id):
                unready_bar_keys.append(bar_key)
                break
    for bar_key in unready_bar_keys:
        del bars[bar_key]

    # Per-ticker eligibility gate (croicu/quant-data#28): a ticker with no provider_pair_disagreement
    # rows at all yet is wholly "ungraduated" -- its bars sit in staging completely unevaluated (no
    # Tier 1-4 attempt, no partial stats update) until at least one candidate accumulates
    # GRADUATION_THRESHOLD_MATCHED_BARS matched bars and graduates below, at which point the
    # ticker's entire currently-fetched backlog (matched and unmatched together, so Tier 1
    # completeness -- which only ever fires on unmatched bars -- isn't stranded) is evaluated
    # through the standard Tier 1-4 stack. Coarse (per-ticker) on purpose: once a ticker has ever
    # graduated for any candidate, it stays eligible for Tier 1-4 permanently -- a second,
    # later-added candidate's own graduation (below) must not re-gate it.
    graduated_ticker_ids: set[int] = set()
    for stats_provider_id, stats_ticker_id, stats_field_id in stats_by_key:
        graduated_ticker_ids.add(stats_ticker_id)

    # Per-(candidate, ticker, field) graduation key (croicu/quant-data#44) -- deliberately the same
    # shape as stats_by_key's own key, not projected down to ticker_id alone. The projection used
    # to be exactly that: "has this ticker ever graduated for *any* candidate" also gated whether
    # the batch-computation block below ran at all, so a second candidate added to an
    # already-graduated ticker (e.g. massive joining ibkr on SPY) could never reach its own
    # graduation batch -- tolerances.get(candidate.provider_id) would stay None forever, locking it
    # out of Tier 2/3 permanently, not just until it "caught up." graduated_keys tracks the finer
    # granularity that actually matches what tolerances are looked up by.
    graduated_keys: set[tuple[int, int, int]] = set(stats_by_key.keys())

    bar_keys_by_ticker: dict[int, list[tuple[int, int, int]]] = {}
    for bar_key in bars:
        ticker_id = bar_key[0]
        if ticker_id not in bar_keys_by_ticker:
            bar_keys_by_ticker[ticker_id] = []
        bar_keys_by_ticker[ticker_id].append(bar_key)

    # Only needed inside the loop below -- guarded so a run with nothing at all in staging (no
    # bars, nothing to graduate) doesn't require dim_field to be resolvable. candidate_provider_ids
    # itself was already computed above (used by the eligibility filter too), no dim_field
    # dependency, so it's always safe to reuse as-is.
    ohlc_field_ids: list[int] = []
    if bar_keys_by_ticker:
        for field_name in fields_for_group(FIELD_GROUP_OHLC):
            ohlc_field_ids.append(field_ids_by_name[field_name])

    for ticker_id, ticker_bar_keys in bar_keys_by_ticker.items():
        missing_keys = _missing_graduation_keys(ticker_id, candidate_provider_ids, ohlc_field_ids, graduated_keys)
        if not missing_keys:
            continue

        matched_bar_keys: list[tuple[int, int, int]] = []
        for bar_key in ticker_bar_keys:
            if _is_matched_bar(bars[bar_key], len(settings.providers)):
                matched_bar_keys.append(bar_key)

        if len(matched_bar_keys) < GRADUATION_THRESHOLD_MATCHED_BARS:
            continue

        # Only accumulate diffs for the specific (candidate, field) combinations this ticker is
        # still missing -- an already-graduated candidate (e.g. ibkr, once massive is the one
        # missing keys) must not have its mature, Welford-accumulated stats silently recomputed
        # and overwritten just because it's also present in these same matched bars.
        diffs_by_candidate_field: dict[tuple[int, str], list[float]] = {}
        for bar_key in matched_bar_keys:
            matched_provider_bars: list[ProviderBar] = []
            for row in bars[bar_key]:
                matched_provider_bars.append(_to_provider_bar(row, providers_by_id[row.provider_id]))
            matched_whistleblower_bar = _find_whistleblower_bar(matched_provider_bars)
            if matched_whistleblower_bar is None:
                continue
            for matched_candidate_bar in matched_provider_bars:
                if matched_candidate_bar.role != ROLE_CANDIDATE:
                    continue
                diffs = relative_diffs_for_stats_update(matched_candidate_bar, matched_whistleblower_bar, FIELD_GROUP_OHLC)
                for field_name, diff in zip(fields_for_group(FIELD_GROUP_OHLC), diffs):
                    field_id = field_ids_by_name[field_name]
                    if (matched_candidate_bar.provider_id, ticker_id, field_id) not in missing_keys:
                        continue
                    diff_key = (matched_candidate_bar.provider_id, field_name)
                    if diff_key not in diffs_by_candidate_field:
                        diffs_by_candidate_field[diff_key] = []
                    diffs_by_candidate_field[diff_key].append(diff)

        if not diffs_by_candidate_field:
            # Every missing candidate simply never reported at any of this ticker's matched bars
            # this run (e.g. massive not ingested for this ticker yet) -- nothing to graduate.
            continue

        graduation_rows: list[tuple[int, int, int, int, float, float, float]] = []
        for (candidate_provider_id, field_name), diffs in diffs_by_candidate_field.items():
            graduation_stats = batch_stats(diffs)
            field_id = field_ids_by_name[field_name]
            stats_by_key[(candidate_provider_id, ticker_id, field_id)] = graduation_stats
            graduated_keys.add((candidate_provider_id, ticker_id, field_id))
            graduation_rows.append(
                (
                    candidate_provider_id,
                    ticker_id,
                    field_id,
                    graduation_stats.sample_count,
                    graduation_stats.running_mean,
                    graduation_stats.running_m2,
                    stddev_from_stats(graduation_stats),
                )
            )
        database.save_provider_pair_disagreement_batch(graduation_rows)

        graduated_ticker_ids.add(ticker_id)
        Logger.info(
            f"quant-reconcile: ticker {ticker_id} graduated {len(graduation_rows)} (provider, field) combination(s) at {len(matched_bar_keys)} matched bars.",
            category=CATEGORY_RECONCILE,
        )

    eligible_bars: dict[tuple[int, int, int], list[StagingRow]] = {}
    for bar_key, rows in bars.items():
        if bar_key[0] in graduated_ticker_ids:
            eligible_bars[bar_key] = rows

    resolved_by_bar: dict[tuple[int, int, int], dict[int, int]] = {}
    for (ticker_id, date_id, time_id, field_group_id), winning_provider_id in database.fetch_resolved_field_groups().items():
        bar_key = (ticker_id, date_id, time_id)
        if bar_key not in resolved_by_bar:
            resolved_by_bar[bar_key] = {}
        resolved_by_bar[bar_key][field_group_id] = winning_provider_id
    for bar_key in eligible_bars:
        if bar_key not in resolved_by_bar:
            resolved_by_bar[bar_key] = {}

    provider_bars_by_bar_key: dict[tuple[int, int, int], list[ProviderBar]] = {}
    windows_by_bar_key: dict[tuple[int, int, int], dict[int, list[ProviderBar | None]]] = {}
    for bar_key, rows in eligible_bars.items():
        ticker_id, _date_id, _time_id = bar_key
        provider_bars: list[ProviderBar] = []
        whistleblower_present = False
        for row in rows:
            provider_bar = _to_provider_bar(row, providers_by_id[row.provider_id])
            provider_bars.append(provider_bar)
            if provider_bar.role == ROLE_WHISTLEBLOWER:
                whistleblower_present = True

        # croicu/quant-data#31: the whistleblower never wrote a row for this bar at all. The
        # earlier filtering step already guarantees this only happens when ingestion_coverage
        # confirms that date was actually scanned (an uncovered candidate-only bar_key never makes
        # it into `bars` in the first place) -- synthesize a placeholder so Tier 1 completeness
        # treats "confirmed absent" the same as "reported but incomplete."
        if not whistleblower_present and whistleblower_provider_id is not None:
            provider_bars.append(_synthetic_absent_whistleblower_bar(providers_by_id[whistleblower_provider_id]))

        provider_bars_by_bar_key[bar_key] = provider_bars

        windows: dict[int, list[ProviderBar | None]] = {}
        for row in rows:
            windows[row.provider_id] = _neighbor_window(rows_by_provider_ticker_timestamp, providers_by_id, row.provider_id, ticker_id, row.timestamp)
        windows_by_bar_key[bar_key] = windows

    total_resolved = 0

    # Fixed-point loop: a bar visited early in a pass can fail Tier 2 using disagreement stats
    # that a later bar's own agreement resolution improves within the very same pass, so a single
    # pass alone doesn't necessarily reach the real convergence floor -- repeat full passes until
    # one resolves nothing new, rather than requiring a person to re-invoke quant-reconcile several
    # times by hand (tasks/quant_reconcile.md's seeding-lag finding: 85 -> 68 -> 63 -> 61 -> 0
    # across four manual re-runs). Provably terminates: each pass either resolves at least one more
    # group (bounded by the finite total group count) or resolves zero, at which point stats can no
    # longer change and further passes would be identical.
    while True:
        pass_resolved = 0
        for bar_key, rows in eligible_bars.items():
            ticker_id, date_id, time_id = bar_key
            current_bar_resolutions = resolved_by_bar[bar_key]
            provider_bars = provider_bars_by_bar_key[bar_key]

            for field_group_name, field_group_id in field_group_ids_by_name.items():
                if field_group_id in current_bar_resolutions:
                    continue

                tolerances: dict[int, dict[str, FieldTolerance]] = {}
                for row in rows:
                    provider = providers_by_id[row.provider_id]
                    if provider.role != ROLE_CANDIDATE:
                        continue
                    field_tolerances: dict[str, FieldTolerance] = {}
                    for field_name in fields_for_group(field_group_name):
                        field_id = field_ids_by_name[field_name]
                        stats = stats_by_key.get((provider.provider_id, ticker_id, field_id))
                        if stats is not None:
                            floor_row = floors_by_key.get((provider.provider_id, ticker_id, field_id))
                            if floor_row is not None:
                                field_tolerances[field_name] = FieldTolerance(
                                    stddev=stddev_from_stats(stats), floor_value=floor_row.floor_value, floor_type=floor_row.floor_type
                                )
                            else:
                                field_tolerances[field_name] = FieldTolerance(stddev=stddev_from_stats(stats))
                    if field_tolerances:
                        tolerances[provider.provider_id] = field_tolerances

                resolution = resolve_automatic(
                    provider_bars,
                    field_group_name,
                    windows_by_bar_key[bar_key],
                    tolerances,
                    settings.reconcile.k,
                    preferred_provider_id,
                    mad_bands_by_ticker.get(ticker_id),
                )

                if resolution is None:
                    continue

                participant_results: list[tuple[int, bool]] = []
                for provider_bar in provider_bars:
                    participant_results.append((provider_bar.provider_id, provider_bar.provider_id == resolution.winning_provider_id))

                database.record_reconciliation(
                    ticker_id,
                    date_id,
                    time_id,
                    field_group_id,
                    resolution.winning_provider_id,
                    resolution.resolution_path,
                    participant_results,
                )
                current_bar_resolutions[field_group_id] = resolution.winning_provider_id
                pass_resolved += 1

                if resolution.resolution_path == RESOLUTION_AGREEMENT:
                    winner_bar = _find_by_provider_id(provider_bars, resolution.winning_provider_id)
                    whistleblower_bar = _find_whistleblower_bar(provider_bars)
                    if winner_bar is not None and whistleblower_bar is not None:
                        diffs = relative_diffs_for_stats_update(winner_bar, whistleblower_bar, field_group_name)
                        field_names = fields_for_group(field_group_name)
                        disagreement_rows: list[tuple[int, int, int, int, float, float, float]] = []
                        for field_name, diff in zip(field_names, diffs):
                            field_id = field_ids_by_name[field_name]
                            key = (resolution.winning_provider_id, ticker_id, field_id)
                            stats = stats_by_key.get(key)
                            if stats is None:
                                stats = DisagreementStats(sample_count=0, running_mean=0.0, running_m2=0.0)
                            stats = welford_update(stats, diff)
                            stats_by_key[key] = stats
                            disagreement_rows.append(
                                (
                                    resolution.winning_provider_id,
                                    ticker_id,
                                    field_id,
                                    stats.sample_count,
                                    stats.running_mean,
                                    stats.running_m2,
                                    stddev_from_stats(stats),
                                )
                            )
                        database.save_provider_pair_disagreement_batch(disagreement_rows)

        total_resolved += pass_resolved
        if pass_resolved == 0:
            break

    newly_pending = 0
    for bar_key, rows in eligible_bars.items():
        ticker_id, date_id, time_id = bar_key
        current_bar_resolutions = resolved_by_bar[bar_key]
        for field_group_id in all_field_group_ids:
            if field_group_id in current_bar_resolutions:
                continue
            database.mark_pending_manual_resolution(ticker_id, date_id, time_id, field_group_id, rows[0].timestamp)
            newly_pending += 1

    _promote_and_lazily_purge(database, eligible_bars, resolved_by_bar, bar_key_by_ticker_timestamp, all_field_group_ids, field_group_ids_by_name["ohlc"])

    return total_resolved, newly_pending


def _run_finalize_pass(database: PostgresDatabase, settings: Settings) -> tuple[int, int]:
    """Force-resolves only what's currently in fact_pending_manual_resolution, using
    settings.reconcile.preferredProvider's raw value -- no Tier 1-3 re-attempt, those tiers already
    failed for these (bar, group)s during a prior automatic pass. Does not evaluate not-yet-attempted
    bars at all; those need a plain (non-finalize) run first."""

    providers = database.fetch_dim_providers()
    providers_by_id: dict[int, ProviderRow] = {}
    providers_by_name: dict[str, ProviderRow] = {}
    for provider in providers:
        providers_by_id[provider.provider_id] = provider
        providers_by_name[provider.name] = provider

    field_groups = database.fetch_dim_field_groups()
    field_group_ids_by_name: dict[str, int] = {}
    for field_group in field_groups:
        field_group_ids_by_name[field_group.name] = field_group.field_group_id
    all_field_group_ids: set[int] = set(field_group_ids_by_name.values())

    preferred_provider = providers_by_name.get(settings.reconcile.preferred_provider)
    preferred_provider_id = preferred_provider.provider_id if preferred_provider is not None else None

    pending_keys = database.fetch_pending_manual_resolution_keys()
    staging_rows = database.fetch_pending_manual_resolution_staging_rows()

    bars: dict[tuple[int, int, int], list[StagingRow]] = {}
    bar_key_by_ticker_timestamp: dict[tuple[int, object], tuple[int, int, int]] = {}
    for row in staging_rows:
        bar_key = (row.ticker_id, row.date_id, row.time_id)
        if bar_key not in bars:
            bars[bar_key] = []
        bars[bar_key].append(row)
        bar_key_by_ticker_timestamp[(row.ticker_id, row.timestamp)] = bar_key

    pending_field_groups_by_bar: dict[tuple[int, int, int], set[int]] = {}
    for ticker_id, date_id, time_id, field_group_id in pending_keys:
        bar_key = (ticker_id, date_id, time_id)
        if bar_key not in pending_field_groups_by_bar:
            pending_field_groups_by_bar[bar_key] = set()
        pending_field_groups_by_bar[bar_key].add(field_group_id)

    resolved_by_bar: dict[tuple[int, int, int], dict[int, int]] = {}
    for bar_key in bars:
        resolved_by_bar[bar_key] = {}

    total_resolved = 0
    still_pending = 0

    if preferred_provider_id is None:
        for bar_key in bars:
            still_pending += len(pending_field_groups_by_bar.get(bar_key, set()))
        return total_resolved, still_pending

    for bar_key, rows in bars.items():
        ticker_id, date_id, time_id = bar_key
        provider_bars: list[ProviderBar] = []
        for row in rows:
            provider_bars.append(_to_provider_bar(row, providers_by_id[row.provider_id]))

        for field_group_id in pending_field_groups_by_bar.get(bar_key, set()):
            resolution = resolve_finalize(provider_bars, preferred_provider_id)
            if resolution is None:
                still_pending += 1
                continue

            participant_results: list[tuple[int, bool]] = []
            for provider_bar in provider_bars:
                participant_results.append((provider_bar.provider_id, provider_bar.provider_id == resolution.winning_provider_id))

            database.record_reconciliation(
                ticker_id,
                date_id,
                time_id,
                field_group_id,
                resolution.winning_provider_id,
                resolution.resolution_path,
                participant_results,
            )
            database.clear_pending_manual_resolution(ticker_id, date_id, time_id, field_group_id)
            resolved_by_bar[bar_key][field_group_id] = resolution.winning_provider_id
            total_resolved += 1

    _promote_and_lazily_purge(database, bars, resolved_by_bar, bar_key_by_ticker_timestamp, all_field_group_ids, field_group_ids_by_name["ohlc"])

    return total_resolved, still_pending


def _archived_row_to_provider_bar(row: ArchivedCandidateBarRow) -> ProviderBar:
    """Only the OHLC fields feed reconcile.algorithm.reevaluate_unadjudicated's comparison --
    role/provider_name/volume/data_quality are never read by it, so these are honest placeholders,
    not guesses standing in for real unknowns. data_quality=ACCEPTED is still the correct fact, not
    just a placeholder: an unadjudicated resolution could only ever have been reached with both
    candidates already ACCEPTED (Tier 1 completeness catches any single-invalid-candidate case
    first), so an archived row that fed one is known-ACCEPTED by construction."""
    return ProviderBar(
        provider_id=row.provider_id,
        provider_name=str(row.provider_id),
        role=ROLE_CANDIDATE,
        open=row.open,
        high=row.high,
        low=row.low,
        close=row.close,
        volume=0.0,
        data_quality=DATA_QUALITY_ACCEPTED,
    )


def _run_reevaluate_unadjudicated_pass(database: PostgresDatabase) -> tuple[int, int]:
    """tasks/reevaluate_unadjudicated_bars.md: retroactively re-checks every existing
    resolution_path='unadjudicated' fact_reconciliation row for a ticker with a since-seeded
    candidate_pair_mad_band, using each candidate's original value from market_data_archive (the
    live staging rows are already purged for all of them). Deliberately never touches
    fact_market_data_1min or winning_provider_id -- only resolution_path changes, either to
    'historical_mad_agreement' (confirmed agreement) or 'unadjudicated_disputed' (confirmed
    disagreement, flagged for a future manual review pass, not retracted -- see that task's
    converged design). A bar this can't re-evaluate (band not fully seeded for its ticker, or not
    exactly two archived candidates) is left untouched, same as if this pass never ran for it.

    Returns (agreed, disputed) -- deliberately not (resolved, needing_attention) despite the
    similar shape, since neither number represents new Tier 1-4 work the way run_reconciliation's
    other two passes do."""
    fields = database.fetch_dim_fields()
    field_ids_by_name: dict[str, int] = {}
    for field in fields:
        field_ids_by_name[field.name] = field.field_id

    field_groups = database.fetch_dim_field_groups()
    field_group_names_by_id: dict[int, str] = {}
    for field_group in field_groups:
        field_group_names_by_id[field_group.field_group_id] = field_group.name

    mad_bands_by_ticker = _fetch_mad_bands_by_ticker(database, field_ids_by_name)

    agreed = 0
    disputed = 0
    updates: list[tuple[int, int, int, int, str]] = []
    for ticker_id, mad_bands in mad_bands_by_ticker.items():
        archived_rows = database.fetch_archived_candidate_values_for_unadjudicated_bars(ticker_id)

        rows_by_bar: dict[tuple[int, int, int], list[ArchivedCandidateBarRow]] = {}
        for row in archived_rows:
            bar_key = (row.date_id, row.time_id, row.field_group_id)
            if bar_key not in rows_by_bar:
                rows_by_bar[bar_key] = []
            rows_by_bar[bar_key].append(row)

        for (date_id, time_id, field_group_id), bar_rows in rows_by_bar.items():
            field_group_name = field_group_names_by_id.get(field_group_id)
            if field_group_name is None:
                continue
            archived_candidates = [_archived_row_to_provider_bar(row) for row in bar_rows]
            new_resolution_path = reevaluate_unadjudicated(archived_candidates, field_group_name, mad_bands)
            if new_resolution_path is None:
                continue

            updates.append((ticker_id, date_id, time_id, field_group_id, new_resolution_path))
            if new_resolution_path == RESOLUTION_HISTORICAL_MAD_AGREEMENT:
                agreed += 1
            else:
                disputed += 1

    database.update_unadjudicated_resolution_paths_batch(updates)
    return agreed, disputed


def run_reconciliation(database: PostgresDatabase, settings: Settings, finalize: bool, reevaluate_unadjudicated_bars: bool = False) -> tuple[int, int]:
    """Dispatches to the automatic pass, the --finalize sweep, or the --reevaluate-unadjudicated
    retroactive re-check -- tasks/quant_reconcile.md's "Updated (2026-08-03)" pending-manual-
    resolution queue design plus tasks/reevaluate_unadjudicated_bars.md's later addition. Returns
    (groups_resolved, groups_needing_attention): for a plain run the second value is newly marked
    pending this run (not a cumulative backlog count, since already-pending bars aren't even
    fetched); for --finalize it's whatever resolve_finalize itself couldn't resolve (expected to
    be 0 in practice, since every pending bar already has every configured provider's data); for
    --reevaluate-unadjudicated it's (agreed, disputed) instead -- see
    _run_reevaluate_unadjudicated_pass's own docstring for why that shape still fits the same
    two-int return."""
    if reevaluate_unadjudicated_bars:
        return _run_reevaluate_unadjudicated_pass(database)
    if finalize:
        return _run_finalize_pass(database, settings)
    return _run_automatic_pass(database, settings)


def main(
    argv: list[str] | None = None,
    settings_path: Path | None = None,
    database_factory: Callable[[PostgresSettings], PostgresDatabase] | None = None,
) -> int:
    arguments = parse_args(sys.argv[1:] if argv is None else argv)

    try:
        settings = Settings.load() if settings_path is None else Settings.load(path=settings_path)
    except AppError as error:
        print(f"quant-reconcile: error: {error}", file=sys.stderr)
        return 1

    debug = settings.debug or arguments.debug

    Logger.set_logger(
        ConsoleLogSink(
            min_level=settings.logging,
            categories=settings.log_categories,
            excluded_categories=settings.excluded_categories,
        )
    )
    try:
        Logger.info("quant-reconcile: started.")

        if settings.postgres is None:
            raise AppError("settings.postgres is required to run quant-reconcile.")

        if not settings.providers:
            # No --providers CLI override here, unlike quant-ingest/quant-stage -- reconcile's
            # provider set is load-bearing for Tier 1-4 correctness (matched-bar counting,
            # candidate/whistleblower set), not just a fetch-source convenience, so it's
            # settings.json/settings.local.json only.
            raise AppError("No provider configured — configure settings.providers in settings.json or settings.local.json.")

        active_database_factory = database_factory if database_factory is not None else _default_database_factory
        database = active_database_factory(settings.postgres)
        try:
            resolved, needing_attention = run_reconciliation(database, settings, arguments.finalize, arguments.reevaluate_unadjudicated)
        finally:
            database.close()

        if arguments.reevaluate_unadjudicated:
            Logger.info(
                f"quant-reconcile --reevaluate-unadjudicated: completed ({resolved} bar(s) confirmed agreed, {needing_attention} bar(s) confirmed disputed).",
                category=CATEGORY_RECONCILE,
            )
        elif arguments.finalize:
            Logger.info(
                f"quant-reconcile --finalize: completed ({resolved} group(s) resolved, {needing_attention} group(s) still unresolved).",
                category=CATEGORY_RECONCILE,
            )
        else:
            Logger.info(
                f"quant-reconcile: completed ({resolved} group(s) resolved, {needing_attention} group(s) newly marked pending manual resolution).",
                category=CATEGORY_RECONCILE,
            )
        return 0
    except AppError as error:
        if debug:
            raise
        print(f"quant-reconcile: error: {error}", file=sys.stderr)
        return 1
    finally:
        Logger.set_logger(None)


if __name__ == "__main__":
    raise SystemExit(main())
