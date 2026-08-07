from __future__ import annotations

from datetime import datetime

from quant_data._internal.shared.postgres import (
    DataQualityThresholdRow,
    DisagreementStatsRow,
    FieldGroupRow,
    FieldRow,
    IngestionCoverageRow,
    ProviderRow,
    StagingRow,
)


class FakeReconcileDatabase:
    """In-memory stand-in for PostgresDatabase's reconcile-facing methods, replicating just
    enough real Postgres behavior (per-bar provider-count scoping, staging purge on promotion)
    to exercise run_reconciliation() end-to-end without mocking psycopg directly."""

    def __init__(
        self,
        providers: list[ProviderRow],
        field_groups: list[FieldGroupRow],
        staging_rows: list[StagingRow],
        fields: list[FieldRow] | None = None,
        disagreement_stats: list[DisagreementStatsRow] | None = None,
        ingestion_coverage: list[IngestionCoverageRow] | None = None,
        data_quality_thresholds: list[DataQualityThresholdRow] | None = None,
    ) -> None:
        self.providers = providers
        self.field_groups = field_groups
        self.fields = fields if fields is not None else []
        self.staging_rows = list(staging_rows)
        self.disagreement_stats: dict[tuple[int, int, int], DisagreementStatsRow] = {}
        if disagreement_stats is not None:
            for stats in disagreement_stats:
                self.disagreement_stats[(stats.provider_id, stats.ticker_id, stats.field_id)] = stats
        self.ingestion_coverage = ingestion_coverage if ingestion_coverage is not None else []
        self.data_quality_thresholds = data_quality_thresholds if data_quality_thresholds is not None else []

        self.fact_reconciliation: list[tuple[int, int, int, int, int, str]] = []
        self.fact_reconciliation_participant: list[tuple[int, int, int, int, int, bool]] = []
        self.fact_market_data: dict[tuple[int, int, int], tuple] = {}
        self.pending_manual_resolution: set[tuple[int, int, int, int]] = set()
        self.closed = False

    def fetch_dim_providers(self) -> list[ProviderRow]:
        return list(self.providers)

    def fetch_dim_field_groups(self) -> list[FieldGroupRow]:
        return list(self.field_groups)

    def fetch_dim_fields(self) -> list[FieldRow]:
        return list(self.fields)

    def fetch_provider_pair_disagreement(self) -> list[DisagreementStatsRow]:
        return list(self.disagreement_stats.values())

    def fetch_ingestion_coverage(self) -> list[IngestionCoverageRow]:
        return list(self.ingestion_coverage)

    def fetch_data_quality_thresholds(self) -> list[DataQualityThresholdRow]:
        return list(self.data_quality_thresholds)

    def fetch_whistleblower_accepted_staging_rows(self) -> list[StagingRow]:
        whistleblower_provider_ids: set[int] = set()
        for provider in self.providers:
            if provider.role == "whistleblower":
                whistleblower_provider_ids.add(provider.provider_id)

        result: list[StagingRow] = []
        for row in self.staging_rows:
            if row.provider_id in whistleblower_provider_ids and row.data_quality == "accepted":
                result.append(row)
        return result

    def mark_staging_bars_rejected(self, keys: list[tuple[int, int, int, int]]) -> None:
        key_set = set(keys)
        for row in self.staging_rows:
            if (row.provider_id, row.ticker_id, row.date_id, row.time_id) in key_set:
                row.data_quality = "rejected"

    def _pending_bar_keys(self) -> set[tuple[int, int, int]]:
        result: set[tuple[int, int, int]] = set()
        for ticker_id, date_id, time_id, _field_group_id in self.pending_manual_resolution:
            result.add((ticker_id, date_id, time_id))
        return result

    def fetch_staging_rows_for_reconciliation(self, expected_provider_names: list[str], required_provider_names: list[str]) -> list[StagingRow]:
        provider_name_by_id: dict[int, str] = {}
        for provider in self.providers:
            provider_name_by_id[provider.provider_id] = provider.name

        pending_bar_keys = self._pending_bar_keys()

        rows_by_bar: dict[tuple[int, int, int], list[StagingRow]] = {}
        for row in self.staging_rows:
            if provider_name_by_id.get(row.provider_id) not in expected_provider_names:
                continue
            bar_key = (row.ticker_id, row.date_id, row.time_id)
            if bar_key in pending_bar_keys:
                continue
            if bar_key not in rows_by_bar:
                rows_by_bar[bar_key] = []
            rows_by_bar[bar_key].append(row)

        result: list[StagingRow] = []
        for rows in rows_by_bar.values():
            reporting_names: set[str] = set()
            for row in rows:
                reporting_names.add(provider_name_by_id[row.provider_id])
            has_all_required = True
            for required_name in required_provider_names:
                if required_name not in reporting_names:
                    has_all_required = False
                    break
            if has_all_required:
                result.extend(rows)
        return result

    def fetch_pending_manual_resolution_staging_rows(self) -> list[StagingRow]:
        pending_bar_keys = self._pending_bar_keys()
        result: list[StagingRow] = []
        for row in self.staging_rows:
            if (row.ticker_id, row.date_id, row.time_id) in pending_bar_keys:
                result.append(row)
        return result

    def fetch_pending_manual_resolution_keys(self) -> set[tuple[int, int, int, int]]:
        return set(self.pending_manual_resolution)

    def mark_pending_manual_resolution(self, ticker_id: int, date_id: int, time_id: int, field_group_id: int) -> None:
        self.pending_manual_resolution.add((ticker_id, date_id, time_id, field_group_id))

    def clear_pending_manual_resolution(self, ticker_id: int, date_id: int, time_id: int, field_group_id: int) -> None:
        self.pending_manual_resolution.discard((ticker_id, date_id, time_id, field_group_id))

    def fetch_resolved_field_groups(self) -> dict[tuple[int, int, int, int], int]:
        staging_bar_keys: set[tuple[int, int, int]] = set()
        for row in self.staging_rows:
            staging_bar_keys.add((row.ticker_id, row.date_id, row.time_id))

        result: dict[tuple[int, int, int, int], int] = {}
        for ticker_id, date_id, time_id, field_group_id, winning_provider_id, _ in self.fact_reconciliation:
            if (ticker_id, date_id, time_id) in staging_bar_keys:
                result[(ticker_id, date_id, time_id, field_group_id)] = winning_provider_id
        return result

    def record_reconciliation(
        self,
        ticker_id: int,
        date_id: int,
        time_id: int,
        field_group_id: int,
        winning_provider_id: int,
        resolution_path: str,
        participant_results: list[tuple[int, bool]],
    ) -> None:
        self.fact_reconciliation.append((ticker_id, date_id, time_id, field_group_id, winning_provider_id, resolution_path))
        for provider_id, won in participant_results:
            self.fact_reconciliation_participant.append((ticker_id, date_id, time_id, field_group_id, provider_id, won))

    def promote_bar_to_fact(
        self,
        ticker_id: int,
        date_id: int,
        time_id: int,
        timestamp: datetime,
        open: float,
        high: float,
        low: float,
        close: float,
        volume: int,
        data_quality: str,
    ) -> None:
        self.fact_market_data[(ticker_id, date_id, time_id)] = (timestamp, open, high, low, close, volume, data_quality)

    def purge_staging_bar(self, ticker_id: int, date_id: int, time_id: int) -> None:
        whistleblower_provider_ids: set[int] = set()
        for provider in self.providers:
            if provider.role == "whistleblower":
                whistleblower_provider_ids.add(provider.provider_id)

        remaining: list[StagingRow] = []
        for row in self.staging_rows:
            if (row.ticker_id, row.date_id, row.time_id) == (ticker_id, date_id, time_id) and row.provider_id not in whistleblower_provider_ids:
                continue
            remaining.append(row)
        self.staging_rows = remaining

    def save_provider_pair_disagreement_batch(self, rows: list[tuple[int, int, int, int, float, float, float]]) -> None:
        for provider_id, ticker_id, field_id, sample_count, running_mean, running_m2, stddev in rows:
            self.disagreement_stats[(provider_id, ticker_id, field_id)] = DisagreementStatsRow(
                provider_id=provider_id,
                ticker_id=ticker_id,
                field_id=field_id,
                sample_count=sample_count,
                running_mean=running_mean,
                running_m2=running_m2,
            )

    def close(self) -> None:
        self.closed = True
