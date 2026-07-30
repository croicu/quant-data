-- 003_add_dim_provider_and_staging.sql
--
-- Adds dim_provider (data-source dimension: yfinance, ibkr) and staging_market_data_1min (each
-- provider's raw, as-ingested bars, held separately until reconciled into fact_market_data_1min).
-- Schema-only step ahead of the IBKR IntraDayProvider and reconciliation logic themselves --
-- fact_market_data_1min's own schema, grain, and public contract are untouched by this migration.
-- Apply with: psql -h <host> -U <user> -d quant_data -f migrations/003_add_dim_provider_and_staging.sql
--
-- Design: see tasks/ibkr-provider-reconciliation.md and docs/SCHEMA.md.

BEGIN;

-- dim_provider: data-source dimension
CREATE TABLE dim_provider (
    provider_id SERIAL PRIMARY KEY,
    name TEXT NOT NULL UNIQUE CHECK (name = LOWER(name)),
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

COMMENT ON TABLE dim_provider IS 'Data-source dimension. One row per distinct provider -- IBKR''s real and paper accounts return identical market data, so they share a single ''ibkr'' row; only the account used to reach IBKR differs, not the provider identity for reconciliation purposes.';
COMMENT ON COLUMN dim_provider.name IS 'Always lowercase — enforced by CHECK, matching this repo''s existing log-category convention (e.g. CATEGORY_YFINANCE = "yfinance").';

INSERT INTO dim_provider (name) VALUES ('yfinance'), ('ibkr');

-- staging_market_data_1min: each provider's raw, as-ingested bars
CREATE TABLE staging_market_data_1min (
    provider_id INT NOT NULL REFERENCES dim_provider(provider_id),
    ticker_id INT NOT NULL REFERENCES dim_ticker(ticker_id),
    date_id INT NOT NULL REFERENCES dim_date(date_id),
    time_id INT NOT NULL REFERENCES dim_time(time_id),
    open NUMERIC NOT NULL,
    high NUMERIC NOT NULL,
    low NUMERIC NOT NULL,
    close NUMERIC NOT NULL,
    volume BIGINT NOT NULL CHECK (volume >= 0),
    timestamp TIMESTAMP NOT NULL,
    incomplete BOOLEAN NOT NULL DEFAULT FALSE,
    PRIMARY KEY (provider_id, ticker_id, date_id, time_id)
);

-- Reconciliation's actual access pattern is "gather every provider's row for one (ticker, date,
-- time) bar" -- the opposite leading-column order from the primary key above (which matches how
-- each IntraDayProvider writes its own rows independently, blind to other providers).
CREATE INDEX idx_staging_1min_ticker_date_time ON staging_market_data_1min(ticker_id, date_id, time_id);

COMMENT ON TABLE staging_market_data_1min IS 'Each provider''s raw, as-ingested bars -- same shape as fact_market_data_1min plus provider_id. Purged per-bar once reconciled into fact_market_data_1min; left in place (an implicit "settlement" state) when not yet all providers have reported, or when reported values disagree beyond tolerance. See tasks/ibkr-provider-reconciliation.md for the reconciliation logic itself (not yet implemented).';

INSERT INTO schema_migrations (version) VALUES ('003_add_dim_provider_and_staging');

COMMIT;
