from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import ClassVar

from .diagnostics import CATEGORY_GENERAL, LEVEL_RANK, TelemetryLevel
from .errors import TaskError
from .providers.ibkr import DEFAULT_CLIENT_ID as IBKR_DEFAULT_CLIENT_ID
from .providers.ibkr import DEFAULT_HOST as IBKR_DEFAULT_HOST
from .providers.ibkr import DEFAULT_PORT as IBKR_DEFAULT_PORT

_SETTINGS_PATH = Path("./settings.json")
_LOCAL_PATH_NAME = "settings.local.json"

# Deliberately empty -- no implicit provider (previously ["yfinance"], removed croicu/quant-data#64
# follow-up: silently defaulting to a single provider when nothing's configured is confusing,
# especially now that --providers/a response file makes an explicit choice cheap. ingest/cli.py and
# stage/cli.py both fail fast with a clear error when settings.providers ends up empty.
DEFAULT_PROVIDERS: list[str] = []
DEFAULT_PREFERRED_PROVIDER = "ibkr"
DEFAULT_RECONCILE_K = 3.0


def _default_providers() -> list[str]:
    return list(DEFAULT_PROVIDERS)


def _parse_rate_limit_payload(rate_limit_payload: dict, settings_key: str) -> RateLimitSettings:
    if "requestsPerWindow" not in rate_limit_payload or "windowSeconds" not in rate_limit_payload:
        raise TaskError(f"'{settings_key}.rateLimit' must set both 'requestsPerWindow' and 'windowSeconds'.")
    requests_per_window = int(rate_limit_payload["requestsPerWindow"])
    window_seconds = int(rate_limit_payload["windowSeconds"])
    if requests_per_window < 1:
        raise TaskError(f"'{settings_key}.rateLimit.requestsPerWindow' must be at least 1, got {requests_per_window}.")
    if window_seconds < 1:
        raise TaskError(f"'{settings_key}.rateLimit.windowSeconds' must be at least 1, got {window_seconds}.")
    return RateLimitSettings(requests_per_window=requests_per_window, window_seconds=window_seconds)


@dataclass
class ScheduleSettings:
    # quant_schedule (croicu/quant-data#66) -- a separate database, same server as the primary
    # connection above but its own dbname/user/password: unlike archive_dbname (which reuses the
    # primary user/password since quant_writer already spans quant_data + quant_ingest),
    # quant_schedule deliberately has its own two-role split -- quant_scheduler (read/insert/update,
    # for hand-managing job definitions via psql) and quant_worker (read/update only, what
    # quant-dispatch itself connects as -- it never inserts a row). Only quant_worker's credentials
    # are configured here; quant_scheduler is a manual-administration role never used by any code in
    # this repo, same "ships empty, inserted by hand" precedent as dataset_inception/jobs itself.
    dbname: str
    user: str
    password: str


@dataclass
class PostgresSettings:
    host: str
    port: int
    user: str
    password: str
    dbname: str
    ssh_user: str | None = None
    ssh_key_path: str | None = None
    # quant_ingest (croicu/quant-data#52) -- a separate database on the same server, same
    # host/port/user/password/ssh_user/ssh_key_path as the primary connection above, just a
    # different dbname. None means archiving to quant_ingest is disabled (e.g. an existing
    # settings.json predating this feature) -- quant-ingest still writes to staging as before.
    archive_dbname: str | None = None
    # quant_schedule (croicu/quant-data#66) -- see ScheduleSettings above. None means
    # quant-dispatch cannot run (it raises at startup) -- unlike archive_dbname, there's no
    # degraded-but-working mode, since jobs is quant-dispatch's entire reason to exist.
    schedule: ScheduleSettings | None = None


@dataclass
class RateLimitSettings:
    requests_per_window: int
    window_seconds: int


