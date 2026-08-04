-- 005_remove_volume_field_group.sql
--
-- Volume stops being an independently reconciled field group (tasks/volume_reconciliation.md).
-- It no longer goes through its own Tier 1-4 comparison against the whistleblower -- once a bar's
-- ohlc group resolves, its winning provider's own volume value rides along, taken straight off
-- that provider's staging row. Two reasons (full detail in the task file): volume is used
-- downstream as a relative, provider-tied confidence signal, not a value needing independent
-- cross-provider corroboration; and yfinance (the whistleblower) has no pre-market volume data at
-- all, which is exactly the window this project trades in, so the whistleblower comparison was
-- never meaningful there in the first place.
--
-- Removes the 'volume' row from dim_field_group and every row keyed to it in
-- fact_reconciliation_participant / fact_reconciliation / provider_pair_disagreement (FK-safe
-- child-to-parent order). Everything deleted here is either the illustrative cold-start seed from
-- 004 (never converged against a full dataset) or the small 2026-08-03 live-test sample -- no real
-- production history is lost. dim_field_group's 'ohlc' row, and fact_market_data_1min's own
-- schema/grain, are untouched.
-- Apply with: psql -h <host> -U <user> -d quant_data -f migrations/005_remove_volume_field_group.sql
--
-- Design: see tasks/volume_reconciliation.md.

BEGIN;

DELETE FROM fact_reconciliation_participant
WHERE field_group_id IN (SELECT field_group_id FROM dim_field_group WHERE name = 'volume');

DELETE FROM fact_reconciliation
WHERE field_group_id IN (SELECT field_group_id FROM dim_field_group WHERE name = 'volume');

DELETE FROM provider_pair_disagreement
WHERE field_group_id IN (SELECT field_group_id FROM dim_field_group WHERE name = 'volume');

DELETE FROM dim_field_group WHERE name = 'volume';

INSERT INTO schema_migrations (version) VALUES ('005_remove_volume_field_group');

COMMIT;
