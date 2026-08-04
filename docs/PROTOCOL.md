# PROTOCOL.md

CLI signature and file format schemas for `quant-data`.

## CLI

<!-- Command name, arguments, flags, exit codes. -->

### `quant-ingest`

- Usage: `quant-ingest [--start-date YYYY-MM-DD [--end-date YYYY-MM-DD] | --catch-up] [--ticker TICKER] [--debug]`
- Fetches 1-minute OHLCV bars from every provider in `settings.providers` (default: just
  `yfinance`; add `ibkr` to also run `IBKRIntraDay`) and writes each provider's bars independently
  into `staging_market_data_1min` — **not** `fact_market_data_1min` directly. Promoting agreeing
  staging rows into `fact_market_data_1min` is a separate tool, `quant-reconcile` (see below and
  `tasks/quant-reconcile.md`); `quant-ingest`'s job ends at staging. See `docs/ARCHITECTURE.md` for
  the full design.
- `--ticker` — single ticker (e.g. `AAPL`); omit to use every ticker in `settings.tickers` instead.
- `--start-date` — first trading date, `YYYY-MM-DD`; omit to use `settings.startDate`.
- `--end-date` — last trading date (inclusive); omit (with `--start-date` given) for a single day.
  Requires `--start-date` — rejected on its own.
- `--catch-up` — re-fetches the trailing `settings.catchUpLookbackDays` days (default 7),
  excluding today, instead of a `--start-date`/`--end-date` range. Rejected in combination with
  `--start-date`/`--end-date`. Meant for an unattended nightly run (cron/systemd timer, set up
  outside this repo — see `tasks/scheduled_jobs.md`) that catches up any day a prior run only
  partially ingested (e.g. `quant-ingest` run manually mid-session); safe to run against
  already-complete days too, since `write_staging_bars` upserts are idempotent.
- `--debug` overrides `settings.json`'s `debug` flag; also re-raises the underlying exception
  instead of printing a one-line error, for upfront failures (settings load, no ticker/date
  configured at all, every configured provider failing to connect).
- `settings.providers` (array of strings, default `["yfinance"]`) — which providers to run each
  invocation; unrecognized names fail fast at startup. `settings.ibkr` (`host`/`port`/`clientId`,
  all optional — default to `IBKRIntraDay`'s own defaults, IB Gateway's paper port `4002`) — only
  consulted when `"ibkr"` is in `settings.providers`.
- Exit codes: `0` every (ticker, date) pair had at least one provider succeed; `1` settings load
  failure, no ticker/date-range configured at all, every configured provider failing to connect, or
  one or more (ticker, date) pairs where every provider failed (an individual provider failing for
  one pair — bad ticker on that source, gateway unreachable — logs a warning and the run continues
  with whatever providers/pairs still work, rather than aborting — `1` here can mean "partial
  success", not necessarily "nothing happened"); `2` argument parsing error (argparse's default
  behavior on missing/bad args, e.g. malformed dates or `--end-date` without `--start-date`).

### `quant-reconcile`

- Usage: `quant-reconcile [--finalize] [--debug]`
- Reads `staging_market_data_1min`, resolves each not-yet-resolved (bar, field group) — today just
  `ohlc`, since `volume` no longer has its own field group and simply rides along with whichever
  provider wins `ohlc` (see `tasks/volume_reconciliation.md`) — against the providers that reported
  for it, and promotes a bar into `fact_market_data_1min` once every field group has resolved.
  Staging rows purge once that's safe — deferred while an adjacent minute is still unresolved, so a
  future run's boundary-fix check doesn't lose that neighbor's raw data.
- No date-range/ticker flags — processes everything currently eligible in staging each run, unlike
  `quant-ingest`.
