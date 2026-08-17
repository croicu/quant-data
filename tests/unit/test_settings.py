from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from quant_data._internal.shared.errors import TaskError
from quant_data._internal.shared.settings import Settings


def _write_settings(path: Path, payload: dict) -> Path:
    path.write_text(json.dumps({"settings": payload}), encoding="utf-8")
    return path


def test_load_parses_start_and_end_date(tmp_path):
    settings_path = _write_settings(tmp_path / "settings.json", {"startDate": "2026-01-02", "endDate": "2026-01-05"})

    settings = Settings.load(path=settings_path)

    assert settings.start_date == date(2026, 1, 2)
    assert settings.end_date == date(2026, 1, 5)


def test_load_defaults_start_and_end_date_to_none(tmp_path):
    settings_path = _write_settings(tmp_path / "settings.json", {"debug": False})

    settings = Settings.load(path=settings_path)

    assert settings.start_date is None
    assert settings.end_date is None


def test_load_defaults_end_date_to_start_date_when_only_start_date_given(tmp_path):
    settings_path = _write_settings(tmp_path / "settings.json", {"startDate": "2026-01-02"})

    settings = Settings.load(path=settings_path)

    assert settings.start_date == date(2026, 1, 2)
    assert settings.end_date == date(2026, 1, 2)


def test_load_raises_when_only_end_date_given(tmp_path):
    settings_path = _write_settings(tmp_path / "settings.json", {"endDate": "2026-01-05"})

    with pytest.raises(TaskError):
        Settings.load(path=settings_path)


def test_load_raises_when_end_date_before_start_date(tmp_path):
    settings_path = _write_settings(tmp_path / "settings.json", {"startDate": "2026-01-05", "endDate": "2026-01-02"})

    with pytest.raises(TaskError):
        Settings.load(path=settings_path)


def test_load_raises_on_malformed_start_date(tmp_path):
    settings_path = _write_settings(tmp_path / "settings.json", {"startDate": "not-a-date", "endDate": "2026-01-05"})

    with pytest.raises(TaskError):
        Settings.load(path=settings_path)


def test_load_defaults_catch_up_lookback_days_to_seven(tmp_path):
    settings_path = _write_settings(tmp_path / "settings.json", {"debug": False})

    settings = Settings.load(path=settings_path)

    assert settings.catch_up_lookback_days == 7


def test_load_parses_catch_up_lookback_days(tmp_path):
    settings_path = _write_settings(tmp_path / "settings.json", {"catchUpLookbackDays": 14})

    settings = Settings.load(path=settings_path)

    assert settings.catch_up_lookback_days == 14


def test_load_raises_on_non_positive_catch_up_lookback_days(tmp_path):
    settings_path = _write_settings(tmp_path / "settings.json", {"catchUpLookbackDays": 0})

    with pytest.raises(TaskError):
        Settings.load(path=settings_path)


def test_load_defaults_backfill_chunk_days_to_one(tmp_path):
    settings_path = _write_settings(tmp_path / "settings.json", {"debug": False})

    settings = Settings.load(path=settings_path)

    assert settings.backfill_chunk_days == 1


def test_load_parses_backfill_chunk_days(tmp_path):
    settings_path = _write_settings(tmp_path / "settings.json", {"backfillChunkDays": 5})

    settings = Settings.load(path=settings_path)

    assert settings.backfill_chunk_days == 5


def test_load_raises_on_non_positive_backfill_chunk_days(tmp_path):
    settings_path = _write_settings(tmp_path / "settings.json", {"backfillChunkDays": 0})

    with pytest.raises(TaskError):
        Settings.load(path=settings_path)


def test_load_default_debug_false_no_log_level_restricts_to_general(tmp_path):
    settings_path = _write_settings(tmp_path / "settings.json", {"debug": False})

    settings = Settings.load(path=settings_path)

    assert settings.log_categories == ["general"]


def test_load_default_debug_true_no_log_level_is_unfiltered(tmp_path):
    settings_path = _write_settings(tmp_path / "settings.json", {"debug": True})

    settings = Settings.load(path=settings_path)

    assert settings.log_categories == []


