-- 009_replace_incomplete_with_data_quality.sql
--
-- Replaces the boolean `incomplete` flag on staging_market_data_1min/fact_market_data_1min with
-- a tri-state `data_quality` column: 'accepted' / 'incomplete' / 'rejected'.
--
-- 'accepted' and 'incomplete' are the old FALSE/TRUE, backfilled below from the existing column.
-- 'rejected' is new: a per-provider staging-quality check found the value implausible (as
-- opposed to 'incomplete', which means no confidence in the value but no positive evidence it's
-- wrong -- e.g. a real zero-volume bar). Reconcile's Tier 1 completeness check treats 'rejected'
-- identically to 'incomplete' -- the distinction is for audit/debugging, not different promotion
-- behavior. See tasks/yahoo_data_sanitization.md for the outlier-detection design this supports;
-- this migration only lays the schema foundation -- no code path sets 'rejected' yet.
--
-- Breaking change to quant_data's public contract: OHLCV.incomplete (bool) becomes
-- OHLCV.data_quality (the new DataQuality enum) -- requires a quant-scratch announcement issue
-- per CLAUDE.md's Cross-Repo Coordination rule.
--
-- Apply with: psql -h <host> -U <user> -d quant_data -f migrations/009_replace_incomplete_with_data_quality.sql

BEGIN;

ALTER TABLE staging_market_data_1min ADD COLUMN data_quality TEXT;
UPDATE staging_market_data_1min SET data_quality = CASE WHEN incomplete THEN 'incomplete' ELSE 'accepted' END;
ALTER TABLE staging_market_data_1min ALTER COLUMN data_quality SET NOT NULL;
ALTER TABLE staging_market_data_1min
    ADD CONSTRAINT staging_market_data_1min_data_quality_check CHECK (data_quality IN ('accepted', 'incomplete', 'rejected'));
ALTER TABLE staging_market_data_1min DROP COLUMN incomplete;

ALTER TABLE fact_market_data_1min ADD COLUMN data_quality TEXT;
UPDATE fact_market_data_1min SET data_quality = CASE WHEN incomplete THEN 'incomplete' ELSE 'accepted' END;
ALTER TABLE fact_market_data_1min ALTER COLUMN data_quality SET NOT NULL;
ALTER TABLE fact_market_data_1min
    ADD CONSTRAINT fact_market_data_1min_data_quality_check CHECK (data_quality IN ('accepted', 'incomplete', 'rejected'));
ALTER TABLE fact_market_data_1min DROP COLUMN incomplete;

COMMENT ON COLUMN staging_market_data_1min.data_quality IS 'accepted / incomplete / rejected -- replaces the old boolean incomplete flag. rejected is set by a per-provider staging-quality check (tasks/yahoo_data_sanitization.md, not yet implemented); reconcile Tier 1 treats it identically to incomplete.';
COMMENT ON COLUMN fact_market_data_1min.data_quality IS 'accepted / incomplete / rejected -- replaces the old boolean incomplete flag. Carries forward whichever provider won the bar''s own data_quality at promotion time.';

INSERT INTO schema_migrations (version) VALUES ('009_replace_incomplete_with_data_quality');

COMMIT;
