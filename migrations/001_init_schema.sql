-- 001_init_schema.sql
--
-- Initial star schema for 1-minute OHLCV market data.
-- Apply with: psql -h <host> -U <user> -d quant_data -f migrations/001_init_schema.sql
--
-- Design: see docs/SCHEMA.md. Four tables — three dimensions (dim_ticker, dim_date, dim_time)
-- and one fact table (fact_market_data_1min) — read-optimized for date-range and time-of-day
-- queries across multiple tickers. No ingest/write logic ships with this migration.

BEGIN;

-- Lightweight migration bookkeeping. Migrations are applied manually via psql for now (no
-- migration-runner tool yet — see docs/DATABASE.md), so this table is purely a record of what's
-- been run, not something that gates re-application.
CREATE TABLE IF NOT EXISTS schema_migrations (
    version TEXT PRIMARY KEY,
    applied_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

COMMENT ON TABLE schema_migrations IS 'Record of which migration files have been applied, in order. Applied manually — not enforced by tooling.';

-- dim_ticker: ticker dimension
CREATE TABLE dim_ticker (
    ticker_id SERIAL PRIMARY KEY,
    ticker TEXT NOT NULL UNIQUE CHECK (ticker = UPPER(ticker)),
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_dim_ticker_symbol ON dim_ticker(ticker);

COMMENT ON TABLE dim_ticker IS 'Ticker symbol dimension. One row per distinct ticker.';
COMMENT ON COLUMN dim_ticker.ticker IS 'Always uppercase — enforced by CHECK, matching quant-scratch''s own ticker-normalization convention. Ingest code should still uppercase before insert rather than rely on the database to catch it.';

-- dim_date: date dimension
CREATE TABLE dim_date (
    date_id SERIAL PRIMARY KEY,
    date DATE NOT NULL UNIQUE,
    day_of_week INT NOT NULL CHECK (day_of_week BETWEEN 0 AND 6),  -- 0 = Monday .. 6 = Sunday
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_dim_date ON dim_date(date);

COMMENT ON TABLE dim_date IS 'Calendar date dimension. One row per distinct trading date encountered by ingest.';
COMMENT ON COLUMN dim_date.day_of_week IS '0 (Monday) through 6 (Sunday), ISO-style. Trading-calendar logic (is_trading_day, market_open_time, etc.) is deliberately out of scope for this initial schema — see docs/SCHEMA.md''s Future Extensibility section.';

-- dim_time: time-of-day dimension
CREATE TABLE dim_time (
    time_id SERIAL PRIMARY KEY,
    hour INT NOT NULL CHECK (hour BETWEEN 0 AND 23),
    minute INT NOT NULL CHECK (minute BETWEEN 0 AND 59),
    time_of_day INT NOT NULL,   -- HHMM as integer, e.g. 930 for 9:30, 1600 for 16:00
    UNIQUE (hour, minute)
);

CREATE INDEX idx_dim_time_of_day ON dim_time(time_of_day);

COMMENT ON TABLE dim_time IS 'Minute-of-day dimension. One row per distinct (hour, minute) pair — at most 1,440 rows regardless of how many days/tickers are loaded.';
COMMENT ON COLUMN dim_time.time_of_day IS 'Redundant with (hour, minute), kept for convenient range queries and display, e.g. WHERE time_of_day BETWEEN 930 AND 1600.';

-- fact_market_data_1min: 1-minute OHLCV facts
CREATE TABLE fact_market_data_1min (
    ticker_id INT NOT NULL REFERENCES dim_ticker(ticker_id),
    date_id INT NOT NULL REFERENCES dim_date(date_id),
    time_id INT NOT NULL REFERENCES dim_time(time_id),
    open NUMERIC NOT NULL,
    high NUMERIC NOT NULL,
    low NUMERIC NOT NULL,
    close NUMERIC NOT NULL,
    volume BIGINT NOT NULL CHECK (volume >= 0),
    timestamp TIMESTAMP NOT NULL,  -- kept for reference/audit; UTC assumed, redundant with date_id + time_id
    PRIMARY KEY (ticker_id, date_id, time_id)
);

CREATE INDEX idx_fact_1min_ticker_date_time ON fact_market_data_1min(ticker_id, date_id, time_id);
CREATE INDEX idx_fact_1min_ticker_date ON fact_market_data_1min(ticker_id, date_id);

COMMENT ON TABLE fact_market_data_1min IS '1-minute OHLCV bars. One row per ticker per minute per trading date, enforced by the primary key.';
COMMENT ON COLUMN fact_market_data_1min.timestamp IS 'UTC timestamp kept for reference/audit alongside the dimension keys — not the primary means of lookup, which goes through ticker_id/date_id/time_id.';
COMMENT ON COLUMN fact_market_data_1min.open IS 'NUMERIC (unbounded precision) rather than a fixed-scale numeric or float, to preserve exact precision for backtests.';

INSERT INTO schema_migrations (version) VALUES ('001_init_schema');

COMMIT;
