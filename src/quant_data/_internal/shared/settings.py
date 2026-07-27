from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import ClassVar

from .diagnostics import CATEGORY_GENERAL, TelemetryLevel
from .errors import TaskError

_SETTINGS_PATH = Path("./settings.json")
_LOCAL_PATH_NAME = "settings.local.json"


@dataclass
class PostgresSettings:
    host: str
    port: int
    user: str
    password: str
    dbname: str


@dataclass
class Settings:
    debug: bool
    logging: TelemetryLevel = TelemetryLevel.ERROR
    log_categories: list[str] = field(default_factory=list)
    excluded_categories: list[str] = field(default_factory=list)
    postgres: PostgresSettings | None = None
    tickers: list[str] = field(default_factory=list)
    start_date: date | None = None
    end_date: date | None = None
    catch_up_lookback_days: int = 7

    _instance: ClassVar[Settings | None] = None

    @classmethod
    def load(cls, path: Path = _SETTINGS_PATH, local_path: Path | None = None) -> Settings:
        # local_path defaults relative to path's own directory, not the process's cwd -- so a
        # test loading a fixture path doesn't silently pick up the real repo-root
        # settings.local.json just because pytest happens to run from the repo root.
        resolved_local_path = local_path if local_path is not None else path.parent / _LOCAL_PATH_NAME

        debug = False
        log_level = TelemetryLevel.ERROR
        log_categories: list[str] = []
        excluded_categories: list[str] = []

        settings_payload: dict = {}

        if path.exists():
            with path.open("r", encoding="utf-8") as f:
                settings_file = json.load(f)
            if not isinstance(settings_file, dict):
                raise TaskError("settings.json must contain a JSON object.")
            base_settings = settings_file.get("settings", {})
            if not isinstance(base_settings, dict):
                raise TaskError("'settings' in settings.json must be a JSON object.")
            settings_payload = dict(base_settings)

        if resolved_local_path.exists():
            with resolved_local_path.open("r", encoding="utf-8") as f:
                local_payload = json.load(f)
            if isinstance(local_payload, dict):
                local_settings = local_payload.get("settings", {})
                if isinstance(local_settings, dict):
                    settings_payload.update(local_settings)

        if settings_payload:
            debug = bool(settings_payload.get("debug", False))
            try:
                log_level = TelemetryLevel(settings_payload.get("logLevel", "error"))
            except ValueError:
                valid_levels = []
                for level in TelemetryLevel:
                    valid_levels.append(level.value)
                raise TaskError(f"'settings.logLevel' in settings.json must be one of: {', '.join(valid_levels)}")

            log_categories_payload = settings_payload.get("logCategories", [])
            if not isinstance(log_categories_payload, list):
                raise TaskError("'settings.logCategories' in settings.json must be an array of strings.")
            log_categories = []
            for category_name in log_categories_payload:
                log_categories.append(str(category_name))

            if log_categories and debug and CATEGORY_GENERAL not in log_categories:
                # debug=true always keeps CATEGORY_GENERAL alongside an explicit narrower list —
                # debug mode's baseline info should stay visible even while zoomed into one
                # category, not be silently dropped by naming a single other category.
                log_categories = [CATEGORY_GENERAL] + log_categories

            excluded_categories_payload = settings_payload.get("excludedCategories", [])
            if not isinstance(excluded_categories_payload, list):
                raise TaskError("'settings.excludedCategories' in settings.json must be an array of strings.")
            excluded_categories = []
            for category_name in excluded_categories_payload:
                excluded_categories.append(str(category_name))

        if not log_categories:
            # No explicit override: debug=false restricts console noise to CATEGORY_GENERAL;
            # debug=true shows everything, same as an empty filter always has.
            log_categories = [] if debug else [CATEGORY_GENERAL]

        postgres_settings: PostgresSettings | None = None
        postgres_payload = settings_payload.get("postgres")
        if postgres_payload is not None:
            if not isinstance(postgres_payload, dict):
                raise TaskError("'settings.postgres' must be a JSON object.")
            required_keys = ["host", "port", "user", "password", "dbname"]
            missing_keys = []
            for key in required_keys:
                if key not in postgres_payload:
                    missing_keys.append(key)
            if missing_keys:
                raise TaskError(f"'settings.postgres' is missing required key(s): {', '.join(missing_keys)}")
            postgres_settings = PostgresSettings(
                host=str(postgres_payload["host"]),
                port=int(postgres_payload["port"]),
                user=str(postgres_payload["user"]),
                password=str(postgres_payload["password"]),
                dbname=str(postgres_payload["dbname"]),
            )

        tickers_payload = settings_payload.get("tickers", [])
        if not isinstance(tickers_payload, list):
            raise TaskError("'settings.tickers' must be an array of strings.")
        tickers: list[str] = []
        for ticker_name in tickers_payload:
            tickers.append(str(ticker_name).upper())

        start_date_setting: date | None = None
        start_date_payload = settings_payload.get("startDate")
        if start_date_payload is not None:
            try:
                start_date_setting = date.fromisoformat(str(start_date_payload))
            except ValueError as error:
                raise TaskError(f"'settings.startDate' must be YYYY-MM-DD: {error}")

        end_date_setting: date | None = None
        end_date_payload = settings_payload.get("endDate")
        if end_date_payload is not None:
            if start_date_setting is None:
                raise TaskError("'settings.endDate' requires 'settings.startDate' to also be set.")
            try:
                end_date_setting = date.fromisoformat(str(end_date_payload))
            except ValueError as error:
                raise TaskError(f"'settings.endDate' must be YYYY-MM-DD: {error}")
        elif start_date_setting is not None:
            # endDate omitted: a single day, same as startDate.
            end_date_setting = start_date_setting

        if start_date_setting is not None and end_date_setting is not None and end_date_setting < start_date_setting:
            raise TaskError(f"'settings.endDate' ({end_date_setting.isoformat()}) must not be before 'settings.startDate' ({start_date_setting.isoformat()}).")

        catch_up_lookback_days = int(settings_payload.get("catchUpLookbackDays", 7))
        if catch_up_lookback_days < 1:
            raise TaskError(f"'settings.catchUpLookbackDays' must be at least 1, got {catch_up_lookback_days}.")

        cls._instance = cls(
            debug=debug,
            logging=log_level,
            log_categories=log_categories,
            excluded_categories=excluded_categories,
            postgres=postgres_settings,
            tickers=tickers,
            start_date=start_date_setting,
            end_date=end_date_setting,
            catch_up_lookback_days=catch_up_lookback_days,
        )

        return cls._instance

    @classmethod
    def current(cls) -> Settings:
        if cls._instance is None:
            raise RuntimeError("Settings.load() must be called first.")
        return cls._instance
