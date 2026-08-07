# ARCHITECTURE.md

Modules, data flow, and contracts for `quant-data`.

## Layout

Two top-level packages under `src/`:

- `src/quant_data/` — the distribution's namespaced package.
  - `__init__.py` — plain re-exports of `MarketData`, `OHLCV`, and `create_postgres_provider`
    (`__all__` + top-level imports; no laziness needed — see "Why no laziness" below).
  - `protocols.py` — `OHLCV` (pure-data contract). **Public.**
  - `client/market_data.py` — `MarketData`, agnostic of the concrete backend (see "Contracts"
    below). **Public.**
  - `client/postgres_provider.py` — `create_postgres_provider`, today's factory for building a
    Postgres-backed provider to hand to `MarketData`. **Public.**
  - `_internal/` — **private** implementation detail, nested so its privacy is a folder-level
    fact, not just an `__init__.py` allow-list. Nothing here is exported by `quant_data`, and none
    of it should be imported directly by external consumers, even though Python doesn't stop you.
    - `contracts.py` — behavioral `Protocol`s (`MarketDataProvider`, `IntraDayProvider`,
      `ConnectionTransport`).
    - `shared/` — cross-app infra: `diagnostics.py` (`Logger`), `errors.py`
      (`AppError`/`TaskError`/`DateOutOfRangeError`), `settings.py` (`Settings`), `postgres.py`
      (`PostgresDatabase`), `providers/` (external data-source clients, e.g. `yfinance.py`),
      `transports/` (`ConnectionTransport` implementations, e.g. `DirectTransport`,
      `SshTunnelTransport`).
- `src/ingest/` — the ingest CLI. Console script: `quant-ingest`, no importable surface at all.

