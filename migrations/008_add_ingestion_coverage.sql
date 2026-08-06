-- 008_add_ingestion_coverage.sql
--
-- New ingestion_coverage table (croicu/quant-data#31): tracks contiguous date ranges each
-- (ticker, provider) pair was successfully ingested for -- one row per genuinely contiguous run,
-- coalesced. Explicit tracking rather than deriving coverage from staging_market_data_1min's
-- presence/absence, since staging rows get purged over time (candidates once resolved; the
-- whistleblower never, per its permanent purge exemption) and wouldn't stay a reliable long-term
-- coverage signal.
--
-- This migration is schema + a one-time backfill only -- quant-ingest's write path (record +
-- coalesce on every successful fetch) and quant-reconcile's consuming change (let a candidate
-- promote via Tier 1 completeness when the whistleblower is confirmed absent, not just when it
-- reported incomplete) are follow-up work, not part of this migration.
--
-- The backfill below populates ingestion_coverage from whatever's currently in
-- staging_market_data_1min at migration-apply time, using a "gaps and islands" contiguous-run
-- detection (dim_date.date_id increments by exactly 1 per calendar day, including weekends, so a
-- true gap -- e.g. a weekend, or a day never ingested -- correctly stays a separate range instead
-- of being bridged by a naive MIN/MAX). On a fresh bootstrap database (empty staging), this
-- backfill is a no-op.
-- Apply with: psql -h <host> -U <user> -d quant_data -f migrations/008_add_ingestion_coverage.sql
--
-- Design: see croicu/quant-data#31.

BEGIN;

CREATE TABLE ingestion_coverage (
    coverage_id SERIAL PRIMARY KEY,
    ticker_id INT NOT NULL REFERENCES dim_ticker(ticker_id),
    provider_id INT NOT NULL REFERENCES dim_provider(provider_id),
    start_date_id INT NOT NULL REFERENCES dim_date(date_id),
    end_date_id INT NOT NULL REFERENCES dim_date(date_id),
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (start_date_id <= end_date_id)
);

CREATE INDEX idx_ingestion_coverage_ticker_provider ON ingestion_coverage(ticker_id, provider_id);

COMMENT ON TABLE ingestion_coverage IS 'One row per contiguous date range successfully ingested for a (ticker, provider) pair -- ingestion coalesces adjacent/overlapping ranges into one row rather than one row per day. A provider''s fetch completing without raising (regardless of resulting bar count, including zero) marks that date covered; a raised AppError -- including a confirmed-empty whole day -- does not. quant-reconcile uses this to let a candidate promote via Tier 1 completeness when the whistleblower is confirmed absent for a bar (its date range was ingested, it just has no row for that specific minute -- most commonly a real no-trade/no-volume minute Yahoo never emits a row for), rather than leaving that bar stuck forever waiting on a whistleblower row that will never arrive. See croicu/quant-data#31.';

WITH distinct_dates AS (
    -- staging_market_data_1min has many rows per (ticker, provider, date) -- one per minute --
    -- so dates must be de-duplicated *before* numbering, not after: ROW_NUMBER() applied directly
    -- to the raw rows would assign a distinct number to every row even within the same date,
    -- breaking the "date_id - row_number is constant within a contiguous run" trick entirely.
    SELECT DISTINCT ticker_id, provider_id, date_id
    FROM staging_market_data_1min
),
islands AS (
    SELECT ticker_id, provider_id, date_id,
           date_id - ROW_NUMBER() OVER (PARTITION BY ticker_id, provider_id ORDER BY date_id) AS contiguous_run
    FROM distinct_dates
)
INSERT INTO ingestion_coverage (ticker_id, provider_id, start_date_id, end_date_id)
SELECT ticker_id, provider_id, min(date_id) AS start_date_id, max(date_id) AS end_date_id
FROM islands
GROUP BY ticker_id, provider_id, contiguous_run;

INSERT INTO schema_migrations (version) VALUES ('008_add_ingestion_coverage');

COMMIT;
