-- 007_add_dim_field_and_dataset_inception.sql
--
-- Schema-only slice 1 of pipeline accuracy hardening (croicu/quant-data#28) -- no algorithm/CLI
-- behavior changes yet; quant-reconcile keeps reading/writing exactly as it does today until
-- slice 2's code lands to consume the new shape. Adds:
--   - dim_field: per-OHLC-field dimension ('open'/'high'/'low'/'close'), mirroring dim_provider's
--     shape. Distinct from dim_field_group (unchanged, still governs fact_reconciliation's atomic
--     promotion unit -- OHLC is still promoted as one bar from one provider; only the *tolerance
--     comparison* becomes per-field).
--   - provider_pair_disagreement: re-keyed from (provider_id, field_group_id) to
--     (provider_id, ticker_id, field_id) -- one independently-measured Var(candidate -
--     whistleblower) per candidate/ticker/field, fixing two compounding pooling problems (across
--     tickers, across fields) previously traced to a single global stddev. Existing rows are
--     discarded, not migrated forward into the new key shape -- every (provider, ticker, field)
--     starts at zero and earns its own real history (same precedent set for the ticker-only
--     predecessor of this change, tasks/per_ticker_disagreement.md).
--   - dataset_inception: new single-row table recording where the dataset is meant to start,
--     needed for slice 2's --backfill. Left empty by this migration -- the actual inception_date
--     value is a data decision, not a schema one.
-- Apply with: psql -h <host> -U <user> -d quant_data -f migrations/007_add_dim_field_and_dataset_inception.sql
--
-- Design: see croicu/quant-data#28 (tasks/pipeline_accuracy_hardening.md is now just a pointer
-- there).

BEGIN;

-- dim_field: per-OHLC-field dimension, mirroring dim_provider's shape
CREATE TABLE dim_field (
    field_id SERIAL PRIMARY KEY,
    name TEXT NOT NULL UNIQUE CHECK (name = LOWER(name)),
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

COMMENT ON TABLE dim_field IS 'Per-OHLC-field dimension (open/high/low/close). Distinct from dim_field_group: dim_field_group still governs which fact_market_data_1min columns must be promoted together as one atomic unit (OHLC from a single winning provider); dim_field exists solely to let provider_pair_disagreement measure each field''s tolerance independently, since yfinance''s noise concentrates in high/low while open/close stay comparatively stable -- a single ohlc-group tolerance let the noisy fields set (or blow) the band for the stable ones.';

INSERT INTO dim_field (name) VALUES ('open'), ('high'), ('low'), ('close');

-- provider_pair_disagreement: re-key from (provider_id, field_group_id) to
-- (provider_id, ticker_id, field_id). Existing rows discarded -- see header.
DELETE FROM provider_pair_disagreement;

ALTER TABLE provider_pair_disagreement DROP CONSTRAINT provider_pair_disagreement_pkey;
ALTER TABLE provider_pair_disagreement DROP COLUMN field_group_id;
ALTER TABLE provider_pair_disagreement ADD COLUMN ticker_id INT NOT NULL REFERENCES dim_ticker(ticker_id);
ALTER TABLE provider_pair_disagreement ADD COLUMN field_id INT NOT NULL REFERENCES dim_field(field_id);
ALTER TABLE provider_pair_disagreement ADD PRIMARY KEY (provider_id, ticker_id, field_id);

COMMENT ON TABLE provider_pair_disagreement IS 'Running (Welford''s algorithm) variance of Var(candidate_value - whistleblower_value), per candidate provider (never the whistleblower itself) per ticker per field. Keyed per-ticker and per-field (not pooled) because noise isn''t homogeneous across either dimension: DOG''s real disagreement band is wider than SPY/QQQ/DIA''s, and yfinance''s noise concentrates in high/low while open/close stay comparatively stable. stddev is a relative/fractional value, denormalized for fast reads -- scaled against an actual reference value at comparison time to get a bar-specific absolute tolerance (tolerance = k * stddev * reference_value). Only in-band (Tier 2 raw-agreement) observations update this -- finalized/manual_override resolutions are deliberately excluded so outliers cannot gradually widen "normal". A ticker below its graduation threshold (2,000 matched bars, see croicu/quant-data#28) has no rows here at all -- nothing is computed until the full graduation batch is in.';
COMMENT ON COLUMN provider_pair_disagreement.running_mean IS 'Signed: candidate_value - whistleblower_value, not the reverse -- carries directional-bias information (e.g. a future tool detecting the candidate consistently reading high) that the symmetric stddev alone would lose.';

-- dataset_inception: single-row table recording where the dataset is meant to start. Left empty
-- here -- the actual inception_date value is a data decision for later, not part of this
-- schema-only slice.
CREATE TABLE dataset_inception (
    id INT PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    inception_date DATE NOT NULL
);

COMMENT ON TABLE dataset_inception IS 'Single-row table (CHECK (id = 1) enforces this at the DB level) recording the date the dataset is meant to start from -- a fact about the dataset''s own properties, not tunable process behavior, so it lives here rather than settings.json. Moving inception_date backward is an operational trigger, not just a value update: backfill must re-run for every configured ticker from the new inception_date to that ticker''s own current earliest covered date (not a single shared date, since tickers may already have different amounts of history). See croicu/quant-data#28.';

INSERT INTO schema_migrations (version) VALUES ('007_add_dim_field_and_dataset_inception');

COMMIT;
