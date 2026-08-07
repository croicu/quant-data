-- 010_add_data_quality_thresholds.sql
--
-- New data_quality_thresholds table: per-(provider, ticker) coefficients for the intra-provider
-- outlier-detection check (tasks/yahoo_data_sanitization.md) that sets
-- staging_market_data_1min.data_quality = 'rejected'. A separate config surface from
-- provider_pair_disagreement's cross-provider reconciliation tolerances -- related but distinct,
-- deliberately not unified into one table.
--
-- No rows are seeded here. reconcile/outlier_detection.py's DEFAULT_K_* constants (3/6/4/8,
-- the seed values from the 2026-08-06 design session -- unvalidated gut priors, not yet checked
-- against the 622-bar backlog) are the fallback whenever no row exists for a given
-- (provider, ticker) -- this table only ever holds deliberately-tuned overrides.
--
-- Apply with: psql -h <host> -U <user> -d quant_data -f migrations/010_add_data_quality_thresholds.sql

BEGIN;

CREATE TABLE data_quality_thresholds (
    provider_id INT NOT NULL REFERENCES dim_provider(provider_id),
    ticker_id INT NOT NULL REFERENCES dim_ticker(ticker_id),
    k_reversal_oc NUMERIC NOT NULL,
    k_trend_oc NUMERIC NOT NULL,
    k_reversal_hl NUMERIC NOT NULL,
    k_trend_hl NUMERIC NOT NULL,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (provider_id, ticker_id)
);

COMMENT ON TABLE data_quality_thresholds IS 'Per-(provider, ticker) MAD-multiplier coefficients for the intra-provider outlier check that sets staging_market_data_1min.data_quality = ''rejected'' (see reconcile/outlier_detection.py, tasks/yahoo_data_sanitization.md). No row required -- absence falls back to the module-level DEFAULT_K_* constants. Deliberately separate from provider_pair_disagreement (cross-provider tolerance, a different concept).';

INSERT INTO schema_migrations (version) VALUES ('010_add_data_quality_thresholds');

COMMIT;
