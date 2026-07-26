# SCHEMA.md

Table definitions, rationale, and query examples for the `quant-data` warehouse.

## Overview

A four-table star schema: three dimensions (`dim_ticker`, `dim_date`, `dim_time`) and one fact
table (`fact_market_data_1min`). Dimensions are looked up/created once per distinct value (ticker,
calendar date, minute-of-day); the fact table holds one row per ticker per minute per trading date,
referencing all three dimensions by foreign key.

```
dim_ticker ──┐
             │
dim_date ────┼──> fact_market_data_1min
             │
dim_time ────┘
```

Rationale for a star schema over one denormalized table: dimensions avoid repeating ticker/date/
time data across millions of fact rows, keep foreign keys narrow (4-byte `INT`s) for join/index
efficiency, and set up cleanly for future aggregate fact tables (5-minute, hourly bars) that reuse
the same dimension keys without touching the raw 1-minute data.

## `dim_ticker`

| Column | Type | Notes |
|---|---|---|
| `ticker_id` | `SERIAL PRIMARY KEY` | |
| `ticker` | `TEXT NOT NULL UNIQUE` | Always uppercase — enforced by a `CHECK` constraint |
| `created_at` | `TIMESTAMP` | Defaults to insert time |

## `dim_date`

| Column | Type | Notes |
|---|---|---|
| `date_id` | `SERIAL PRIMARY KEY` | |
| `date` | `DATE NOT NULL UNIQUE` | |
| `day_of_week` | `INT NOT NULL` | 0 (Monday) – 6 (Sunday), ISO-style |
| `created_at` | `TIMESTAMP` | Defaults to insert time |

## `dim_time`

| Column | Type | Notes |
|---|---|---|
| `time_id` | `SERIAL PRIMARY KEY` | |
| `hour` | `INT NOT NULL` | 0–23 |
| `minute` | `INT NOT NULL` | 0–59 |
| `time_of_day` | `INT NOT NULL` | `HHMM` as an integer, e.g. `930` for 9:30, `1600` for 16:00 |

At most 1,440 rows total (one per minute of the day), regardless of how much history is loaded —
populated once, not per ticker/date.

## `fact_market_data_1min`

| Column | Type | Notes |
|---|---|---|
| `ticker_id` | `INT NOT NULL` | FK → `dim_ticker` |
| `date_id` | `INT NOT NULL` | FK → `dim_date` |
| `time_id` | `INT NOT NULL` | FK → `dim_time` |
| `open`, `high`, `low`, `close` | `NUMERIC NOT NULL` | Unbounded-precision, not a fixed-scale numeric or float — preserves exact precision for backtests |
| `volume` | `BIGINT NOT NULL` | `>= 0` |
| `timestamp` | `TIMESTAMP NOT NULL` | UTC assumed; kept for reference/audit, redundant with the three dimension keys |

Primary key: `(ticker_id, date_id, time_id)` — enforces exactly one bar per ticker per minute per
date.

## Indexes

- `idx_fact_1min_ticker_date_time` on `(ticker_id, date_id, time_id)` — matches the primary key
  order; supports point lookups and range scans within a single ticker/date.
- `idx_fact_1min_ticker_date` on `(ticker_id, date_id)` — supports "give me the whole day for this
  ticker" queries without needing a specific time.
- `idx_dim_ticker_symbol`, `idx_dim_date`, `idx_dim_time_of_day` — support dimension lookups by
  their natural key (ticker symbol, calendar date, `HHMM` integer) when resolving a natural-key
  query into dimension IDs before joining the fact table.

## Query examples

Fetch all 1-minute bars for AAPL on 2026-01-15:

```sql
SELECT f.timestamp, f.open, f.high, f.low, f.close, f.volume
FROM fact_market_data_1min f
JOIN dim_ticker t ON t.ticker_id = f.ticker_id
JOIN dim_date d ON d.date_id = f.date_id
WHERE t.ticker = 'AAPL' AND d.date = '2026-01-15'
ORDER BY f.time_id;
```

Fetch AAPL bars during regular trading hours only (9:30–16:00) across a date range:

```sql
SELECT d.date, ti.time_of_day, f.open, f.high, f.low, f.close, f.volume
FROM fact_market_data_1min f
JOIN dim_ticker t ON t.ticker_id = f.ticker_id
JOIN dim_date d ON d.date_id = f.date_id
JOIN dim_time ti ON ti.time_id = f.time_id
WHERE t.ticker = 'AAPL'
  AND d.date BETWEEN '2026-01-01' AND '2026-01-31'
  AND ti.time_of_day BETWEEN 930 AND 1600
ORDER BY d.date, ti.time_of_day;
```

## Future extensibility

Deliberately out of scope for this initial migration (see `database_layer.md`'s original design
notes in `quant-scratch` for the full reasoning) — noted here so they aren't rediscovered from
scratch later:

- **Aggregate fact tables**: `fact_market_data_5min`, `fact_market_data_hourly`, reusing the same
  three dimension tables, populated by rolling up `fact_market_data_1min` rather than re-ingesting.
- **Trading calendar in `dim_date`**: `is_trading_day`, `market_open_time`, `market_close_time`
  columns, to distinguish holidays/weekends from real trading days without relying on ingest-time
  logic. Not needed for the schema itself yet since ingest only ever writes real trading data.
- **Table partitioning**: once `fact_market_data_1min` holds multiple years across many tickers,
  native Postgres declarative range partitioning by `date_id` (or `timestamp`) is the natural next
  step for both query performance and easier archival of old partitions. Not added now — no schema
  bloat ahead of an actual need.
- **No JSON columns**: explicit typed columns only, by design — keeps the schema queryable and
  indexable without a JSON-parsing layer.