def _default_ibkr_rate_limit() -> RateLimitSettings:
    # Deliberately under IBKR's documented ceiling (60/600), not exactly at it -- margin against
    # clock skew or concurrent connections (croicu/quant-data#28). Unlike other providers,
    # "unspecified" for IBKR does not mean unlimited: it has a known real ceiling that should
    # always apply, so this is the dataclass default even when settings.json omits ibkr.rateLimit
    # entirely, not just the "if present" parsed value.
    return RateLimitSettings(requests_per_window=50, window_seconds=600)


@dataclass
class IbkrSettings:
    host: str = IBKR_DEFAULT_HOST
    port: int = IBKR_DEFAULT_PORT
    client_id: int = IBKR_DEFAULT_CLIENT_ID
    rate_limit: RateLimitSettings | None = field(default_factory=_default_ibkr_rate_limit)
    # None (the default) means "ingest all methods" -- IBKRIntraDay.DEFAULT_METHODS
    # (croicu/quant-data#60). A configured list restricts a run to just those methods; only IBKR
    # carries this today since it's the only provider genuinely multi-valued -- see
    # quant_data._internal.contracts.DEFAULT_METHODS_BY_PROVIDER.
    methods: list[str] | None = None


@dataclass
class YfinanceSettings:
    # None (the default) means unlimited -- yfinance has no known ceiling, unlike IBKR above.
    rate_limit: RateLimitSettings | None = None


def _default_massive_rate_limit() -> RateLimitSettings:
    # Massive's free Basic tier documents 5 calls/minute -- like IBKR above (and unlike yfinance),
    # "unspecified" does not mean unlimited: this is the dataclass default even when
    # settings.json omits massive.rateLimit entirely. croicu/quant-scratch#24's prototype found
    # this limit isn't strictly enforced in practice, which is why MassiveIntraDay also retries on
    # HTTP 429 rather than relying on pre-emptive limiting alone.
    return RateLimitSettings(requests_per_window=5, window_seconds=60)


@dataclass
class MassiveSettings:
    # No default -- a free-tier account credential, no usable local default, same reasoning as
    # DatabentoSettings.api_key in quant-scratch's own settings.
    api_key: str
    rate_limit: RateLimitSettings | None = field(default_factory=_default_massive_rate_limit)