def test_load_explicit_verbose_log_level_unfilters_categories_even_with_debug_false(tmp_path):
    # Regression guard: an explicit permissive logLevel is more specific than the blanket debug
    # flag and must win outright -- setting logLevel="verbose" alone (without also flipping
    # debug=true) must not be silently muted by debug's own separate category default.
    settings_path = _write_settings(tmp_path / "settings.json", {"debug": False, "logLevel": "verbose"})

    settings = Settings.load(path=settings_path)

    assert settings.log_categories == []


def test_load_explicit_critical_log_level_restricts_categories_even_with_debug_true(tmp_path):
    # The same precedence in the other direction: an explicit restrictive logLevel wins even
    # when debug=true would otherwise have unfiltered everything.
    settings_path = _write_settings(tmp_path / "settings.json", {"debug": True, "logLevel": "critical"})

    settings = Settings.load(path=settings_path)

    assert settings.log_categories == ["general"]


def test_load_explicit_error_log_level_restricts_categories_same_as_default(tmp_path):
    # An explicit logLevel matching the implicit default ("error") behaves the same as today's
    # default, even though it's technically "explicit".
    settings_path = _write_settings(tmp_path / "settings.json", {"debug": False, "logLevel": "error"})

    settings = Settings.load(path=settings_path)

    assert settings.log_categories == ["general"]


def test_load_explicit_log_categories_always_wins_regardless_of_log_level(tmp_path):
    settings_path = _write_settings(
        tmp_path / "settings.json",
        {"debug": False, "logLevel": "critical", "logCategories": ["ingest"]},
    )

    settings = Settings.load(path=settings_path)

    assert settings.log_categories == ["ingest"]


def test_load_verbose_log_level_widens_explicit_narrow_categories_to_include_general(tmp_path):
    settings_path = _write_settings(
        tmp_path / "settings.json",
        {"debug": False, "logLevel": "verbose", "logCategories": ["ingest"]},
    )

    settings = Settings.load(path=settings_path)

    assert settings.log_categories == ["general", "ingest"]


def _postgres_payload(**overrides) -> dict:
    payload = {"host": "localhost", "port": 5433, "user": "quant_reader", "password": "", "dbname": "quant_data"}
    payload.update(overrides)
    return payload


def test_load_postgres_settings_default_ssh_fields_to_none(tmp_path):
    settings_path = _write_settings(tmp_path / "settings.json", {"postgres": _postgres_payload()})

    settings = Settings.load(path=settings_path)

    assert settings.postgres.ssh_user is None
    assert settings.postgres.ssh_key_path is None


def test_load_postgres_settings_parses_ssh_fields_when_both_given(tmp_path):
    settings_path = _write_settings(
        tmp_path / "settings.json",
        {"postgres": _postgres_payload(sshUser="alex", sshKeyPath="/home/alex/.ssh/id_ed25519")},
    )

    settings = Settings.load(path=settings_path)

    assert settings.postgres.ssh_user == "alex"
    assert settings.postgres.ssh_key_path == "/home/alex/.ssh/id_ed25519"


def test_load_postgres_settings_raises_when_only_ssh_user_given(tmp_path):
    settings_path = _write_settings(tmp_path / "settings.json", {"postgres": _postgres_payload(sshUser="alex")})

    with pytest.raises(TaskError):
        Settings.load(path=settings_path)


def test_load_postgres_settings_raises_when_only_ssh_key_path_given(tmp_path):
    settings_path = _write_settings(tmp_path / "settings.json", {"postgres": _postgres_payload(sshKeyPath="/home/alex/.ssh/id_ed25519")})

    with pytest.raises(TaskError):
        Settings.load(path=settings_path)


def test_load_postgres_settings_defaults_archive_dbname_to_none(tmp_path):
    settings_path = _write_settings(tmp_path / "settings.json", {"postgres": _postgres_payload()})

    settings = Settings.load(path=settings_path)

    assert settings.postgres.archive_dbname is None


def test_load_postgres_settings_parses_archive_dbname_when_given(tmp_path):
    settings_path = _write_settings(tmp_path / "settings.json", {"postgres": _postgres_payload(archiveDbname="quant_ingest")})

    settings = Settings.load(path=settings_path)

    assert settings.postgres.archive_dbname == "quant_ingest"