**Public surface**: external consumers (`quant-scratch`) should only depend on the `quant_data`
top level — `from quant_data import MarketData, OHLCV, LoggingSink, create_postgres_provider`. Since
`client`/`protocols.py` hold only genuinely public content and `_internal/` holds none, the folder
split itself signals what's safe to depend on, instead of relying solely on an `__init__.py`
allow-list (see croicu/quant-data#10).

**Why nested `_internal/`, not a second top-level package**: this repo's history went through three
shapes. First, it mirrored `quant-scratch`'s flat top-level-package convention (`defs`, `shared`,
... with no common prefix, see commit 4ca99cf) — broke the moment `quant-data` was installed as a
pip dependency *alongside* `quant-scratch` in the same environment, since both repos' top-level
`defs`/`shared` packages shared the same import name and whichever installed last silently shadowed
the other's entirely (croicu/quant-data#7, reproduced both installation orders). Second, everything
moved under `quant_data.*` — collision-safe, but conflated "collision-safe" with "public":
`quant_data.client`/`quant_data.defs`/`quant_data.shared` were all equally collision-proof, but only
some of their contents were actually meant for external consumers. Third, the private half became
its own sibling top-level package, `quant_data_internal` — folder-level public/private clarity, but
this is what caused a real, reproduced circular-import crash (see "Why no laziness" below), because
a second independent top-level package with a genuine two-way dependency on the first loses a
Python import-ordering guarantee that nesting keeps. **Current (fourth) shape**: `_internal/`
nested inside `quant_data/` — same folder-level public/private clarity as the sibling-package
version, collision-safety unaffected (nesting under `quant_data.` was already sufficient — the
sibling package's own name, `quant_data_internal`, was never actually the thing preventing
collisions; `quant_data.`'s own uniqueness was), and the circular-import risk goes away because
nesting keeps Python's own import-completion ordering on your side (see below).

Cross-package imports are absolute with the full package prefix (`from
quant_data._internal.shared.diagnostics import Logger`); same-package imports stay relative (`from
.errors import ...`).

**Why no laziness is needed** (unlike the earlier sibling-package shape, which needed a
module-level `__getattr__` workaround): `quant_data._internal.shared.postgres` needs `OHLCV` from
`quant_data.protocols`, and `create_postgres_provider` needs `PostgresDatabase` from
`quant_data._internal.shared.postgres` in turn — a real two-way relationship between the public and
private halves (`MarketData` itself doesn't have this problem — see "Contracts" below — only the
Postgres-specific factory does). When `_internal` was a separate top-level package
(`quant_data_internal`), eagerly importing at `quant_data/__init__.py`'s module scope turned this
into an actual circular import whenever something touched `quant_data_internal` before `quant_data`
itself (reproduced directly: `import quant_data_internal.shared.postgres` as the first import in a
process raised `ImportError: cannot import name 'PostgresDatabase' from partially initialized
module`). Nesting removes the failure mode structurally: importing a dotted path like
`quant_data._internal.shared.postgres` requires Python to fully finish importing `quant_data` (i.e.
run its whole `__init__.py`) *before it even attempts* the `._internal` segment — so by the time
Python would try to import `quant_data._internal.shared.postgres` a second time (the self-reference
that caused the crash), it's already been loaded as a side effect of finishing `quant_data`'s own
`__init__.py`, not still mid-execution. Verified directly: re-ran the exact `import
quant_data._internal.shared.postgres`-first reproduction against the nested layout with a plain,
eager `__init__.py` — no crash. A second top-level package doesn't get this guarantee, since Python
has no "finish importing the whole first package before touching the second" rule between two
independent top-level packages.

## Modules

### `quant_data.protocols`

- `OHLCV`: ticker, timestamp (UTC), open/high/low/close, volume, and `data_quality`
  (`DataQuality`, defaults `ACCEPTED`) — set when the provider couldn't supply full data for that
  minute, or (not yet implemented) a per-provider plausibility check rejected the value (see
  `docs/SCHEMA.md`'s `fact_market_data_1min.data_quality`). Re-exported at the `quant_data` top
  level. **Breaking change (`009_replace_incomplete_with_data_quality`)**: this field was
  `incomplete: bool` before; now `data_quality: DataQuality`.
- `ProviderRole(Enum)`: `CANDIDATE`/`WHISTLEBLOWER`, mirroring `dim_provider.role`'s `CHECK`
  constraint. A closed set (unlike e.g. `LoggingSink`'s open `category` strings), so this follows
  the same pattern as `_internal.shared.diagnostics.TelemetryLevel` — the one other closed-set
  string column in this codebase already modeled as an `Enum` — rather than a plain `str`.
  Re-exported at the `quant_data` top level.
- `DataQuality(Enum)`: `ACCEPTED`/`INCOMPLETE`/`REJECTED`, mirroring
  `staging_market_data_1min`/`fact_market_data_1min.data_quality`'s `CHECK` constraint — same
  closed-set-`Enum` precedent as `ProviderRole`. `REJECTED` is treated identically to `INCOMPLETE`
  by reconcile's Tier 1 completeness check; the distinction is for audit/debugging only.
- `PendingResolutionBar`: `field_group` (`str`), `provider` (`str`), `role` (`ProviderRole`),
  `bar` (`OHLCV`) — one provider's disputed staging value for a (bar, field group) still awaiting
  manual resolution (`fact_pending_manual_resolution`). A bar is pending precisely because its
  reporting providers disagree, so `MarketDataProvider.fetch_pending_resolution_bars` returns one
  entry per (bar, field group, provider) rather than a single `OHLCV`, letting a caller see the
  actual disagreement instead of just that a bar is stuck. `role` (sourced from
  `dim_provider.role`) is what actually lets a caller identify the reference value among possibly
  several candidates — today's data is exactly one whistleblower (`yfinance`) plus one candidate
  (`ibkr`), but `dim_provider` isn't hardcoded to two rows, so don't assume exactly one candidate.
  Re-exported at the `quant_data` top level.
- `RejectedWhistleblowerBar`: `provider` (`str`), `bar` (`OHLCV`) — a whistleblower-reported
  staging value with `data_quality=REJECTED`. Deliberately separate from `PendingResolutionBar`:
  a rejected whistleblower value with an *accepted* candidate auto-resolves via Tier 1 completeness
  and never reaches `fact_pending_manual_resolution` at all, so it would never be visible through
  `fetch_pending_resolution_bars` — this is the only way to see it, regardless of resolution
  outcome, since whistleblower rows are never purged. No `role` field (always `WHISTLEBLOWER` by
  construction, unlike `PendingResolutionBar` which covers both sides of a dispute). Re-exported at
  the `quant_data` top level.
- `LoggingSink(Protocol)`: `diagnostic`/`info`/`warning`/`error`/`fatal(message, category="general")`
  plus `perf(description, elapsed_seconds)` — the injectable logging contract (quant-data#20).
  Mirrors `_internal.shared.diagnostics.DiagnosticsLogSink`'s method surface exactly, so a host
  application's own `Logger` (any `tpl-py`-descended repo already has one structurally identical)
  satisfies it with zero changes on the host side. `category` defaults to the literal string
  `"general"` here rather than importing `CATEGORY_GENERAL` from `_internal`, specifically so this
  module stays a dependency-graph leaf (rule 8) — this is the one behavioral `Protocol` in the
  public half of the split (see "Contracts" below for how it differs from `_internal.contracts`'s
  behavioral `Protocol`s). Re-exported at the `quant_data` top level alongside `MarketData`/
  `OHLCV`/`create_postgres_provider`.
  - `create_postgres_provider` and `PostgresDatabase` both take an optional `logger: LoggingSink`
    parameter, defaulting to quant-data's own private `Logger` class itself (not an instance — its
    methods are all `@staticmethod`s, so using the class as the default value behaves identically
    to the direct static calls it replaced internally). `MarketData` also accepts the parameter
    for consistency/discoverability at the layer consumers instantiate directly, though it doesn't
    log anything of its own yet.
  - Additive to all three signatures (new optional kwarg, default preserves today's exact
    behavior) — a host application (e.g. `quant-scratch`) can pass its own `Logger` in and get
    quant-data's internal logging routed into its own log stream, with no shared type/adapter
    needed beyond structurally matching this `Protocol`.

### `quant_data._internal.contracts`

- `MarketDataProvider(Protocol)`: `fetch_bars(ticker, start_date, end_date) -> list[OHLCV]`,
  `fetch_pending_resolution_bars(ticker, start_date, end_date) -> list[PendingResolutionBar]`,
  `fetch_rejected_whistleblower_bars(ticker, start_date, end_date) -> list[RejectedWhistleblowerBar]`,
  plus `close() -> None`, read-only. The read-side contract `MarketData` depends on (not
  `PostgresDatabase` concretely) and that `PostgresDatabase` implements (via
  `create_postgres_provider`) — not something external consumers import directly, since they use
  `MarketData` plus a factory instead. `fetch_pending_resolution_bars` joins
  `fact_pending_manual_resolution` against `staging_market_data_1min` (on ticker/date/time) to
  surface every candidate/whistleblower provider's disputed raw value for a still-pending (bar,
  field group) — `fact_pending_manual_resolution` itself holds no `OHLCV` data, only the key
  marking a (bar, field group) as stuck, so the actual values are read from staging.
  `fetch_rejected_whistleblower_bars` queries `staging_market_data_1min` directly, filtered to
  `role = 'whistleblower'` and `data_quality = 'rejected'` — no join against
  `fact_pending_manual_resolution`, since a rejected whistleblower value with an accepted candidate
  never becomes pending at all.
- `IntraDayProvider(Protocol)`: `connect() -> None`, `fetch_bars(ticker, target_date) ->
  list[OHLCV]` for a single session day, and `close() -> None`. The ingest-side contract for
  external data sources — `ingest` depends on this abstraction, not concretely on whichever
  provider(s) are plugged in. `connect()`/`close()` were added alongside `IBKRIntraDay` (issue
  #21/#22): a no-op for a stateless per-call fetcher like `YahooFinanceIntraDay`, but real
  lifecycle methods for a provider with an expensive connection handshake to amortize across a
  batch — `ingest` calls `connect()` once per provider at the start of a run and `close()` once at
  the end, uniformly across every configured provider.
- `ConnectionTransport(Protocol)`: `open() -> tuple[str, int]` (establish whatever's needed to
  reach Postgres, returning the `(host, port)` to connect to) plus `close() -> None`. Introduced so
  `PostgresDatabase` never needs to know *how* Postgres is actually reached (direct TCP vs. an SSH
  tunnel) — see `postgres.py`/`transports/` below. `create_postgres_provider` and `ingest/cli.py`'s
  database factory both build a concrete transport and hand it to `PostgresDatabase`, rather than
  `PostgresDatabase` depending on either concrete transport itself.

### `quant_data._internal.shared`

- `diagnostics.py`/`errors.py`/`settings.py` — standard `tpl-py` infra, unchanged in behavior from
  earlier layouts this was carried over from.
- `settings.py` additionally defines `PostgresSettings` (`host`, `port`, `user`, `password`,
  `dbname`, plus optional `ssh_user`/`ssh_key_path` — parsed from `sshUser`/`sshKeyPath`, must be
  set together or both omitted) and a `Settings.postgres` field, parsed from a `postgres` object
  under `settings.json`/`settings.local.json`'s `settings` key. `ssh_user`/`ssh_key_path` select an
  `SshTunnelTransport` (see below) instead of the default `DirectTransport` — omitting them
  reproduces the exact behavior from before `ConnectionTransport` existed, so this is additive, not
  breaking, for any existing `settings.json`. The password belongs only in `settings.local.json`
  (gitignored). A `Settings.tickers` field (a plain array of strings, uppercased on load) is
  parsed the same way — a personal watchlist default for `quant-ingest`'s batch mode, so it also
  belongs in `settings.local.json` rather than the committed `settings.json`. `Settings.load(path,
  local_path)`'s `local_path` defaults relative to `path`'s own directory (not the process's cwd),
  so loading a test fixture's `settings.json` never accidentally picks up the real repo-root
  `settings.local.json`. `Settings.catch_up_lookback_days` (`catchUpLookbackDays`, default `7`)
  sizes `quant-ingest --catch-up`'s trailing re-fetch window. `Settings.providers` (`providers`,
  default `["yfinance"]`, lowercased) is the global list `quant-ingest` runs each invocation;
  `Settings.ibkr` (`IbkrSettings`: `host`/`port`/`client_id`, parsed from `ibkr`) only matters
  when `"ibkr"` is configured. `Settings.reconcile` (`ReconcileSettings`: `preferred_provider`
  default `"ibkr"`, `k` default `3.0`, parsed from a `reconcile` object —
  `preferredProvider`/`k`) configures `quant-reconcile`'s tie-break/fallback provider and its
  tolerance multiplier; `k` must be positive.
- `postgres.py` — `PostgresDatabase`: the concrete `MarketDataProvider` implementation, plus
  `write_bars`/`write_staging_bars` (used only by `ingest`) and a set of reconcile-facing
  read/write methods (`fetch_dim_providers`, `fetch_dim_field_groups`,
  `fetch_provider_pair_disagreement`, `fetch_staging_rows_for_reconciliation`,
  `fetch_resolved_field_groups`, `record_reconciliation`, `promote_bar_to_fact`,
  `save_provider_pair_disagreement_batch`, `fetch_pending_manual_resolution_staging_rows`,
  `fetch_pending_manual_resolution_keys`, `mark_pending_manual_resolution`,
  `clear_pending_manual_resolution` — used only by `reconcile`). None of this is part of the
  `MarketDataProvider` Protocol itself — see "Contracts" below on why that's ergonomics, not the
  actual security boundary. One connection-owning class for every internal purpose (reads,
  ingest's writes, reconcile's reads/writes) rather than a second connection-management
  implementation for a third caller. The reconcile-facing methods return plain row dataclasses
  (`ProviderRow`, `FieldGroupRow`, `StagingRow`, `DisagreementStatsRow`) with no reconciliation
  business meaning of their own (candidate/whistleblower, tiers, ...) — that domain model lives in
  `reconcile.algorithm`, and `quant_data._internal` never imports from `reconcile` (rule 8's
  acyclic dependency graph: `reconcile` depends on this module, never the other way around, same
  direction `ingest` already uses). Single
  connection per invocation (no pooling); wraps `psycopg` errors as `AppError`, or
  `DateOutOfRangeError` (a specific `AppError` subclass) when a bar's date falls outside the
  populated `dim_date` range. Takes a `ConnectionTransport` (see `contracts.py` above) instead of
  `host`/`port` directly — it calls `transport.open()` for the effective host/port to connect
  `psycopg` to, and `close()` tears down the connection then the transport. This is what keeps it
  free of any assumption about *how* the host is reached (no SSH-tunnel logic, no hardcoded
  endpoint) — a future move to AWS/Azure/elsewhere, or dropping the SSH tunnel entirely, is a
  settings + `docs/DATABASE.md` change only, never a code change here. Logs an `info`-level
  message before attempting the connection and another once it succeeds, under category
  `postgres` (`CATEGORY_POSTGRES`) — so a hang during connect (e.g. a stalled SSH handshake) is
  distinguishable from one during query execution. Normalizes an effective host of the literal
  string `"localhost"` to `"127.0.0.1"` right before calling `psycopg.connect` — `psycopg`/libpq
  resolves the bare hostname as dual-stack and can fall back from an unreachable IPv6 loopback
  with a ~130s internal timeout instead of connecting immediately, isolated in
  [quant-data#19](https://github.com/croicu/quant-data/issues/19) (a ~660x slowdown vs. the
  literal IPv4 address, on the same tunnel). `SshTunnelTransport` (below) also binds its own local
  end to `127.0.0.1` directly so it never hands back the ambiguous hostname in the first place;
  the normalization here is a second line of defense for any other caller. Also emits
  `Logger.perf()` duration markers (`quant_data._internal.shared.diagnostics`, category `perf`)
  around `transport.open()`, `psycopg.connect()`, and each `fetch_bars`/`write_bars` call — added
  alongside the fix since that's exactly what made the stall diagnosable in the first place
  (traced from the `quant-scratch` consumer side with ad-hoc probe scripts before this existed
  natively).
- `transports/` — `ConnectionTransport` implementations, resolved via
  `transports.resolve_transport(host, port, ssh_user, ssh_key_path)` (picks `SshTunnelTransport`
  when both are set, else `DirectTransport`) — used by both `create_postgres_provider` and
  `ingest/cli.py`'s database factory, the two real call sites needing this wiring.
  - `direct.py` — `DirectTransport(host, port)`: `open()` returns `(host, port)` unchanged,
    `close()` is a no-op. What a cloud-hosted Postgres (or an already-running manual tunnel) uses —
    behaviorally identical to what `PostgresDatabase` did before `ConnectionTransport` existed.
  - `ssh_tunnel.py` — `SshTunnelTransport(host, port, ssh_user, ssh_key_path)`: opens an
    `sshtunnel.SSHTunnelForwarder` on `open()`, binding its local end explicitly to
    `("127.0.0.1", 0)` (an OS-assigned port, not a fixed one — and a concrete address, not
    sshtunnel's own `0.0.0.0` default) and returns `("127.0.0.1", tunnel.local_bind_port)`;
    `close()` stops it. Returning the literal address rather than the bare hostname `"localhost"`
    is a deliberate fix (quant-data#19) — `psycopg`/libpq resolves `"localhost"` as dual-stack and
    can fall back from an unreachable IPv6 loopback with a ~130s internal timeout instead of
    connecting immediately. Key-based auth only, no
    passphrase/agent support. One tunnel per `PostgresDatabase` instance, matching its existing
    single-connection-per-invocation lifecycle. Tunnel-start failures (bad host, bad key, auth,
    network) wrap into `AppError`, mirroring the existing psycopg-error wrapping. What CroicuWS1's
    on-prem hosting uses today — this is what replaced the old requirement of a human-run
    `ssh -N -L ...`/systemd unit before the Python client could connect at all (see
    `docs/DATABASE.md`; direct `psql` access still needs that manual tunnel, since `psql` never
    goes through this transport abstraction).
- `providers/yfinance.py` — `YahooFinanceIntraDay`, an `IntraDayProvider` implementation wrapping
  `yfinance`. Ported from `quant-scratch`'s `shared/providers/yahoo_finance.py`, adapted to
  produce `OHLCV` (which carries its own `ticker`, unlike `quant-scratch`'s `DayBar`) and to set
  `data_quality=DataQuality.INCOMPLETE` (with the value coerced to `0`/`0.0`) whenever `yfinance`
  returns `NaN` for any OHLCV field, or a literal `0` for volume — a real tick can't have zero
  volume, so `yfinance` reporting either NaN or a literal 0 both signal the same underlying problem (most commonly
  pre-market/after-hours minutes with no trades recorded). This is `ingest`'s default provider
  (`settings.providers` defaults to `["yfinance"]`), not the only one anymore — see
  `providers/ibkr.py` below, added to close exactly this pre-/after-market zero-volume gap and
  wired in as an additional configured provider (issue #22); `ingest` depends on
  `IntraDayProvider`, not on `YahooFinanceIntraDay` concretely, so adding/swapping providers is a
  `settings.providers` change, not a code change to `ingest/cli.py`'s logic.
- `providers/yfinance_logging.py` — redirects `yfinance`'s own diagnostics (it logs through the
  stdlib `logging` module, e.g. `logging.getLogger('yfinance')`, rather than raising or going
  through our `Logger`) into `Logger` instead of letting them print straight to stderr.
  `YFinanceLoggingAdapter.classify(message)` matches yfinance's message text against a small,
  explicitly-extensible list of `YFinanceLogRule`s (regex pattern -> `TelemetryLevel`; only one
  rule exists today, `"possibly delisted"` -> `INFO`) and falls back to `WARNING` for anything
  unrecognized. `_YFinanceLogHandler` is a thin `logging.Handler` that forwards each
  `logging.LogRecord`'s message to the adapter — the adapter itself takes its `log` function as a
  constructor parameter (defaulting to `Logger.log`), so classification is unit-testable without
  touching Python's global `logging` state at all. `install_log_capture()` (called once, at
  `providers/yfinance.py` module import time) lowers `logging.getLogger('yfinance')`'s level so no
  message is dropped before reaching the adapter, sets `propagate = False` so nothing double-
  prints via the root logger's `lastResort` handler, and is idempotent (guards against attaching a
  second handler if the module is imported more than once). All of this is additive — it doesn't
  change `_ingest_one`'s own existing fetch/write failure logging (see the `ingest` section below);
  see `tasks/ingest_error_classification.md` for whether the two should eventually be related.
- `providers/ibkr.py` — `IBKRIntraDay`, an `IntraDayProvider` implementation wrapping `ib_async`
  (the maintained fork of the archived `ib_insync`), ported from `quant-scratch`'s
  `shared/providers/ibkr.py` (see croicu/quant-scratch#11/#12). Connects to a local IB Gateway/TWS
  (`127.0.0.1:4002` by default, IB Gateway's paper port) and fetches 1-minute `TRADES` bars
  (`useRTH=False`, i.e. including pre-/after-market) via `reqHistoricalData`, ending each session
  day at 20:00 America/New_York. Unlike `YahooFinanceIntraDay`'s stateless per-call HTTP fetch,
  IBKR's connection handshake is expensive enough to amortize across a batch, so `connect()`/
  `close()` are explicit and separate from `fetch_bars()` — a caller connects once, fetches many
  (ticker, date) pairs, then closes; `fetch_bars()` raises `AppError` if called before `connect()`.
  `connect()` passes `fetchFields=StartupFetchNONE` to skip `ib_async`'s default positions/open-
  and-completed-orders/account-updates fetch, which a Read-Only-API Gateway (the correct setting
  for data-only ingest) otherwise rejects — costing ~10s per connection waiting for that fetch to
  time out. No zero-volume-as-incomplete heuristic (unlike Yahoo's): IBKR only returns bars it
  actually has trade data for, so a zero-volume bar is a real "no trades that minute" fact, not a
  synthesized placeholder — this is exactly the gap `YahooFinanceIntraDay` has that motivated
  adding this provider. Tested via `tests/unit/test_ibkr.py` plus a live
  `tests/integration/test_ibkr.py` probe against a real Gateway (requires one running and
  reachable at the configured host/port — that test fails, not skips, without one, same as any
  other integration test in this repo hitting a real external dependency). **Wired into `ingest`**
  as of issue #22 — add `"ibkr"` to `settings.providers` to have `quant-ingest` run it alongside
  (or instead of) `YahooFinanceIntraDay`; see the `ingest` section below for how `settings.providers`
  and `settings.ibkr` control this. Reconciliation itself (staging -> `fact_market_data_1min`) is
  built as of `quant-reconcile` (see `tasks/quant-reconcile.md`) — see the `reconcile` section
  below.

### `ingest`

`cli.py` — `quant-ingest [--start-date YYYY-MM-DD [--end-date YYYY-MM-DD] | --catch-up | --backfill] [--ticker TICKER]`:
fetches bars over an inclusive date range from every provider named in `settings.providers`
(default `["yfinance"]`; `IntraDayProvider` instances built by `_build_provider`/
`_default_providers`, or injected directly via `main`'s `providers: dict[str, IntraDayProvider]`
parameter) and writes each provider's bars into `staging_market_data_1min` via an injected
`PostgresDatabase` factory (defaults to constructing one from `settings.postgres`). **Writes go to
staging only** — `quant-ingest` never writes `fact_market_data_1min` directly (issue #22); a
separate, not-yet-built `quant-reconcile` tool owns promoting agreeing staging rows into
`fact_market_data_1min` (see `tasks/ibkr-provider-reconciliation.md`; supersedes that file's
earlier "reconciliation folded into `--catch-up`" idea — reconciliation is its own CLI now, not a
mode of `quant-ingest`). Both `providers` and the database factory are constructor-injectable per
this repo's DI-over-monkeypatching convention, so tests substitute fakes (`tests/mocks/yfinance.py`,
`tests/mocks/postgres.py`) instead of patching `ingest`'s own internals.

- `--ticker` is optional — omit it to fetch every ticker in `settings.tickers` instead of one.
- `--end-date` is optional — omit it (with `--start-date` given) for a single day. `--end-date`
  without `--start-date` is rejected. Omitting both falls back to
  `settings.startDate`/`settings.endDate` (same single-day-if-only-startDate-given rule there too).
- `--catch-up` is a third, mutually-exclusive way to pick the date range: instead of an explicit
  range, it re-fetches the trailing `settings.catchUpLookbackDays` days (default 7), excluding
  today (computed via an injectable `today: Callable[[], date]` parameter on `main`, defaulting to
  `date.today`, per this repo's clock-injection convention). Deliberately does no gap
  *detection* — it just re-runs the same per-day fetch+write for the whole window unconditionally.
  Because `write_staging_bars` upserts on `(provider_id, ticker_id, date_id, time_id)`,
  re-ingesting an already-complete day is a harmless no-op; a day a prior run only partially
  ingested (e.g. `quant-ingest` run manually mid-session, or interrupted) gets filled in. This is
  the first concrete job to come out of the scheduled-jobs brainstorm (`tasks/scheduled_jobs.md`,
  issue #3) — deliberately the narrowest slice of it: no jobs table, no in-DB scheduling mechanism,
  just a CLI flag. Actually running it nightly means wiring a cron/systemd timer on whatever host
  runs it, which stays outside this public repo per that brainstorm's design lean (box-specific
  scheduling detail shouldn't leak into committed source).
- Every configured provider is `connect()`ed once at the start of the run (see
  `IntraDayProvider.connect()`/`close()`) and `close()`d in the `finally` alongside the database
  connection — amortized across the whole batch, not reconnected per (ticker, date). A provider
  that fails to `connect()` (e.g. IBKR Gateway not running) is dropped from the run entirely
  (logged as a warning) rather than aborting it; if every configured provider fails to connect,
  that *does* abort the run (`AppError`, no data source left to use).
- One Postgres connection is opened for the whole run and reused across every (ticker, date) pair.
- Fetch and write are attempted independently **per provider, per (ticker, date) pair** — one
  provider failing (bad ticker on that source, no data for that day, e.g. a weekend, a gateway
  hiccup) doesn't stop the others from still writing their own `staging_market_data_1min` rows for
  the same (ticker, date); a (ticker, date) pair only counts as failed if *every* configured
  provider failed it. `_ingest_one` logs the fetch step and the write step separately, and fetch
  failures are tagged by provider (`_FETCH_FAILURE_CATEGORY`: `yfinance` ->
  `quant_data._internal.shared.providers.yfinance.CATEGORY_YFINANCE`, `ibkr` ->
  `quant_data._internal.shared.providers.ibkr.CATEGORY_IBKR`) so a Yahoo vs. IBKR fetch problem
  stays filterable apart from a Postgres write problem — both still count the same toward the exit
  code today (see `tasks/ingest_error_classification.md` for the postponed work on making that
  distinction affect the exit code itself).
- `settings.providers` (`list[str]`, lowercased, default `["yfinance"]`) is a flat global list —
  the same providers run for every ticker in a given invocation; there's no per-ticker provider
  override (see `tasks/ibkr-provider-reconciliation.md`'s now-resolved "currently configured
  providers" question). `_build_provider` raises `AppError` for any name that isn't `"yfinance"` or
  `"ibkr"`. `settings.ibkr` (`IbkrSettings`: `host`/`port`/`client_id`, all defaulting to
  `IBKRIntraDay`'s own module-level defaults) is only consulted when `"ibkr"` is configured.
- **`--backfill`** — a fourth, mutually-exclusive way to pick dates (croicu/quant-data#28), for
  extending existing coverage backward toward `dataset_inception.inception_date` rather than
  fetching a caller-specified range. Per invocation, for each configured ticker: fetch that
  ticker's current earliest covered date (`PostgresDatabase.fetch_earliest_covered_date`, `MIN`
  over `staging_market_data_1min` ∪ `fact_market_data_1min`; `None` for a never-ingested ticker),
  then walk backward one `settings.backfillChunkDays`-sized chunk (default `1`) —
  `end_date = reference_date - 1 day`, `start_date = max(inception_date, end_date - chunkDays +
  1)`, where `reference_date` is the earliest covered date, or `today` if the ticker has never
  been ingested at all (auto-bootstrapping its first chunk from the same "excludes today"
  convention `--catch-up` already uses, via the same injectable `today` callable). A ticker
  already at `inception_date` is a no-op, symmetric to `--catch-up`'s no-op-on-complete-days
  behavior. **Round-robin, not sequential-to-completion**: every configured ticker advances
  exactly one chunk per invocation (never drained to `inception_date` before the next ticker is
  touched), since `earliest_covered` is recomputed fresh each run — coverage depth grows evenly
  across the whole configured universe, tolerating being interrupted or resource-capped at any
  point. No new progress-tracking table — everything above is derived from existing data, same
  idempotent-upsert guarantee `--catch-up` already relies on. `PostgresDatabase.fetch_dataset_inception_date`
  raises `AppError` if `dataset_inception` has no row — that table is schema-only as of migration
  `007`, so `--backfill` cannot run until a real `inception_date` value is inserted by hand.
- **Internal rate limiting** — `ingest.rate_limiter.RateLimiter` (sliding window: at most
  `requests_per_window` calls per rolling `window_seconds`, `collections.deque` of recent call
  timestamps, constructor-injected `clock`/`sleep` per this repo's DI-over-monkeypatching
  convention) sits between `_ingest_one`'s per-provider loop and `IntraDayProvider.fetch_bars` —
  not inside any provider implementation, since pacing is a property of reaching a specific
  external service. Config shape: `settings.<provider>.rateLimit: {requestsPerWindow,
  windowSeconds}`; a provider with no configured limit gets no `RateLimiter` entry at all
  (`_default_rate_limiters`) and is never throttled. `IbkrSettings.rate_limit` defaults to `50`
  requests / `600` seconds even when `settings.ibkr.rateLimit` is entirely absent from
  `settings.json` — deliberately under IBKR's documented `60`/`600` ceiling for margin, and
  deliberately *not* "unspecified means unlimited" like every other provider, since IBKR has a
  known real ceiling that should always apply. `YfinanceSettings.rate_limit` defaults to `None`
  (unlimited), matching yfinance's current unbounded behavior.

### `reconcile`

`cli.py` — `quant-reconcile [--finalize] [--debug]`: reads `staging_market_data_1min`, resolves
what it can automatically, and promotes fully-resolved bars into `fact_market_data_1min`, purging
their staging rows once it's safe to (see "lazy purge" below). See `tasks/quant-reconcile.md` for
the full design (field consistency groups, the candidate/whistleblower model, the tier algorithm,
`--finalize`, manual correction, and the `fact_pending_manual_resolution` queue added
2026-08-03). No importable surface, console script only, same shape as `ingest`.

- **`algorithm.py`** — pure functions, no database access, directly unit-testable
  (`tests/unit/test_reconcile_algorithm.py`). `ProviderBar` (one provider's reported values for a
  bar, plus its `role`), `Resolution` (winning `provider_id` + `resolution_path`),
  `DisagreementStats` (Welford's-algorithm accumulator) are the module's own domain types —
  deliberately not shared with `quant_data._internal.shared.postgres`, which stays unaware of any
  reconciliation concept (rule 8's acyclic dependency graph).
  - `resolve_automatic(bars, field_group, windows, tolerances, k, preferred_provider_id)` — Tiers
    1-3 (completeness / agreement / boundary-fix), first one that resolves a group wins. Returns
    `None` if still stuck (Tier 4) — left in staging for a person to look at. `tolerances` is
    `dict[int, dict[str, float]]` (`provider_id -> field_name -> stddev`) since
    croicu/quant-data#28 — every field in the group (`open`/`high`/`low`/`close`) is checked
    independently against its own learned tolerance (`_agrees_within_tolerance`/`_windowed_agrees`),
    not "max diff across the group within one pooled tolerance" as before. A candidate missing
    tolerance data for even one field fails Tier 2/3 entirely for that field group (fails closed,
    not silently skipped) — OHLC still stays one atomic *promotion* unit; only the comparison is
    now per-field. `fields_for_group(field_group)` (public — `cli.py` needs it directly for the
    stats fan-out below and for the graduation batch) is the one place the group→fields mapping
    lives.
  - `resolve_finalize(bars, preferred_provider_id)` — `--finalize`'s fallback: promotes
    `preferredProvider`'s raw value outright, no tolerance check.
  - `welford_update`/`stddev_from_stats`/`relative_diffs_for_stats_update` — the running-variance
    machinery behind `provider_pair_disagreement`'s tolerance; population variance (`M2 / n`).
    Only called for `RESOLUTION_AGREEMENT` resolutions (see "Design decisions" in the task file for
    why) — `cli.py` fans `relative_diffs_for_stats_update`'s one-diff-per-field result out into 4
    independent per-`(provider_id, ticker_id, field_id)` Welford buckets instead of one shared
    per-group bucket, matching `provider_pair_disagreement`'s migration-`007` key. `batch_stats`
    (new) folds `welford_update` over a whole list of observations in one call — used only by the
    graduation batch below, mathematically identical to the same observations arriving one at a
    time (order-independent).
  - `GRADUATION_THRESHOLD_MATCHED_BARS` (`1400`) — see the per-ticker graduation bullet below.
- **`cli.py`'s `run_reconciliation(database, settings, finalize)`** dispatches to one of two
  functions — they no longer share one code path, since a plain run and `--finalize` now operate
  on disjoint fetch scopes:
  - **`_run_automatic_pass`** (plain, `finalize=False`) — fetches every dimension/stats table plus
    every staging row belonging to a bar where every **candidate** provider in `settings.providers`
    has reported *and* the bar has no `fact_pending_manual_resolution` row (bars missing an
    expected candidate, or already flagged pending, are skipped entirely, untouched) — since
    croicu/quant-data#31, the whistleblower is no longer required at fetch time (see the
    whistleblower-absence bullet below for what happens next), groups rows by bar, and for each
    not-yet-resolved `(bar, field_group)` calls `resolve_automatic` only — no `resolve_finalize`
    fallback in this path anymore. Whatever's still unresolved once the fixed-point loop below
    converges gets a `fact_pending_manual_resolution` row inserted (a new write; previously nothing
    was recorded for a stuck bar at all) so every future plain run skips it.
  - **Outlier-detection pass** (croicu/quant-data#32, `outlier_detection.py`) — runs first, before
    any Tier 1-4 attempt. Pure logic (`is_bar_rejected`, directly unit-testable, no database
    access — same split as `algorithm.py`) lives in its own module rather than `algorithm.py`
    itself, since it's a genuinely different concept (intra-provider plausibility, never comparing
    against another provider) from the rest of reconciliation. `_run_outlier_detection_pass` in
    `cli.py` is the orchestration: bulk-fetches every `data_quality='accepted'` whistleblower
    staging row (`fetch_whistleblower_accepted_staging_rows`) and every tuned
    `data_quality_thresholds` override, groups by `(provider_id, ticker_id, date_id)`, then by
    session segment (premarket/regular/afterhours, `_session_segment`, ET-converted from the
    naive-UTC `timestamp` column) within each day.
    - **MAD reference window: `± BACKGROUND_HALF_WINDOW_MINUTES` (20 minutes), the target's own
      diffs excluded from the reference sample.** An earlier `± 2`-minute design (2026-08-06)
      included the target's own back/forward diffs in the very MAD sample used to judge them —
      self-contaminating in both directions: a genuinely large target move inflated its own
      reference scale (masking real spikes), and two coincidentally similar background diffs
      elsewhere could collapse the scale to near-zero (flagging ordinary noise). Real-data
      recalibration (2026-08-07) against the confirmed DataBento-verified SPY cases fixed both by
      widening the window and excluding the two target-touching diffs from the background sample
      — see `tasks/yahoo_data_sanitization.md`'s recalibration section for the before/after numbers.
    - **Session-boundary tail handling.** The last/first `BACKGROUND_HALF_WINDOW_MINUTES` of each
      segment don't get their own freshly-recentered window (which would either shrink as the edge
      approaches or need to reach past the boundary, comparing across a real regime change). Each
      segment's tail instead reuses one shared "frozen" window anchored at the last position where
      a full `± BACKGROUND_HALF_WINDOW_MINUTES` window still fits entirely inside that segment
      (`_build_outlier_window`, called once per segment edge rather than once per bar). The literal
      first/last bar of a segment still only has one usable immediate neighbor — its other side
      belongs to a different segment by construction — which `is_bar_rejected` handles with a
      one-sided check against a separate, real-data-calibrated `k_boundary_oc`/`k_boundary_hl`
      threshold instead of skipping the bar outright (fixed 2026-08-07 after a confirmed bad SPY
      16:00 ET tick was found un-evaluable under the two-sided-only design — see the task file).
    - A field is flagged if its immediate backward/forward diff (or, at a one-sided boundary bar,
      the single available diff) is large relative to the background MAD scale — a tight
      coefficient when both diffs oppose (a reversal/spike shape), a loose one when they agree (a
      persisting trend), or the dedicated boundary coefficient when only one side exists. A bar is
      rejected if any of its four fields is flagged. Whatever's flagged gets
      `mark_staging_bars_rejected` — one commit for the whole run's rejections regardless of count
      (same batching lesson as croicu/quant-data#30). Sweeps the *entire* staging table every run,
      not just new data, since this is the only mechanism that can ever touch bars already stuck
      before the check existed — ingest-time placement was considered and rejected for exactly
      that reason (see `tasks/yahoo_data_sanitization.md`). Running it first within the same pass
      matters: `rejected` is treated identically to `incomplete` by `_resolve_completeness`, so a
      bar newly rejected this run can still auto-promote its candidate in the very same invocation
      instead of waiting for the next one.
  - **Whistleblower-absence handling** (croicu/quant-data#31) — a candidate-only bar_key (the
    whistleblower never wrote a row for that minute at all) is only genuinely ready for evaluation
    once `ingestion_coverage` confirms the whistleblower's date range was actually ingested; if not
    covered yet, that bar_key is filtered back out of `bars` immediately after fetch, before
    graduation counting or the fixed-point loop ever see it — behaving exactly like a bar missing
    any other required provider (not evaluated, and critically **not** marked pending, since "not
    ingested yet" isn't evidence of anything). For a covered-but-absent bar_key, a placeholder
    `ProviderBar` (`data_quality="incomplete"`, dummy zero field values, `role=WHISTLEBLOWER`) is
    synthesized when building `provider_bars` for that bar — `_resolve_completeness` already treats
    a non-`"accepted"` bar as "reported but unusable," so this reuses that exact path with **zero
    changes to `algorithm.py`**: a confirmed-absent whistleblower behaves identically to one that
    reported and was flagged `incomplete`, including the resolution_path (`'completeness'`, not a
    new label). The dummy values are never actually compared against — Tier 2/3 measure tolerance using
    the *candidate's* own reference value, never the whistleblower's, and both fail closed
    (correctly) if ever reached with placeholder zeros instead of Tier 1 catching it first. Live
    motivating case: 6,939 of 7,192 `ibkr` rows stuck in staging on CroicuWS1 were exactly this —
    `yfinance`'s day was successfully ingested but had no row for that specific minute.
  - **Per-ticker graduation gate** (croicu/quant-data#28) — before any Tier 1-4 attempt, a ticker
    must have accumulated `GRADUATION_THRESHOLD_MATCHED_BARS` (`1400`) *matched* bars (every
    configured provider reported real, non-incomplete data for that minute — same definition
    already used for Tier 1/2 eligibility). "Graduated" is derived, not stored separately: a ticker
    is graduated iff `provider_pair_disagreement` already has at least one row for it, since those
    rows are created only by the one-time graduation batch or by a later Tier-2 update, never
    before. Below the threshold, a ticker's bars sit in staging completely unevaluated (no partial
    stats update either — nothing to protect a partial estimate from outlier contamination, since
    nothing is computed until the full batch is in). At the threshold, `relative_diffs_for_stats_update`
    is called unconditionally (the one deliberate exception to "only Tier 2 observations update
    stats" — there's no tolerance yet to gate on) over every matched bar, `batch_stats` computes
    each field's mean/stddev in one pass, and the ticker's *entire currently-fetched staging
    backlog* (matched and unmatched together, not just the graduation batch) is evaluated through
    the standard Tier 1-4 stack in the very same run — required so Tier 1 completeness (which only
    ever fires on unmatched bars) isn't stranded at exactly the moment a tolerance becomes
    available. `_run_finalize_pass` needs no equivalent logic: an ungraduated ticker's bars are
    never Tier-1-4-attempted, so they never reach `fact_pending_manual_resolution` in the first
    place.
  - **`_run_finalize_pass`** (`--finalize`) — fetches only staging rows for bars that already have
    a `fact_pending_manual_resolution` row, and only the specific `(bar, field_group)` keys that
    are actually pending (`fetch_pending_manual_resolution_keys`). Calls `resolve_finalize`
    directly for each — no Tier 1-3 attempt, those already failed during a prior automatic pass —
    and deletes the `fact_pending_manual_resolution` row once resolved
    (`clear_pending_manual_resolution`). Does **not** also evaluate not-yet-attempted bars; a
    `--finalize` run with nothing pending yet has nothing to do. Known, accepted tradeoff: a bar
    that's already resolved but purge-blocked by a *different*, still-pending neighbor doesn't get
    its own purge-eligibility re-checked during a `--finalize` run (that neighbor isn't fetched in
    this narrower scope) — it self-heals on the next plain run, which re-fetches it (already
    resolved, so no Tier 1-3 re-attempt) and re-checks the now-unblocked neighbor.
  - Both share `_promote_and_lazily_purge` (promotion + lazy-purge, see below) and the same
    one-bulk-fetch-per-table philosophy — no round trip per bar for reads. Tier 3's neighbor-minute
    lookups (automatic pass only; `resolve_finalize` doesn't use windows at all) are served from
    the same in-memory staging data via each row's own `timestamp` column, no extra queries.
    Writes (record a resolution, update disagreement stats, mark/clear pending, promote a
    fully-resolved bar, purge its staging rows) are still one round trip each, same performance
    profile as `write_bars`/`write_staging_bars` (see issue #23) — deliberately not optimized
    further here, since the read side was the actual N-per-bar problem this design avoids, and the
    pending-queue mechanism above is what stops that read cost from compounding indefinitely for
    bars that never resolve.
- **Fixed-point convergence within a single automatic pass.** A bar visited early in a pass over
  `bars` can fail Tier 2 using disagreement stats that a later bar's own agreement resolution
  improves within that same pass — a plain single pass doesn't necessarily reach the real
  convergence floor. Rather than requiring a person to re-invoke `quant-reconcile` several times by
  hand (the live-tested finding in `tasks/quant_reconcile.md`: 85 → 68 → 63 → 61 → 0 stuck groups
  across four manual re-runs), `_run_automatic_pass` repeats full passes over `bars` until one
  resolves nothing new. Provably terminates: each pass either resolves at least one more group
  (bounded by the finite total group count) or resolves zero, at which point `stats_by_key` can no
  longer change and further passes would be identical. `_run_finalize_pass` has no equivalent loop
  — `resolve_finalize` doesn't depend on stats, so it either succeeds or fails deterministically in
  one attempt.
- A bar promotes to `fact_market_data_1min` once **every** field group has a resolution (checked
  after the convergence loop above, for every bar whether resolved just now or in an earlier run).
  Today that's just `'ohlc'` — `volume` is no longer its own field group (see
  `tasks/volume_reconciliation.md`); the promoted row's `volume` and `data_quality` come straight
  off the `'ohlc'` winner's own `StagingRow`, not a separately resolved value.
- **Lazy purge.** `promote_bar_to_fact` (upsert into `fact_market_data_1min`) and
  `purge_staging_bar` (delete that bar's staging rows) are separate calls, not one atomic step.
  After promoting, a bar's staging rows are purged only if neither adjacent minute (`t-1`/`t+1`,
  same ticker) is still present in staging and unresolved — `_bar_still_needed_as_neighbor` checks
  this via a `(ticker_id, timestamp) -> bar_key` index built alongside the staging fetch. Purging
  immediately (the original design) permanently lost a promoted bar's raw data for any future run's
  Tier-3 windowed-average check on its still-stuck neighbor — a real gap found during the
  2026-08-03 live test (`tasks/quant_reconcile.md`). A neighbor that's absent (never existed, or
  already purged) or already fully resolved will never call the windowed check again, so it doesn't
  block purging. **Whistleblower-role providers' rows (`yfinance` today) are permanently exempt**
  (croicu/quant-data#28) — `purge_staging_bar` now excludes `dim_provider.role = 'whistleblower'`
  rows from the delete entirely, reusing the same `role` mechanism that distinguishes candidates
  from the whistleblower everywhere else, not a hardcoded provider name. Rationale is
  irreplaceability, not just audit trail: Yahoo's historical access policy is external and not
  guaranteed, so whatever's already been fetched may be the only copy that will ever exist.
  Accepted tradeoff: unbounded storage growth for a provider whose individual bars are never
  promoted. Once a bar's candidate rows are gone, its orphaned whistleblower row becomes
  permanently inert for reconcile's purposes — `fetch_staging_rows_for_reconciliation`'s
  every-configured-provider-reported check can never be satisfied again for that bar_key, so it
  never resurfaces, needing no compensating logic.
- `settings.reconcile.preferredProvider` (default `"ibkr"`) and `settings.reconcile.k` (default
  `3.0`, must be positive) are the only two tunables — see `ReconcileSettings` above.

### `quant_data.client`

- `market_data.py` — `MarketData`: a thin, read-only wrapper around a `MarketDataProvider`.
  Agnostic of the concrete backend by construction — it only knows the protocol, not
  `PostgresDatabase` or any other concrete implementation, so swapping backends (a cloud store,
  something else entirely) never requires changing `MarketData` itself, only writing a new
  factory. Deliberately doesn't expose `write_bars` at all — for the Postgres backend specifically,
  the real enforcement of "no client can write" is the `quant_reader` role's DB-level privileges,
  not this class's shape, but a narrower surface is still better ergonomics regardless of backend.
  `fetch_pending_resolution_bars(ticker, start_date, end_date)` delegates straight to the provider,
  same shape as `fetch_bars` — the first public method exposing anything from the reconciliation
  domain (see "Contracts" below). `fetch_rejected_whistleblower_bars(ticker, start_date, end_date)`
  is the same shape again, surfacing whistleblower values a per-provider plausibility check flagged
  implausible (`data_quality=REJECTED`) — distinct from `fetch_pending_resolution_bars` since a
  rejected value with an accepted candidate resolves automatically and never becomes pending.
  Re-exported at `quant_data` top level (`from quant_data import MarketData`).
- `postgres_provider.py` — `create_postgres_provider(host, port, dbname, user="quant_reader",
  password="", ssh_user=None, ssh_key_path=None)`: today's factory, resolves a
  `ConnectionTransport` from the `ssh_user`/`ssh_key_path` kwargs (`transports.resolve_transport`)
  and builds a `PostgresDatabase` (connecting as `quant_reader` by default) with it, returned as a
  `MarketDataProvider`. Also re-exported at `quant_data` top level. Omitting `ssh_user`/
  `ssh_key_path` reproduces the exact pre-`ConnectionTransport` behavior (a direct connect to
  `host:port`), so this is additive to the function's signature, not breaking. A future backend
  (e.g. a cloud store) gets its own sibling factory here rather than a change to `MarketData` or to
  this one function's signature.

## Data flow

A caller supplies `ticker` + a date range → `MarketDataProvider.fetch_bars` joins
`fact_market_data_1min` against `dim_ticker`/`dim_date` for that ticker/range → returns `OHLCV`
rows ordered by timestamp.

On the write side, two stages now (as of issue #22): for each (ticker, date) pair in the requested
range/batch, `ingest` fetches that ticker/day of bars from every configured `IntraDayProvider` →
`PostgresDatabase.write_staging_bars` upserts the ticker into `dim_ticker` (insert-or-fetch, single
round trip via `ON CONFLICT ... RETURNING`), resolves `date_id`/`time_id` from the pre-populated
`dim_date`/`dim_time` dimensions, then upserts into `staging_market_data_1min` (idempotent re-run
via `ON CONFLICT (provider_id, ticker_id, date_id, time_id) DO UPDATE`) inside one transaction —
any failure within that pair rolls back just that pair's writes, logs a warning, and the run
continues rather than aborting entirely. `reconcile` then reads `staging_market_data_1min`,
resolves what it can (`fact_reconciliation`/`fact_reconciliation_participant`/
`provider_pair_disagreement` record how), and upserts fully-resolved bars into
`fact_market_data_1min` — the only path anything reaches the fact table today (`write_bars` still
exists and is tested but nothing calls it). `ingest`/`reconcile` are the only writers; consumer
repos (`quant-scratch` and future others) only ever read, via `MarketData`/`quant_reader` —
a `SELECT`-only, trust-authenticated role (no DB password; the SSH tunnel/key is the actual gate)
with no write grant at all, enforced at the database-privilege level, not just by the Python
interface (verified directly: a write attempt through `quant_reader` gets a real Postgres
`permission denied`, not just a missing method).

## Contracts

`quant_data.protocols`'s `OHLCV` and `LoggingSink`, and `quant_data._internal.contracts`'s
`MarketDataProvider`/`IntraDayProvider`/`ConnectionTransport`, are the actual Python-level
contracts now (see "Modules" above for their shapes). The split between the two isn't data-vs-
behavior — `LoggingSink` is behavioral too — it's public-vs-private: `protocols.py` holds
`Protocol`s a consumer is meant to actually implement/inject (`LoggingSink`), while
`_internal.contracts` holds `Protocol`s that only wire quant_data's own internals together
(`MarketDataProvider`, `IntraDayProvider`, `ConnectionTransport`) and are never imported by
external consumers. The database schema itself (five dimension tables, `fact_market_data_1min`,
`staging_market_data_1min`, and `reconcile`'s own `fact_reconciliation`/
`fact_reconciliation_participant`/`provider_pair_disagreement`) remains the underlying persisted
contract — see `docs/SCHEMA.md`. `fact_market_data_1min` (via `MarketData.fetch_bars`), as of
`MarketData.fetch_pending_resolution_bars`, `fact_pending_manual_resolution` joined against
`staging_market_data_1min`, and as of `MarketData.fetch_rejected_whistleblower_bars`,
`staging_market_data_1min` directly (filtered on `role`/`data_quality`) are the actual external
contract now — everything else (`fact_reconciliation`, `fact_reconciliation_participant`,
`provider_pair_disagreement`, `dim_provider.role`) is still this repo's own internal write-path
plumbing, never queried directly by `quant-scratch` or any other consumer. Exposing
pending-resolution data required a `quant_reader` grant on `staging_market_data_1min`/
`fact_pending_manual_resolution`/`dim_field_group`/`dim_provider` — see `docs/DATABASE.md`'s
"Granting quant_reader access to new tables". `fetch_rejected_whistleblower_bars` needed no new
grant, since it only reads tables `quant_reader` already had access to from that earlier rollout.
