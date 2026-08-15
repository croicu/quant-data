-- 011_add_market_data_archive.sql
--
-- New market_data_archive table: a permanent, append-only record of a bar's raw provider value
-- once it's no longer kept in staging_market_data_1min -- either a purged candidate row (archived
-- immediately before purge_staging_bar deletes it) or a hand-entered 'manual' correction (written
-- directly here, never staged at all). See tasks/staging_archive_before_purge.md /
-- croicu/quant-data#35 for the full design discussion.
--
-- Widens dim_provider.role with a third value, 'advisor': can suggest a value but has no
-- autonomous authoring rights -- unlike 'candidate', an advisor provider can never win a bar
-- through the automatic Tier 1-3 pass, only through an explicit human action. Seeds 'manual' (the
-- existing --finalize/hand-correction path gaining an actual dim_provider identity) and
-- 'databento' (a purely out-of-band reference with zero footprint elsewhere in the schema -- the
-- dim_provider row exists for identity/documentation only).
--
-- fact_reconciliation_participant gains a nullable archive_id, populated once a participant's raw
-- value is archived (an archived candidate, or a 'manual' winner) -- NULL for the whistleblower
-- (never archived, permanently purge-exempt in staging) or any not-yet-archived participant.
--
-- Apply with: psql -h <host> -U <user> -d quant_data -f migrations/011_add_market_data_archive.sql

BEGIN;

ALTER TABLE dim_provider DROP CONSTRAINT dim_provider_role_check;
ALTER TABLE dim_provider ADD CONSTRAINT dim_provider_role_check CHECK (role IN ('candidate', 'whistleblower', 'advisor'));

INSERT INTO dim_provider (name, role) VALUES ('manual', 'advisor'), ('databento', 'advisor');

-- Loosely mirrors staging_market_data_1min's columns rather than the leanest possible shape, since
-- new columns are expected here over time and a close mirror keeps that trivial. A surrogate
-- archive_id PK (not the natural (provider_id, ticker_id, date_id, time_id) key) is required
-- because fact_reconciliation_participant.archive_id must be able to reference a 'manual' row with
-- no corresponding staging row to key off of, and because the natural key isn't unique here -- the
-- same bar can be archived more than once over time (e.g. re-ingested and re-resolved after a
-- prior archival).
CREATE TABLE market_data_archive (
    archive_id SERIAL PRIMARY KEY,
    provider_id INT NOT NULL REFERENCES dim_provider(provider_id),
    ticker_id INT NOT NULL REFERENCES dim_ticker(ticker_id),
    date_id INT NOT NULL REFERENCES dim_date(date_id),
    time_id INT NOT NULL REFERENCES dim_time(time_id),
    timestamp TIMESTAMP NOT NULL,
    open NUMERIC NOT NULL,
    high NUMERIC NOT NULL,
    low NUMERIC NOT NULL,
    close NUMERIC NOT NULL,
    volume BIGINT NOT NULL CHECK (volume >= 0),
    data_quality TEXT NOT NULL CHECK (data_quality IN ('accepted', 'incomplete', 'rejected')),
    archived_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_market_data_archive_ticker_date_time ON market_data_archive(ticker_id, date_id, time_id);

COMMENT ON TABLE market_data_archive IS 'Permanent, append-only archive of a bar''s raw provider value once removed from staging_market_data_1min (purged candidate rows) or entered directly (manual corrections, never staged at all). Never pruned. See tasks/staging_archive_before_purge.md / croicu/quant-data#35.';

ALTER TABLE fact_reconciliation_participant ADD COLUMN archive_id INT REFERENCES market_data_archive(archive_id);

COMMENT ON COLUMN fact_reconciliation_participant.archive_id IS 'Points to this participant''s archived raw value once archived -- populated for an archived candidate or a manual winner, NULL for the whistleblower (never archived, permanently purge-exempt in staging) or any not-yet-archived participant.';

INSERT INTO schema_migrations (version) VALUES ('011_add_market_data_archive');

COMMIT;
