# ARCHITECTURE.md

Modules, data flow, and contracts for `quant-data`.

## Layout

Mirrors `quant-scratch`'s convention: one folder per app under `src/`, plus two repo-wide
non-app packages shared across all of them.

- `src/defs/` — `contracts.py` (behavioral `Protocol`s) + `protocols.py` (pure-data contracts).
  Kept separate from `shared/` deliberately: they're the specification, not owned by whichever
  package happens to implement them.
- `src/shared/` — cross-app infra: `diagnostics.py` (`Logger`), `errors.py` (`AppError`/
  `TaskError`), `settings.py` (`Settings`).
- `src/ingest/` — the (currently placeholder) ingest CLI. Console script: `quant-ingest`.

There is no top-level `quant_data` package and no generic `quant-data` console script — same as
`quant-scratch` has no generic `quant-scratch` script, only per-app ones.

## Modules

### `src/defs/`, `src/shared/` — placeholder Python scaffold

No real logic yet — `defs/protocols.py` and `defs/contracts.py` are empty placeholders;
`shared/diagnostics.py`/`errors.py`/`settings.py` are the standard `tpl-py` infra, unchanged in
behavior from the single-package layout this was split out of. The actual data layer right now is
the Postgres schema itself (see `docs/SCHEMA.md`), not Python code.

### `src/ingest/` — placeholder app

`cli.py` does nothing beyond logging start/end (console script `quant-ingest`). The follow-up task
(`tasks/postgres_client_and_dimensions.md`) will add real content here plus a new read-side module:

- `defs/contracts.py` — `MarketDataProvider(Protocol)`: `fetch_bars(ticker, start_date, end_date) -> list[OHLCV]`, read-only
- `defs/protocols.py` — an `OHLCV` dataclass (ticker, date, time, open, high, low, close, volume)
- `shared/postgres.py` (or similar) — `PostgresDatabase`, the concrete `MarketDataProvider`
  implementation, wrapping a single connection per invocation (no pooling yet), translating DB
  errors to `AppError`. Endpoint/transport-agnostic by construction — takes host/port/credentials
  purely from settings, never embeds assumptions about the current SSH-tunnel-to-an-Ubuntu-box
  setup, so a future move to AWS/Azure/elsewhere is a settings + `docs/DATABASE.md` change only,
  never a code change here.
- `src/ingest/` — the real ingest pipeline (pulling from IBKR — see `quant-scratch`'s
  `tasks/ibkr_tws_extended_hours.md`), the only thing with write access to the database (see
  "single-writer, many-reader" in `tasks/postgres_client_and_dimensions.md`).

## Data flow

<!-- How data enters, gets transformed, and leaves the system. -->

Not yet implemented. Planned shape (see `tasks/postgres_client_and_dimensions.md`): a caller
supplies `ticker` + a date range → `MarketDataProvider.fetch_bars` resolves ticker/date/time
dimension IDs → joins `fact_market_data_1min` → returns `OHLCV` rows. On the write side, `ingest`
pulls bars from IBKR and is the sole writer into `fact_market_data_1min`; consumer repos
(`quant-scratch` and future others) only ever read, via a `quant_reader` DB role with no write
grant — enforced at the database-privilege level, not just by the Python interface.

## Contracts

<!-- protocols.py: persisted/shared data contracts (pure data).
     contracts.py: runtime behavioral interfaces (Protocol classes). -->

Both `defs/protocols.py` and `defs/contracts.py` are currently empty. The database schema itself
(four tables: `dim_ticker`, `dim_date`, `dim_time`, `fact_market_data_1min`) is this repo's actual
data contract for now — see `docs/SCHEMA.md`.
