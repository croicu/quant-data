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


def test_load_local_path_is_scoped_to_given_path_directory(tmp_path):
    # Regression guard: local_path used to default relative to the process's cwd regardless of
    # which `path` was passed, so loading a fixture elsewhere could silently pick up whatever
    # settings.local.json happened to sit in the real working directory. It must instead resolve
    # next to the given `path`.
    _write_settings(tmp_path / "settings.json", {"tickers": ["AAA"]})
    _write_settings(tmp_path / "settings.local.json", {"tickers": ["BBB"]})

    settings = Settings.load(path=tmp_path / "settings.json")

    assert settings.tickers == ["BBB"]
