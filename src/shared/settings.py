from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar

from .diagnostics import CATEGORY_GENERAL, TelemetryLevel
from .errors import TaskError

_SETTINGS_PATH = Path("./settings.json")
_LOCAL_PATH = Path("./settings.local.json")


@dataclass
class Settings:
    debug: bool
    logging: TelemetryLevel = TelemetryLevel.ERROR
    log_categories: list[str] = field(default_factory=list)
    excluded_categories: list[str] = field(default_factory=list)

    _instance: ClassVar[Settings | None] = None

    @classmethod
    def load(cls, path: Path = _SETTINGS_PATH, local_path: Path = _LOCAL_PATH) -> Settings:
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

        if local_path.exists():
            with local_path.open("r", encoding="utf-8") as f:
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

        cls._instance = cls(
            debug=debug,
            logging=log_level,
            log_categories=log_categories,
            excluded_categories=excluded_categories,
        )

        return cls._instance

    @classmethod
    def current(cls) -> Settings:
        if cls._instance is None:
            raise RuntimeError("Settings.load() must be called first.")
        return cls._instance
