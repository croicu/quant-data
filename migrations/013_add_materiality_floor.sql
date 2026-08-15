-- 013_add_materiality_floor.sql
--
-- New materiality_floor table: a per-(provider, ticker, field) minimum tolerance, bounding
-- Tier 2/3's tolerance formula (k * stddev * reference_value) below so Tier 2 classification
-- means "statistically out of band AND material enough to be worth a human's time" -- not pure
-- z-score distance. Without a floor, a field group whose true variance is genuinely tiny (a
-- consistently-agreeing candidate) sees its tolerance shrink right along with the honestly-
-- converging stddev estimate, pushing economically trivial disagreements to Tier 4 (manual)
-- purely because they exceed an ever-tightening relative threshold.
--
-- Keyed to exactly mirror provider_pair_disagreement's existing grain -- provider_id implicitly
-- means the candidate (whistleblower stays singular/implicit, same convention as
-- provider_pair_disagreement, no separate whistleblower column).
--
-- No rows are seeded here, same precedent as data_quality_thresholds (migration 010): a
-- (provider, ticker, field) with no row here applies floor_value = 0, i.e. no floor at all --
-- additive/opt-in, doesn't change existing behavior until a row is deliberately added. Real floor
-- values need seeding + validation against the manual review backlog before this is anything more
-- than schema (see tasks/materiality_floor_tolerance.md's Open Questions).
--
-- Apply with: psql -h <host> -U <user> -d quant_data -f migrations/013_add_materiality_floor.sql

BEGIN;

CREATE TABLE materiality_floor (
    provider_id INT NOT NULL REFERENCES dim_provider(provider_id),
    ticker_id INT NOT NULL REFERENCES dim_ticker(ticker_id),
    field_id INT NOT NULL REFERENCES dim_field(field_id),
    floor_value NUMERIC NOT NULL,
    floor_type TEXT NOT NULL CHECK (floor_type IN ('absolute', 'bps_of_reference')),
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (provider_id, ticker_id, field_id)
);

COMMENT ON TABLE materiality_floor IS 'Per-(provider, ticker, field) minimum tolerance floor for quant-reconcile''s Tier 2/3 agreement check -- bounds k * stddev * reference_value below by an economically meaningful minimum. No row required -- absence falls back to floor_value = 0 (no floor, current behavior unchanged). floor_type absolute: floor_value is a raw unit; bps_of_reference: floor_value is basis points of the bar''s own reference_value. See tasks/materiality_floor_tolerance.md.';

INSERT INTO schema_migrations (version) VALUES ('013_add_materiality_floor');

COMMIT;
