-- 020_seed_candidate_pair_mad_band_spy.sql
--
-- Seeds candidate_pair_mad_band (migration 019) with SPY's own real, validated conditional-MAD
-- values -- not a starting guess, the exact pooled full-range values
-- tasks/ibkr_massive_mad_calibration.md's E4 (.exp/budget/k_sweep.py) already computed and
-- verified against 146,272 lag-0-joined ibkr/massive bars (2025-12-31..2026-08-21), reused
-- verbatim here rather than recomputed. k=3.0 is E8's own recommended value (matches production's
-- existing DEFAULT_RECONCILE_K), checked live against a second review pass before being trusted --
-- see that task's "Pre-report blockers" and "Second review pass" sections for the full
-- verification trail (E6 precision bug found and fixed, B4/R1 checked and did not undermine k=3 at
-- the recommended operating point, R3's pooled-vs-rolling tension resolved in favor of exactly
-- this pooled-and-fixed shape).
--
-- SPY only -- the only ticker with a frozen, validated dataset behind these numbers. Every other
-- ticker stays unseeded (falls back to today's unadjudicated behavior) until it has its own
-- calibration, same "ship schema, seed real values once validated" precedent as
-- materiality_floor's own seed migration (014).
--
-- Apply with: psql -h <host> -U <user> -d quant_data -f migrations/020_seed_candidate_pair_mad_band_spy.sql

BEGIN;

INSERT INTO candidate_pair_mad_band (ticker_id, field_id, conditional_mad_scaled, k)
SELECT t.ticker_id, f.field_id, v.conditional_mad_scaled, 3.0
FROM (VALUES
    ('open',  7.548154034284933e-06),
    ('high',  4.93846754202411e-06),
    ('low',   4.7291956369035015e-06),
    ('close', 7.65450258563553e-06)
) AS v(field, conditional_mad_scaled)
JOIN dim_field f ON f.name = v.field
JOIN dim_ticker t ON t.ticker = 'SPY';

INSERT INTO schema_migrations (version) VALUES ('020_seed_candidate_pair_mad_band_spy');

COMMIT;
