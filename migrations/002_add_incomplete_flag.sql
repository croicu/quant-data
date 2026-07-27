-- 002_add_incomplete_flag.sql
--
-- Adds fact_market_data_1min.incomplete: flags bars where the provider couldn't supply full data
-- (e.g. missing pre-market volume), so incomplete rows can be prioritized for backfilling later.
-- Apply with: psql -h <host> -U <user> -d quant_data -f migrations/002_add_incomplete_flag.sql

BEGIN;

ALTER TABLE fact_market_data_1min
    ADD COLUMN incomplete BOOLEAN NOT NULL DEFAULT FALSE;

COMMENT ON COLUMN fact_market_data_1min.incomplete IS 'True when the provider could not supply full data for this bar (e.g. missing pre-market volume) — a signal to prioritize backfilling, not a data-quality gate on read.';

INSERT INTO schema_migrations (version) VALUES ('002_add_incomplete_flag');

COMMIT;
