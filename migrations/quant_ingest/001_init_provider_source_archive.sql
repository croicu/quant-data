-- 001_init_provider_source_archive.sql
--
-- Initial schema for the quant_ingest database (croicu/quant-data#52) -- a separate database from
-- quant_data, on the same Postgres instance, holding the immutable, append-only record of every
-- provider fetch. Deliberately separate, not a table/schema inside quant_data: Postgres has no
-- cross-database foreign keys, and this repo's own routine clean-slate testing (DROP DATABASE
-- quant_data) must never be able to touch it structurally.
--
-- Apply with: psql -h <host> -U <role> -d quant_ingest -f migrations/quant_ingest/001_init_provider_source_archive.sql
-- (against the quant_ingest database, not quant_data -- create it first: CREATE DATABASE quant_ingest OWNER quant_data;)
--
-- No relationship to quant_data's star schema: ticker/provider are plain validated text, not
-- surrogate-key foreign keys to dim_ticker/dim_provider, since those live in a different database
-- entirely.

BEGIN;

-- Own migration bookkeeping, separate from quant_data's schema_migrations -- this is a different
-- database with its own independent migration history.
CREATE TABLE IF NOT EXISTS schema_migrations (
    version TEXT PRIMARY KEY,
    applied_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

COMMENT ON TABLE schema_migrations IS 'Record of which quant_ingest migration files have been applied, in order. Applied manually -- not enforced by tooling.';

-- provider_source_archive: immutable, append-only record of every successful provider fetch.
CREATE TABLE provider_source_archive (
    id BIGSERIAL PRIMARY KEY,
    ticker TEXT NOT NULL CHECK (ticker = UPPER(ticker)),
    provider TEXT NOT NULL,
    trading_date DATE NOT NULL,
    fetched_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    fetch_version TEXT NOT NULL,
    payload_kind TEXT NOT NULL CHECK (payload_kind IN ('raw_api_response', 'parsed_bars')),
    payload JSONB NOT NULL
);

CREATE INDEX idx_provider_source_archive_lookup ON provider_source_archive(ticker, provider, trading_date);

COMMENT ON TABLE provider_source_archive IS 'Append-only record of every provider fetch -- no unique constraint on (ticker, provider, trading_date): a re-fetch of the same day is a new row, never an upsert. Application code (ProviderSourceArchiveWriter) only ever INSERTs. quant_writer originally had no UPDATE/DELETE grant at all (true DB-enforced immutability); DELETE was granted afterward for manual cleanup, at the repo owner''s explicit request -- see docs/DATABASE.md. UPDATE remains ungranted: a row can be removed, never edited in place.';
COMMENT ON COLUMN provider_source_archive.payload_kind IS 'raw_api_response for providers where this repo talks HTTP directly and can capture the literal response (e.g. Massive); parsed_bars for providers with no raw payload this repo''s code ever sees (yfinance parses its own HTTP call internally; IBKR''s wire protocol is not JSON at all) -- see quant_data._internal.contracts.PayloadKind.';
COMMENT ON COLUMN provider_source_archive.fetch_version IS 'The fetching provider''s FETCH_VERSION at the time of this fetch -- a plain string, bumped by hand whenever that provider''s own request construction changes in a way that could change what comes back.';

-- archive_coverage: the archive-side equivalent of quant_data's ingestion_coverage
-- (croicu/quant-data#31), but keyed by fetch_version too -- coalesced contiguous date ranges per
-- (ticker, provider, fetch_version), updated incrementally on every successful fetch rather than
-- recomputed from scratch.
CREATE TABLE archive_coverage (
    coverage_id SERIAL PRIMARY KEY,
    ticker TEXT NOT NULL,
    provider TEXT NOT NULL,
    fetch_version TEXT NOT NULL,
    start_date DATE NOT NULL,
    end_date DATE NOT NULL,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_archive_coverage_lookup ON archive_coverage(ticker, provider, fetch_version);

COMMENT ON TABLE archive_coverage IS 'Coalesced contiguous date ranges per (ticker, provider, fetch_version). A date not covered by any fetch_version''s range is a genuine gap; a date covered only by a non-current fetch_version is stale and a candidate for re-fetching.';

INSERT INTO schema_migrations (version) VALUES ('001_init_provider_source_archive');

COMMIT;
