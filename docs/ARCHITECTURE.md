# ARCHITECTURE.md

Modules, data flow, and contracts for `quant-data`.

## Modules

<!-- One entry per module under src/quant_data/: what it owns, what it depends on. -->

### `quant_data` — placeholder Python scaffold

No real modules yet — `protocols.py` and `contracts.py` are empty placeholders, `cli.py` does
nothing beyond logging start/end, following the standard `tpl-py` shape. The actual data layer
right now is the Postgres schema itself (see `docs/SCHEMA.md`), not Python code.

The follow-up task (`tasks/postgres_client_and_dimensions.md`) will add:

- `contracts.py` — `MarketDataProvider(Protocol)`: `fetch_bars(ticker, start_date, end_date) -> list[OHLCV]`, read-only
- `protocols.py` — an `OHLCV` dataclass (ticker, date, time, open, high, low, close, volume)
- `postgres.py` — `PostgresDatabase`, the concrete `MarketDataProvider` implementation, wrapping a
  single connection per invocation (no pooling yet), translating DB errors to `AppError`.
  Endpoint/transport-agnostic by construction — takes host/port/credentials purely from settings,
  never embeds assumptions about the current SSH-tunnel-to-an-Ubuntu-box setup, so a future move
  to AWS/Azure/elsewhere is a settings + `docs/DATABASE.md` change only, never a code change here.

## Data flow

<!-- How data enters, gets transformed, and leaves the system. -->

Not yet implemented. Planned shape (see `tasks/postgres_client_and_dimensions.md`): a caller
supplies `ticker` + a date range → `MarketDataProvider.fetch_bars` resolves ticker/date/time
dimension IDs → joins `fact_market_data_1min` → returns `OHLCV` rows. No write path exists yet;
this repo currently has no ingest tool of its own (ingest may live here or in a separate repo —
undecided, see `tasks/postgres_client_and_dimensions.md`'s open questions).

## Contracts

<!-- protocols.py: persisted/shared data contracts (pure data).
     contracts.py: runtime behavioral interfaces (Protocol classes). -->

Both `protocols.py` and `contracts.py` are currently empty. The database schema itself (four
tables: `dim_ticker`, `dim_date`, `dim_time`, `fact_market_data_1min`) is this repo's actual data
contract for now — see `docs/SCHEMA.md`.