def test_load_defaults_providers_to_yfinance_only(tmp_path):
    settings_path = _write_settings(tmp_path / "settings.json", {"debug": False})

    settings = Settings.load(path=settings_path)

    assert settings.providers == ["yfinance"]


def test_load_parses_providers_and_lowercases_them(tmp_path):
    settings_path = _write_settings(tmp_path / "settings.json", {"providers": ["YFinance", "IBKR"]})

    settings = Settings.load(path=settings_path)

    assert settings.providers == ["yfinance", "ibkr"]


def test_load_raises_when_providers_is_not_a_list(tmp_path):
    settings_path = _write_settings(tmp_path / "settings.json", {"providers": "yfinance"})

    with pytest.raises(TaskError):
        Settings.load(path=settings_path)


def test_load_defaults_ibkr_settings(tmp_path):
    settings_path = _write_settings(tmp_path / "settings.json", {"debug": False})

    settings = Settings.load(path=settings_path)

    assert settings.ibkr.host == "127.0.0.1"
    assert settings.ibkr.port == 4002
    assert settings.ibkr.client_id == 1


def test_load_parses_ibkr_settings_overrides(tmp_path):
    settings_path = _write_settings(tmp_path / "settings.json", {"ibkr": {"host": "10.0.0.5", "port": 7497, "clientId": 9}})

    settings = Settings.load(path=settings_path)

    assert settings.ibkr.host == "10.0.0.5"
    assert settings.ibkr.port == 7497
    assert settings.ibkr.client_id == 9


def test_load_raises_when_ibkr_settings_is_not_an_object(tmp_path):
    settings_path = _write_settings(tmp_path / "settings.json", {"ibkr": "not-an-object"})

    with pytest.raises(TaskError):
        Settings.load(path=settings_path)


def test_load_defaults_ibkr_rate_limit_even_when_ibkr_block_omitted(tmp_path):
    # Unlike other providers, "unspecified" for IBKR does not mean unlimited -- it has a known
    # real ceiling that always applies (croicu/quant-data#28).
    settings_path = _write_settings(tmp_path / "settings.json", {"debug": False})

    settings = Settings.load(path=settings_path)

    assert settings.ibkr.rate_limit is not None
    assert settings.ibkr.rate_limit.requests_per_window == 50
    assert settings.ibkr.rate_limit.window_seconds == 600


def test_load_defaults_ibkr_rate_limit_when_ibkr_block_present_but_rate_limit_omitted(tmp_path):
    settings_path = _write_settings(tmp_path / "settings.json", {"ibkr": {"host": "10.0.0.5"}})

    settings = Settings.load(path=settings_path)

    assert settings.ibkr.rate_limit.requests_per_window == 50
    assert settings.ibkr.rate_limit.window_seconds == 600


def test_load_parses_ibkr_rate_limit_override(tmp_path):
    settings_path = _write_settings(tmp_path / "settings.json", {"ibkr": {"rateLimit": {"requestsPerWindow": 30, "windowSeconds": 300}}})

    settings = Settings.load(path=settings_path)

    assert settings.ibkr.rate_limit.requests_per_window == 30
    assert settings.ibkr.rate_limit.window_seconds == 300


def test_load_raises_when_ibkr_rate_limit_is_missing_a_key(tmp_path):
    settings_path = _write_settings(tmp_path / "settings.json", {"ibkr": {"rateLimit": {"requestsPerWindow": 30}}})

    with pytest.raises(TaskError):
        Settings.load(path=settings_path)


def test_load_defaults_yfinance_rate_limit_to_unlimited(tmp_path):
    settings_path = _write_settings(tmp_path / "settings.json", {"debug": False})

    settings = Settings.load(path=settings_path)

    assert settings.yfinance.rate_limit is None


def test_load_parses_yfinance_rate_limit_override(tmp_path):
    settings_path = _write_settings(tmp_path / "settings.json", {"yfinance": {"rateLimit": {"requestsPerWindow": 100, "windowSeconds": 60}}})

    settings = Settings.load(path=settings_path)

    assert settings.yfinance.rate_limit.requests_per_window == 100
    assert settings.yfinance.rate_limit.window_seconds == 60


def test_load_raises_when_yfinance_settings_is_not_an_object(tmp_path):
    settings_path = _write_settings(tmp_path / "settings.json", {"yfinance": "not-an-object"})

    with pytest.raises(TaskError):
        Settings.load(path=settings_path)


