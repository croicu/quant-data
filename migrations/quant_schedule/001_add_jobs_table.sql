-- 001_add_jobs_table.sql
--
-- Initial schema for the quant_schedule database (croicu/quant-data#66) -- a separate database
-- from quant_data (and quant_ingest), on the same Postgres instance, holding the generic,
-- table-driven schedule for the repo's recurring processes (quant-ingest/quant-stage/
-- quant-reconcile). Deliberately separate, not a table inside quant_data: jobs is meant to
-- eventually schedule work against other databases this repo doesn't own too (a future
-- quant_trades, quant_accounting, ...), so it shouldn't be tied to quant_data's own lifecycle
-- (this repo's routine clean-slate DROP DATABASE quant_data testing must never touch it).
--
-- Apply with: psql -h <host> -U <role> -d quant_schedule -f migrations/quant_schedule/001_add_jobs_table.sql
-- (against the quant_schedule database, not quant_data -- create it first: CREATE DATABASE quant_schedule OWNER quant_data;)
--
-- Job definitions live here as data, not as committed code or box-specific cron/systemd entries,
-- so the public repo never needs to name a specific host -- only the generic table shape and
-- generic dispatch code are committed. This migration ships the table empty; real job rows (real
-- intervals, real command values, anything host-specific) are inserted by hand afterward, by the
-- quant_scheduler role (see docs/DATABASE.md), same precedent as quant_data's own
-- dataset_inception (migration 007).
--
-- `command` is a plain argv array (e.g. '{quant-ingest,--catch-up}'), passed straight to
-- subprocess.run without shell=True -- no shell injection/quoting concern. `status` tracks
-- 'idle'/'running' so quant-dispatch can skip a job whose previous invocation hasn't finished yet
-- rather than double-dispatching it (e.g. two concurrent quant-reconcile runs against the same
-- database).

BEGIN;

-- Own migration bookkeeping, separate from quant_data's/quant_ingest's schema_migrations -- this
-- is a different database with its own independent migration history.
CREATE TABLE IF NOT EXISTS schema_migrations (
    version TEXT PRIMARY KEY,
    applied_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

COMMENT ON TABLE schema_migrations IS 'Record of which quant_schedule migration files have been applied, in order. Applied manually -- not enforced by tooling.';

CREATE TABLE jobs (
    job_id SERIAL PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    command TEXT[] NOT NULL,
    interval_seconds INT NOT NULL CHECK (interval_seconds > 0),
    next_run_at TIMESTAMP NOT NULL,
    enabled BOOLEAN NOT NULL DEFAULT true,
    status TEXT NOT NULL DEFAULT 'idle' CHECK (status IN ('idle', 'running')),
    last_run_at TIMESTAMP,
    last_exit_code INT,
    last_error TEXT
);

COMMENT ON TABLE jobs IS 'Table-driven schedule for quant-dispatch (croicu/quant-data#66). command is an argv array run via subprocess without shell=True. status guards against double-dispatch while a previous invocation is still running. No rows seeded here -- real schedules are inserted by hand per host, by the quant_scheduler role.';

INSERT INTO schema_migrations (version) VALUES ('001_add_jobs_table');

COMMIT;
