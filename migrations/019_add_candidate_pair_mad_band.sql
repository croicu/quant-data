-- 019_add_candidate_pair_mad_band.sql
--
-- New candidate_pair_mad_band table: a per-(ticker, field) pooled, fixed conditional-MAD band on
-- the ibkr/massive candidate-pair difference series, used by quant-reconcile as a stand-in
-- adjudicator for the historical period yfinance's ~30-day rolling window can't reach (no
-- ACCEPTED whistleblower available at all). Validated by tasks/ibkr_massive_mad_calibration.md
-- (E0-E8, all merged) and integrated per tasks/retroactive_revision.md.
--
-- Checked live against production before this migration was written: 6,736 of SPY's 13,437
-- already-promoted fact_reconciliation rows (50.1%) went through resolution_path = 'unadjudicated'
-- -- ibkr's raw value promoted with zero comparison against massive at all, because no
-- whistleblower was ever available for that historical period. This table is what lets
-- quant-reconcile actually check candidate agreement there instead of promoting blind.
--
-- Deliberately pooled over the full historical range and fixed, not rolled per period -- per the
-- calibration task's own R3a finding, rolling this value would make drift structurally
-- undetectable (every period would flag near its own design rate by construction). A ticker/field
-- with no row here has no historical MAD band at all -- quant-reconcile falls back to today's
-- unadjudicated blind-promotion behavior for it, unchanged, until it gets one. Same "ship schema,
-- seed real values once validated" precedent as materiality_floor (migration 013) and
-- data_quality_thresholds (migration 010): no rows seeded here, see the follow-on seed migration
-- for SPY's own already-validated values.
--
-- Widens fact_reconciliation.resolution_path's CHECK with 'historical_mad_agreement': distinct
-- from 'agreement' (Tier 2, feeds provider_pair_disagreement's Welford variance -- this must not,
-- since there's no whistleblower observation to record) and from 'unadjudicated' (no check
-- attempted at all, see migration 017's own reasoning for that same distinction).
--
-- Apply with: psql -h <host> -U <user> -d quant_data -f migrations/019_add_candidate_pair_mad_band.sql

BEGIN;

CREATE TABLE candidate_pair_mad_band (
    ticker_id INT NOT NULL REFERENCES dim_ticker(ticker_id),
    field_id INT NOT NULL REFERENCES dim_field(field_id),
    conditional_mad_scaled NUMERIC NOT NULL,
    k NUMERIC NOT NULL,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (ticker_id, field_id)
);

COMMENT ON TABLE candidate_pair_mad_band IS 'Per-(ticker, field) pooled, fixed conditional-MAD band on the ibkr/massive candidate-pair difference series -- quant-reconcile''s stand-in adjudicator for the historical (no-whistleblower) period. No row required -- absence falls back to resolution_path=''unadjudicated'' (blind preferredProvider promotion, current behavior unchanged). conditional_mad_scaled is 1.4826 * median(|d|) among nonzero (ibkr-massive)/midpoint differences (excludes the exact-match point mass); k multiplies it into an actual tolerance. Deliberately NOT rolled/recalibrated per period -- see tasks/ibkr_massive_mad_calibration.md''s R3a finding and tasks/retroactive_revision.md.';

ALTER TABLE fact_reconciliation DROP CONSTRAINT fact_reconciliation_resolution_path_check;
ALTER TABLE fact_reconciliation ADD CONSTRAINT fact_reconciliation_resolution_path_check
    CHECK (resolution_path IN ('completeness', 'agreement', 'boundary_fix', 'unadjudicated', 'historical_mad_agreement', 'finalized', 'manual_override'));

COMMENT ON TABLE fact_reconciliation IS 'One row per (bar, field group) once reconciled -- a bar with no row here for one of its groups is still "stuck" in staging_market_data_1min. resolution_path: completeness/agreement/boundary_fix/unadjudicated/historical_mad_agreement (automatic pass, Tiers 1-3 plus the no-adjudicator fallbacks), finalized (--finalize''s preferredProvider algorithm), manual_override (a person directly corrected it -- the only path the whistleblower provider can ever win through).';

INSERT INTO schema_migrations (version) VALUES ('019_add_candidate_pair_mad_band');

COMMIT;
