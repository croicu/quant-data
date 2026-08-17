-- 016_add_massive_provider.sql
--
-- Seeds `massive` (formerly Polygon.io) as a second dim_provider row with role = 'candidate',
-- alongside the existing 'ibkr' candidate -- the first time fact_market_data_1min's reconciliation
-- has ever had two real candidates competing. See croicu/quant-data#44 for the full design
-- (provider mechanics, the whistleblower-validity-gate fix, the graduation-key-granularity fix) --
-- this migration only adds the provider identity row itself. No other schema change is needed for
-- the provider mechanics: dim_provider.role's CHECK already allows 'candidate', and
-- provider_pair_disagreement has been keyed (provider_id, ticker_id, field_id) -- not tied to any
-- specific candidate count -- since migration 007.
--
-- Apply with: psql -h <host> -U <user> -d quant_data -f migrations/016_add_massive_provider.sql

BEGIN;

INSERT INTO dim_provider (name, role) VALUES ('massive', 'candidate');

INSERT INTO schema_migrations (version) VALUES ('016_add_massive_provider');

COMMIT;