def test_load_defaults_massive_settings_to_none(tmp_path):
    settings_path = _write_settings(tmp_path / "settings.json", {"debug": False})

    settings = Settings.load(path=settings_path)

    assert settings.massive is None


def test_load_parses_massive_api_key(tmp_path):
    settings_path = _write_settings(tmp_path / "settings.json", {"massive": {"apiKey": "abc123"}})

    settings = Settings.load(path=settings_path)

    assert settings.massive.api_key == "abc123"


def test_load_raises_when_massive_settings_is_not_an_object(tmp_path):
    settings_path = _write_settings(tmp_path / "settings.json", {"massive": "not-an-object"})

    with pytest.raises(TaskError):
        Settings.load(path=settings_path)


def test_load_raises_when_massive_api_key_missing(tmp_path):
    settings_path = _write_settings(tmp_path / "settings.json", {"massive": {}})

    with pytest.raises(TaskError):
        Settings.load(path=settings_path)


def test_load_defaults_massive_rate_limit_when_massive_block_present_but_rate_limit_omitted(tmp_path):
    # Like IBKR (and unlike yfinance), "unspecified" for Massive does not mean unlimited -- it has
    # a documented real ceiling that always applies once settings.massive is configured at all.
    settings_path = _write_settings(tmp_path / "settings.json", {"massive": {"apiKey": "abc123"}})

    settings = Settings.load(path=settings_path)

    assert settings.massive.rate_limit is not None
    assert settings.massive.rate_limit.requests_per_window == 5
    assert settings.massive.rate_limit.window_seconds == 60


def test_load_parses_massive_rate_limit_override(tmp_path):
    settings_path = _write_settings(tmp_path / "settings.json", {"massive": {"apiKey": "abc123", "rateLimit": {"requestsPerWindow": 10, "windowSeconds": 120}}})

    settings = Settings.load(path=settings_path)

    assert settings.massive.rate_limit.requests_per_window == 10
    assert settings.massive.rate_limit.window_seconds == 120


def test_load_raises_when_massive_rate_limit_is_missing_a_key(tmp_path):
    settings_path = _write_settings(tmp_path / "settings.json", {"massive": {"apiKey": "abc123", "rateLimit": {"requestsPerWindow": 10}}})

    with pytest.raises(TaskError):
        Settings.load(path=settings_path)


def test_load_defaults_reconcile_settings(tmp_path):
    settings_path = _write_settings(tmp_path / "settings.json", {"debug": False})

    settings = Settings.load(path=settings_path)

    assert settings.reconcile.preferred_provider == "ibkr"
    assert settings.reconcile.k == 3.0


def test_load_parses_reconcile_settings_overrides(tmp_path):
    settings_path = _write_settings(tmp_path / "settings.json", {"reconcile": {"preferredProvider": "IBKR", "k": 2.5}})

    settings = Settings.load(path=settings_path)

    assert settings.reconcile.preferred_provider == "ibkr"
    assert settings.reconcile.k == 2.5


def test_load_raises_when_reconcile_settings_is_not_an_object(tmp_path):
    settings_path = _write_settings(tmp_path / "settings.json", {"reconcile": "not-an-object"})

    with pytest.raises(TaskError):
        Settings.load(path=settings_path)


def test_load_raises_when_reconcile_k_is_not_positive(tmp_path):
    settings_path = _write_settings(tmp_path / "settings.json", {"reconcile": {"k": 0}})

    with pytest.raises(TaskError):
        Settings.load(path=settings_path)


def test_load_local_path_is_scoped_to_given_path_directory(tmp_path):
    # Regression guard: local_path used to default relative to the process's cwd regardless of
    # which `path` was passed, so loading a fixture elsewhere could silently pick up whatever
    # settings.local.json happened to sit in the real working directory. It must instead resolve
    # next to the given `path`.
    _write_settings(tmp_path / "settings.json", {"tickers": ["AAA"]})
    _write_settings(tmp_path / "settings.local.json", {"tickers": ["BBB"]})

    settings = Settings.load(path=tmp_path / "settings.json")

    assert settings.tickers == ["BBB"]
