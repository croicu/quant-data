-- 012_add_timestamp_to_pending_manual_resolution.sql
--
-- Adds a denormalized timestamp column to fact_pending_manual_resolution, matching the pattern
-- already used by fact_market_data_1min, staging_market_data_1min, and market_data_archive (all
-- carry timestamp alongside their date_id/time_id dimension keys). fact_pending_manual_resolution
-- was the one holdout, forcing any consumer query to join dim_date/dim_time just to get a readable
-- timestamp -- surfaced while building a Power Query/Excel dashboard against quant-data from
-- quant-scratch's open-quant-data tool (croicu/quant-scratch#19/#20). See
-- https://github.com/croicu/quant-data/issues/36.
--
-- Backfills existing rows from dim_date.date + dim_time.hour/minute, then makes the column
-- NOT NULL -- same two-step shape as other backfilled columns in this codebase (e.g.
-- 008_add_ingestion_coverage's gaps-and-islands backfill).
--
-- Apply with: psql -h <host> -U <user> -d quant_data -f migrations/012_add_timestamp_to_pending_manual_resolution.sql

BEGIN;

ALTER TABLE fact_pending_manual_resolution ADD COLUMN timestamp TIMESTAMP;

UPDATE fact_pending_manual_resolution fpmr
SET timestamp = d.date + (t.hour * INTERVAL '1 hour') + (t.minute * INTERVAL '1 minute')
FROM dim_date d, dim_time t
WHERE d.date_id = fpmr.date_id AND t.time_id = fpmr.time_id;

ALTER TABLE fact_pending_manual_resolution ALTER COLUMN timestamp SET NOT NULL;

COMMENT ON COLUMN fact_pending_manual_resolution.timestamp IS 'UTC, same as fact_market_data_1min/staging_market_data_1min/market_data_archive -- denormalized alongside date_id/time_id purely for read convenience, redundant with those two keys.';

INSERT INTO schema_migrations (version) VALUES ('012_add_timestamp_to_pending_manual_resolution');

COMMIT;
