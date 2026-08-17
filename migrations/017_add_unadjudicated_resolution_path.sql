-- 017_add_unadjudicated_resolution_path.sql
--
-- Widens fact_reconciliation.resolution_path's CHECK with a new value, 'unadjudicated': the
-- whistleblower-validity-gate fix (croicu/quant-data#44) resolves a bar to
-- settings.reconcile.preferredProvider's raw value whenever no ACCEPTED whistleblower exists to
-- adjudicate between two or more real candidates (an outlier-rejected or confirmed-absent
-- whistleblower) -- previously unreachable with exactly one candidate present, where Tier 1
-- completeness always caught the bar first, before Tier 2/3 ever looked at the whistleblower's
-- own data_quality.
--
-- Kept distinct from 'finalized' (--finalize's own preferredProvider fallback, only ever reached
-- from the pending-manual-resolution queue after the automatic pass already gave up) and
-- 'manual_override' (a person's explicit correction): 'unadjudicated' fires automatically,
-- mid-automatic-pass, with no human involved and no tolerance comparison ever attempted, so
-- reusing either existing label would misrepresent how the bar actually resolved. The distinction
-- matters downstream too -- reconcile/cli.py's automatic pass already only feeds
-- provider_pair_disagreement's Welford variance on RESOLUTION_AGREEMENT, so an 'unadjudicated'
-- resolution is automatically excluded from that update with no additional code change.
--
-- Apply with: psql -h <host> -U <user> -d quant_data -f migrations/017_add_unadjudicated_resolution_path.sql

BEGIN;

ALTER TABLE fact_reconciliation DROP CONSTRAINT fact_reconciliation_resolution_path_check;
ALTER TABLE fact_reconciliation ADD CONSTRAINT fact_reconciliation_resolution_path_check
    CHECK (resolution_path IN ('completeness', 'agreement', 'boundary_fix', 'unadjudicated', 'finalized', 'manual_override'));

COMMENT ON TABLE fact_reconciliation IS 'One row per (bar, field group) once reconciled -- a bar with no row here for one of its groups is still "stuck" in staging_market_data_1min. resolution_path: completeness/agreement/boundary_fix/unadjudicated (automatic pass, Tiers 1-3 plus the no-adjudicator fallback), finalized (--finalize''s preferredProvider algorithm), manual_override (a person directly corrected it -- the only path the whistleblower provider can ever win through).';

INSERT INTO schema_migrations (version) VALUES ('017_add_unadjudicated_resolution_path');

COMMIT;
