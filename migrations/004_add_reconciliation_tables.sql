-- 004_add_reconciliation_tables.sql
--
-- Schema-only slice for quant-reconcile (tasks/quant-reconcile.md) -- no Python code consumes
-- these tables yet; quant-reconcile's automatic pass / --finalize / manual-correction logic is
-- separate, later work. Adds:
--   - dim_provider.role: distinguishes real candidate providers (whose data can be promoted to
--     fact_market_data_1min) from a whistleblower provider (compared against, never promoted
--     except via manual correction).
--   - dim_field_group: which fact_market_data_1min columns must be resolved together (OHLC) vs.
--     independently (volume), so a promoted bar is never assembled from mismatched providers
--     within a group.
--   - fact_reconciliation / fact_reconciliation_participant: one resolved (bar, field group) per
--     row, plus who competed for it and who won -- doubles as the provider-reputation record
--     (see docs/SCHEMA.md).
--   - provider_pair_disagreement: running (Welford's algorithm) variance of each candidate
--     provider's disagreement against the whistleblower, per field group -- the measured basis
--     for reconciliation's comparison tolerance.
-- fact_market_data_1min's own schema, grain, and public contract are untouched by this migration.
-- Apply with: psql -h <host> -U <user> -d quant_data -f migrations/004_add_reconciliation_tables.sql
--
-- Design: see tasks/quant-reconcile.md and docs/SCHEMA.md.

BEGIN;

-- dim_provider.role: candidate (data can be promoted) vs. whistleblower (compared against only)
ALTER TABLE dim_provider
    ADD COLUMN role TEXT NOT NULL DEFAULT 'candidate' CHECK (role IN ('candidate', 'whistleblower'));

UPDATE dim_provider SET role = 'whistleblower' WHERE name = 'yfinance';

COMMENT ON COLUMN dim_provider.role IS 'candidate: data can be promoted into fact_market_data_1min. whistleblower: compared against to derive tolerance/completeness signals, never promoted except via a person''s manual correction. Single source of truth for this distinction -- not duplicated in settings.json.';

-- dim_field_group: which fact_market_data_1min columns must resolve together
CREATE TABLE dim_field_group (
    field_group_id SERIAL PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

COMMENT ON TABLE dim_field_group IS 'Groups of fact_market_data_1min columns that must be resolved from a single provider together (e.g. OHLC), vs. independently (e.g. volume) -- prevents assembling an internally-inconsistent bar (e.g. low > close) from two different providers'' individual fields. Extensible: a later migration adding a new fact_market_data_1min column assigns it to an existing or new group row, a data change, not a schema change.';

INSERT INTO dim_field_group (name) VALUES ('ohlc'), ('volume');

-- fact_reconciliation: one row per resolved (bar, field group) -- presence of a row IS "resolved"
CREATE TABLE fact_reconciliation (
    ticker_id INT NOT NULL REFERENCES dim_ticker(ticker_id),
    date_id INT NOT NULL REFERENCES dim_date(date_id),
    time_id INT NOT NULL REFERENCES dim_time(time_id),
    field_group_id INT NOT NULL REFERENCES dim_field_group(field_group_id),
    winning_provider_id INT NOT NULL REFERENCES dim_provider(provider_id),
    resolution_path TEXT NOT NULL CHECK (resolution_path IN ('completeness', 'agreement', 'boundary_fix', 'finalized', 'manual_override')),
    resolved_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (ticker_id, date_id, time_id, field_group_id)
);

CREATE INDEX idx_fact_reconciliation_resolution_path ON fact_reconciliation(resolution_path);

COMMENT ON TABLE fact_reconciliation IS 'One row per (bar, field group) once reconciled -- a bar with no row here for one of its groups is still "stuck" in staging_market_data_1min. resolution_path: completeness/agreement/boundary_fix (automatic pass, Tiers 1-3), finalized (--finalize''s preferredProvider algorithm), manual_override (a person directly corrected it -- the only path the whistleblower provider can ever win through).';

-- fact_reconciliation_participant: who competed for each resolved (bar, group), win or lose --
-- the whistleblower gets a row here too, like any provider that wrote a staging row for that bar,
-- not just the winning candidate.
CREATE TABLE fact_reconciliation_participant (
    ticker_id INT NOT NULL,
    date_id INT NOT NULL,
    time_id INT NOT NULL,
    field_group_id INT NOT NULL,
    provider_id INT NOT NULL REFERENCES dim_provider(provider_id),
    won BOOLEAN NOT NULL,
    PRIMARY KEY (ticker_id, date_id, time_id, field_group_id, provider_id),
    FOREIGN KEY (ticker_id, date_id, time_id, field_group_id)
        REFERENCES fact_reconciliation(ticker_id, date_id, time_id, field_group_id)
);

CREATE INDEX idx_fact_reconciliation_participant_provider ON fact_reconciliation_participant(provider_id, won);

COMMENT ON TABLE fact_reconciliation_participant IS 'One row per provider that competed for a resolved (bar, group), win or lose. Doubles as the provider-reputation record: "who tends to lose" is WHERE won = FALSE grouped by provider, filtered (via a join to fact_reconciliation) to resolution_path = ''manual_override'' -- a bare disagreement isn''t evidence of fault, only an actual manual correction is. For the whistleblower provider specifically, won = TRUE rows are themselves a signal: how often a person actually reached for its value over manual correction.';

-- provider_pair_disagreement: running variance of each candidate's disagreement vs. the
-- whistleblower, per field group -- the measured basis for reconciliation's comparison tolerance
CREATE TABLE provider_pair_disagreement (
    provider_id INT NOT NULL REFERENCES dim_provider(provider_id),
    field_group_id INT NOT NULL REFERENCES dim_field_group(field_group_id),
    sample_count BIGINT NOT NULL DEFAULT 0 CHECK (sample_count >= 0),
    running_mean NUMERIC NOT NULL DEFAULT 0,
    running_m2 NUMERIC NOT NULL DEFAULT 0,
    stddev NUMERIC,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (provider_id, field_group_id)
);

COMMENT ON TABLE provider_pair_disagreement IS 'Running (Welford''s algorithm) variance of Var(candidate_value - whistleblower_value), per candidate provider (never the whistleblower itself -- always the fixed other side of the comparison) per field group. No ticker dimension: noise is a property of methodology, not of individual tickers. stddev is a relative/fractional value, denormalized for fast reads -- scaled against an actual reference value at comparison time to get a bar-specific absolute tolerance (tolerance = k * stddev * reference_value). Only in-band (Tier 2 raw-agreement) observations update this -- finalized/manual_override resolutions are deliberately excluded so outliers cannot gradually widen "normal".';
COMMENT ON COLUMN provider_pair_disagreement.running_mean IS 'Signed: candidate_value - whistleblower_value, not the reverse -- carries directional-bias information (e.g. a future tool detecting the candidate consistently reading high) that the symmetric stddev alone would lose.';

-- Illustrative cold-start seed (not measured data -- revisit once real reconciliation history
-- exists), pseudo-count 100 so it fades slowly as real observations accumulate. running_m2
-- derived as (stddev^2 * sample_count), i.e. population variance, to match the seeded stddev.
INSERT INTO provider_pair_disagreement (provider_id, field_group_id, sample_count, running_mean, running_m2, stddev)
SELECT ibkr.provider_id, g.field_group_id, seed.sample_count, seed.running_mean, seed.running_m2, seed.stddev
FROM (VALUES
    ('ohlc', 100, 0, 0.000064, 0.0008),
    ('volume', 100, 0, 0.09, 0.03)
) AS seed(field_group_name, sample_count, running_mean, running_m2, stddev)
JOIN dim_field_group g ON g.name = seed.field_group_name
CROSS JOIN (SELECT provider_id FROM dim_provider WHERE name = 'ibkr') AS ibkr;

INSERT INTO schema_migrations (version) VALUES ('004_add_reconciliation_tables');

COMMIT;
