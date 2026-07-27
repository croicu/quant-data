# quant-data

PostgreSQL warehouse for market data — 1-minute OHLCV bars by ticker, date, and time.

A four-table star schema (`dim_ticker`, `dim_date`, `dim_time`, `fact_market_data_1min`) hosted on
a remote Postgres instance, built to be the centralized data source for
[`quant-scratch`](https://github.com/croicu/quant-scratch)'s experiments and CLI tools, and for
`quant-research`'s published analysis — so historical bars get fetched and stored once, not
re-pulled by every experiment from its own live data provider.

See [`docs/SCHEMA.md`](docs/SCHEMA.md) for the table design, [`docs/SETUP.md`](docs/SETUP.md) to
stand up the schema on a fresh Postgres instance, and [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)
for the Python read client (`MarketDataProvider`/`PostgresDatabase`) and the `quant-ingest` CLI
that pulls bars from Yahoo Finance.

---

## Install

```bash
pip install -e ".[dev]"
```

## Lint

```bash
ruff check src/ tests/
ruff format src/ tests/
```

## Test

```bash
pytest
pytest tests/unit/test_foo.py::test_bar   # single test
```

## Database setup

See [`docs/SETUP.md`](docs/SETUP.md) for step-by-step instructions, and
[`docs/DATABASE.md`](docs/DATABASE.md) for connection/tunnel details and query examples.
