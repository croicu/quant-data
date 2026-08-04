-- 006_add_pending_manual_resolution.sql
--
-- Makes "stuck" an explicit, trackable state instead of an implicit one (tasks/quant_reconcile.md's
-- "Updated (2026-08-03)" section). Previously, a bar/group that exhausted the automatic pass's
-- Tiers 1-3 just sat in staging with no record of the attempt -- every future quant-reconcile run
-- re-fetched and re-evaluated it regardless of whether anything relevant had changed, which
-- compounds badly under a realistic cadence (e.g. plain quant-reconcile daily, --finalize weekly).
--
-- fact_pending_manual_resolution follows the same "presence of a row is the status" pattern
-- fact_reconciliation already uses. Plain quant-reconcile runs skip anything with a row here
-- entirely; --finalize reads only what has a row here, force-resolves it, and deletes the row.
-- Apply with: psql -h <host> -U <user> -d quant_data -f migrations/006_add_pending_manual_resolution.sql
--
-- Design: see tasks/quant_reconcile.md.

BEGIN;

CREATE TABLE fact_pending_manual_resolution (
    ticker_id INT NOT NULL REFERENCES dim_ticker(ticker_id),
    date_id INT NOT NULL REFERENCES dim_date(date_id),
    time_id INT NOT NULL REFERENCES dim_time(time_id),
    field_group_id INT NOT NULL REFERENCES dim_field_group(field_group_id),
    flagged_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (ticker_id, date_id, time_id, field_group_id)
);

COMMENT ON TABLE fact_pending_manual_resolution IS 'Presence of a row means this (bar, field group) exhausted the automatic pass''s Tiers 1-3 and is awaiting --finalize or manual correction -- same grain and "presence is status" convention as fact_reconciliation. Plain quant-reconcile runs never fetch/evaluate a (bar, group) with a row here; --finalize fetches only what has a row here, force-resolves it via the finalize algorithm (no Tier 1-3 re-attempt), and deletes the row once resolved.';

INSERT INTO schema_migrations (version) VALUES ('006_add_pending_manual_resolution');

COMMIT;