- **Without `--finalize`** (the plain, day-to-day invocation): only the automatic tiers run
  (completeness / raw agreement / boundary-misalignment), and only against bars with no
  `fact_pending_manual_resolution` row — anything already flagged pending from an earlier run is
  skipped entirely, not re-evaluated. Repeats internally until a pass resolves nothing new, so a
  single invocation reaches the real convergence floor rather than needing several manual re-runs
  (`tasks/quant_reconcile.md`'s seeding-lag fix). A (bar, group) that exhausts all three tiers gets
  a `fact_pending_manual_resolution` row inserted — an expected steady state, not a failure, and the
  deliberate hand-off point to `--finalize`/manual correction. This is what makes a realistic
  cadence (e.g. plain `quant-reconcile` run daily) cheap even with a growing backlog of genuinely
  unresolved bars: each plain run only ever touches what's new since the last one.
- **`--finalize`**: force-resolves *only* what's currently in `fact_pending_manual_resolution`,
  using `settings.reconcile.preferredProvider`'s raw value (`resolution_path = 'finalized'`) — no
  automatic-tier re-attempt, those already failed. It does **not** also evaluate not-yet-attempted
  bars; a `--finalize` run with nothing pending yet has nothing to do. Meant to be run separately
  from the plain cadence (e.g. weekly, after a person has had a chance to look at what's
  accumulated and optionally retune `settings.reconcile`) — run plain `quant-reconcile` first if you
  want it to also pick up anything from today that hasn't been attempted yet.
- `--debug` overrides `settings.json`'s `debug` flag; also re-raises the underlying exception
  instead of printing a one-line error.
- `settings.reconcile.preferredProvider` (default `"ibkr"`) — which candidate provider wins
  `--finalize`'s fallback and any Tier 2 tie-break among multiple agreeing candidates; only ever a
  provider with `dim_provider.role = 'candidate'`, never the whistleblower.
  `settings.reconcile.k` (default `3.0`, must be positive) — the tolerance multiplier
  (`tolerance = k * stddev * reference_value`) applied against `provider_pair_disagreement`'s
  measured variance.
- Exit codes: `0` the run completed (regardless of how many groups ended up stuck — that's a
  normal outcome, not a failure); `1` settings load failure, `settings.postgres` not configured;
  `2` argument parsing error.
- A whistleblower provider's value (`yfinance` today) only ever reaches `fact_market_data_1min`
  through manual correction — directly hand-editing `staging_market_data_1min`/
  `fact_market_data_1min`, no dedicated tooling — never through `--finalize`'s algorithm. A person
  doing this should also delete the corresponding `fact_pending_manual_resolution` row (if one
  exists) as part of the same manual edit — nothing else does this automatically for a hand
  correction the way `--finalize` does for its own resolutions.

There is no generic `quant-data` command — `quant-ingest`/`quant-reconcile` (write side, packages
`ingest`/`reconcile`, outside the `quant_data` namespace — no importable surface, console script
only) and `quant_data.MarketData` (read side — a library, not a CLI) are the consumer-facing entry
points. `MarketData`,
`OHLCV`, `LoggingSink`, and `create_postgres_provider` are re-exported at the `quant_data` top level
(`from quant_data import MarketData, OHLCV, LoggingSink, create_postgres_provider`);
`quant_data._internal.*` is private (nested, not a separate package) and should not be imported
directly by external consumers. `LoggingSink` is the injectable logging contract — pass an
optional `logger=` matching its shape to `create_postgres_provider`/`MarketData` to route
quant-data's internal logging into your own log stream (see `docs/ARCHITECTURE.md`).

## File formats

<!-- Schemas for any files this project reads or writes. -->

This repo's primary "file format" is the database schema itself — see `docs/SCHEMA.md` for the
four-table star schema (`dim_ticker`, `dim_date`, `dim_time`, `fact_market_data_1min`) and
`migrations/001_init_schema.sql` for the exact DDL.

### Migration files (`migrations/*.sql`)

Plain numbered SQL files (`NNN_description.sql`), applied manually via `psql` in order — see
`docs/DATABASE.md`. Each migration wraps its DDL in a single transaction and records itself in the
`schema_migrations` table on success.
