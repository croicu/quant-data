-- 002_add_dependencies_and_run_once.sql
--
-- Adds real dependency tracking and one-shot job support to quant_schedule.jobs, for the
-- work-item scheduler (croicu/quant-data#68). #66's original design deliberately left job
-- ordering to interval/offset scheduling and had every job reschedule itself forever -- both
-- accepted risks for the recurring quant-ingest/quant-stage/quant-reconcile jobs #66 shipped for.
-- A one-shot backfill work item (ingest per day/provider/method, then one staging job, then one
-- reconcile job) doesn't get the self-healing retries that made offset timing acceptable, so this
-- migration adds the mechanism #66 explicitly deferred.
--
-- Apply with: psql -h <host> -U <role> -d quant_schedule -f migrations/quant_schedule/002_add_dependencies_and_run_once.sql
--
-- run_once: when true, a job that succeeds is disabled instead of rescheduled (quant-dispatch's
-- _run_job sets enabled = false on success only -- a failure still reschedules/retries normally).
-- job_dependencies: a job is only "due" once every job it depends on has last_exit_code = 0 (see
-- ScheduleDatabase.fetch_due_jobs). ON DELETE CASCADE both sides so removing a job (manual
-- cleanup via psql) can't leave an orphaned dependency row behind.

BEGIN;

ALTER TABLE jobs ADD COLUMN run_once BOOLEAN NOT NULL DEFAULT false;

CREATE TABLE job_dependencies (
    job_id INT NOT NULL REFERENCES jobs (job_id) ON DELETE CASCADE,
    depends_on_job_id INT NOT NULL REFERENCES jobs (job_id) ON DELETE CASCADE,
    PRIMARY KEY (job_id, depends_on_job_id),
    CHECK (job_id <> depends_on_job_id)
);

COMMENT ON TABLE job_dependencies IS 'A job is only dispatched once every job it depends on has succeeded (jobs.last_exit_code = 0) -- see ScheduleDatabase.fetch_due_jobs. Populated by quant-schedule (croicu/quant-data#68) when it decomposes a work item into a job graph.';

INSERT INTO schema_migrations (version) VALUES ('002_add_dependencies_and_run_once');

COMMIT;