@dataclass
class ReconcileSettings:
    preferred_provider: str = DEFAULT_PREFERRED_PROVIDER
    k: float = DEFAULT_RECONCILE_K


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
    providers: list[str] = field(default_factory=_default_providers)
    ibkr: IbkrSettings = field(default_factory=IbkrSettings)
    yfinance: YfinanceSettings = field(default_factory=YfinanceSettings)
    massive: MassiveSettings | None = None
    reconcile: ReconcileSettings = field(default_factory=ReconcileSettings)
    backfill_chunk_days: int = 1

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
        expand_categories = False

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

            if "logLevel" in settings_payload:
                # An explicit logLevel is more specific than the blanket debug flag, so it wins
                # outright whenever the two would otherwise disagree about how much to show:
                # setting a permissive level (e.g. "verbose") shouldn't be silently muted by
                # debug's own separate category default, and setting a restrictive level (e.g.
                # "critical") shouldn't be forced open just because debug=true. debug only falls
                # back into play here when logLevel was left at its implicit default.
                expand_categories = LEVEL_RANK[log_level] < LEVEL_RANK[TelemetryLevel.ERROR]
            else:
                expand_categories = debug

            log_categories_payload = settings_payload.get("logCategories", [])
            if not isinstance(log_categories_payload, list):
                raise TaskError("'settings.logCategories' in settings.json must be an array of strings.")
            log_categories = []
            for category_name in log_categories_payload:
                log_categories.append(str(category_name))

            if log_categories and expand_categories and CATEGORY_GENERAL not in log_categories:
                # Expanded-logging intent (see expand_categories above) always keeps
                # CATEGORY_GENERAL alongside an explicit narrower list — its baseline info should
                # stay visible even while zoomed into one category, not be silently dropped by
                # naming a single other category.
                log_categories = [CATEGORY_GENERAL] + log_categories

            excluded_categories_payload = settings_payload.get("excludedCategories", [])
            if not isinstance(excluded_categories_payload, list):
                raise TaskError("'settings.excludedCategories' in settings.json must be an array of strings.")
            excluded_categories = []
            for category_name in excluded_categories_payload:
                excluded_categories.append(str(category_name))

        if not log_categories:
            # No explicit override: restricts console noise to CATEGORY_GENERAL unless expanded
            # logging was signaled (via an explicit permissive logLevel, or debug=true when
            # logLevel was left at its default — see expand_categories above), in which case
            # everything shows, same as an empty filter always has.
            log_categories = [] if expand_categories else [CATEGORY_GENERAL]

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

            ssh_user_payload = postgres_payload.get("sshUser")
            ssh_key_path_payload = postgres_payload.get("sshKeyPath")
            if (ssh_user_payload is None) != (ssh_key_path_payload is None):
                raise TaskError("'settings.postgres.sshUser' and 'settings.postgres.sshKeyPath' must be set together (or both omitted).")

            archive_dbname_payload = postgres_payload.get("archiveDbname")

            schedule_settings: ScheduleSettings | None = None
            schedule_payload = postgres_payload.get("schedule")
            if schedule_payload is not None:
                if not isinstance(schedule_payload, dict):
                    raise TaskError("'settings.postgres.schedule' must be a JSON object.")
                schedule_required_keys = ["dbname", "user", "password"]
                schedule_missing_keys = []
                for key in schedule_required_keys:
                    if key not in schedule_payload:
                        schedule_missing_keys.append(key)
                if schedule_missing_keys:
                    raise TaskError(f"'settings.postgres.schedule' is missing required key(s): {', '.join(schedule_missing_keys)}")
                schedule_settings = ScheduleSettings(
                    dbname=str(schedule_payload["dbname"]),
                    user=str(schedule_payload["user"]),
                    password=str(schedule_payload["password"]),
                )

            postgres_settings = PostgresSettings(
                host=str(postgres_payload["host"]),
                port=int(postgres_payload["port"]),
                user=str(postgres_payload["user"]),
                password=str(postgres_payload["password"]),
                dbname=str(postgres_payload["dbname"]),
                ssh_user=str(ssh_user_payload) if ssh_user_payload is not None else None,
                ssh_key_path=str(ssh_key_path_payload) if ssh_key_path_payload is not None else None,
                archive_dbname=str(archive_dbname_payload) if archive_dbname_payload is not None else None,
                schedule=schedule_settings,
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

        backfill_chunk_days = int(settings_payload.get("backfillChunkDays", 1))
        if backfill_chunk_days < 1:
            raise TaskError(f"'settings.backfillChunkDays' must be at least 1, got {backfill_chunk_days}.")

        providers_payload = settings_payload.get("providers", list(DEFAULT_PROVIDERS))
        if not isinstance(providers_payload, list):
            raise TaskError("'settings.providers' must be an array of strings.")
        providers: list[str] = []
        for provider_name in providers_payload:
            # Lowercase, matching dim_provider.name's own CHECK constraint and this repo's
            # existing log-category convention (e.g. CATEGORY_YFINANCE = "yfinance") -- unlike
            # settings.tickers, which is uppercased instead, per that column's own convention.
            providers.append(str(provider_name).lower())

        ibkr_settings = IbkrSettings()
        ibkr_payload = settings_payload.get("ibkr")
        if ibkr_payload is not None:
            if not isinstance(ibkr_payload, dict):
                raise TaskError("'settings.ibkr' must be a JSON object.")
            ibkr_rate_limit = _default_ibkr_rate_limit()
            ibkr_rate_limit_payload = ibkr_payload.get("rateLimit")
            if ibkr_rate_limit_payload is not None:
                if not isinstance(ibkr_rate_limit_payload, dict):
                    raise TaskError("'settings.ibkr.rateLimit' must be a JSON object.")
                ibkr_rate_limit = _parse_rate_limit_payload(ibkr_rate_limit_payload, "settings.ibkr")
            ibkr_methods: list[str] | None = None
            ibkr_methods_payload = ibkr_payload.get("methods")
            if ibkr_methods_payload is not None:
                if not isinstance(ibkr_methods_payload, list) or not ibkr_methods_payload:
                    raise TaskError("'settings.ibkr.methods' must be a non-empty array of strings.")
                ibkr_methods = []
                for method_name in ibkr_methods_payload:
                    ibkr_methods.append(str(method_name))
            ibkr_settings = IbkrSettings(
                host=str(ibkr_payload.get("host", IBKR_DEFAULT_HOST)),
                port=int(ibkr_payload.get("port", IBKR_DEFAULT_PORT)),
                client_id=int(ibkr_payload.get("clientId", IBKR_DEFAULT_CLIENT_ID)),
                rate_limit=ibkr_rate_limit,
                methods=ibkr_methods,
            )

        yfinance_settings = YfinanceSettings()
        yfinance_payload = settings_payload.get("yfinance")
        if yfinance_payload is not None:
            if not isinstance(yfinance_payload, dict):
                raise TaskError("'settings.yfinance' must be a JSON object.")
            yfinance_rate_limit: RateLimitSettings | None = None
            yfinance_rate_limit_payload = yfinance_payload.get("rateLimit")
            if yfinance_rate_limit_payload is not None:
                if not isinstance(yfinance_rate_limit_payload, dict):
                    raise TaskError("'settings.yfinance.rateLimit' must be a JSON object.")
                yfinance_rate_limit = _parse_rate_limit_payload(yfinance_rate_limit_payload, "settings.yfinance")
            yfinance_settings = YfinanceSettings(rate_limit=yfinance_rate_limit)

        massive_settings: MassiveSettings | None = None
        massive_payload = settings_payload.get("massive")
        if massive_payload is not None:
            if not isinstance(massive_payload, dict):
                raise TaskError("'settings.massive' must be a JSON object.")
            if "apiKey" not in massive_payload:
                raise TaskError("'settings.massive' is missing required key(s): apiKey")
            massive_rate_limit = _default_massive_rate_limit()
            massive_rate_limit_payload = massive_payload.get("rateLimit")
            if massive_rate_limit_payload is not None:
                if not isinstance(massive_rate_limit_payload, dict):
                    raise TaskError("'settings.massive.rateLimit' must be a JSON object.")
                massive_rate_limit = _parse_rate_limit_payload(massive_rate_limit_payload, "settings.massive")
            massive_settings = MassiveSettings(api_key=str(massive_payload["apiKey"]), rate_limit=massive_rate_limit)

        reconcile_settings = ReconcileSettings()
        reconcile_payload = settings_payload.get("reconcile")
        if reconcile_payload is not None:
            if not isinstance(reconcile_payload, dict):
                raise TaskError("'settings.reconcile' must be a JSON object.")
            reconcile_k = float(reconcile_payload.get("k", DEFAULT_RECONCILE_K))
            if reconcile_k <= 0:
                raise TaskError(f"'settings.reconcile.k' must be positive, got {reconcile_k}.")
            reconcile_settings = ReconcileSettings(
                preferred_provider=str(reconcile_payload.get("preferredProvider", DEFAULT_PREFERRED_PROVIDER)).lower(),
                k=reconcile_k,
            )

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
            providers=providers,
            ibkr=ibkr_settings,
            yfinance=yfinance_settings,
            massive=massive_settings,
            reconcile=reconcile_settings,
            backfill_chunk_days=backfill_chunk_days,
        )

        return cls._instance

    @classmethod
    def current(cls) -> Settings:
        if cls._instance is None:
            raise RuntimeError("Settings.load() must be called first.")
        return cls._instance
