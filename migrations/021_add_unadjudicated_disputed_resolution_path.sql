-- 021_add_unadjudicated_disputed_resolution_path.sql
--
-- Widens fact_reconciliation.resolution_path's CHECK with a new value,
-- 'unadjudicated_disputed': tasks/reevaluate_unadjudicated_bars.md's retroactive re-check of
-- existing resolution_path='unadjudicated' bars against a since-seeded candidate_pair_mad_band
-- (migration 019). Follow-up to issue #85 -- that task deliberately scoped the historical MAD
-- band to going-forward only, leaving every already-promoted 'unadjudicated' bar unchecked; this
-- is the deferred re-evaluation.
--
-- Repo owner's explicit design call (issue #87): no retraction mechanism. A re-evaluated bar that
-- confirms agreement gets relabeled 'historical_mad_agreement' (matches the live tier's own
-- label -- a reader can no longer tell whether that label came from a live promotion or a
-- retroactive check, nor does it need to). A re-evaluated bar that confirms a real disagreement
-- keeps its fact_market_data_1min value exactly as published -- this repo has no mechanism to
-- retract an already-promoted fact row, and building one was explicitly rejected as
-- out of scope -- but gets relabeled 'unadjudicated_disputed' instead of staying plain
-- 'unadjudicated', so a future manual-review pass can find it with a plain query
-- (WHERE resolution_path = 'unadjudicated_disputed') instead of it looking identical to a bar
-- that was simply never checked at all.
--
-- Apply with: psql -h <host> -U <user> -d quant_data -f migrations/021_add_unadjudicated_disputed_resolution_path.sql

BEGIN;

ALTER TABLE fact_reconciliation DROP CONSTRAINT fact_reconciliation_resolution_path_check;
ALTER TABLE fact_reconciliation ADD CONSTRAINT fact_reconciliation_resolution_path_check
    CHECK (resolution_path IN ('completeness', 'agreement', 'boundary_fix', 'unadjudicated', 'historical_mad_agreement', 'unadjudicated_disputed', 'finalized', 'manual_override'));

COMMENT ON TABLE fact_reconciliation IS 'One row per (bar, field group) once reconciled -- a bar with no row here for one of its groups is still "stuck" in staging_market_data_1min. resolution_path: completeness/agreement/boundary_fix/unadjudicated/historical_mad_agreement/unadjudicated_disputed (automatic pass, Tiers 1-3 plus the no-adjudicator fallbacks and the retroactive re-check), finalized (--finalize''s preferredProvider algorithm), manual_override (a person directly corrected it -- the only path the whistleblower provider can ever win through). unadjudicated_disputed means a bar originally promoted blind (unadjudicated) was later re-checked and found to genuinely disagree beyond its ticker''s historical MAD band -- the promoted value is UNCHANGED (no retraction mechanism exists), this label only marks it for manual review.';

INSERT INTO schema_migrations (version) VALUES ('021_add_unadjudicated_disputed_resolution_path');

COMMIT;
