from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from shared.errors import TaskError
from shared.settings import Settings


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


def test_load_local_path_is_scoped_to_given_path_directory(tmp_path):
    # Regression guard: local_path used to default relative to the process's cwd regardless of
    # which `path` was passed, so loading a fixture elsewhere could silently pick up whatever
    # settings.local.json happened to sit in the real working directory. It must instead resolve
    # next to the given `path`.
    _write_settings(tmp_path / "settings.json", {"tickers": ["AAA"]})
    _write_settings(tmp_path / "settings.local.json", {"tickers": ["BBB"]})

    settings = Settings.load(path=tmp_path / "settings.json")

    assert settings.tickers == ["BBB"]
