# Scheduled Jobs

## Status: Brainstorm (postponed — see [issue #3](https://github.com/croicu/quant-data/issues/3))

## Problem statement

quant-data's responsibility is bigger than the schema: pulling data from heterogeneous providers
into the warehouse, serving it to consumers, and running recurring background maintenance
(vacuum/analyze, reindexing, and eventually whatever the ingest cadence needs). That maintenance
work has to run *somewhere* on a schedule, but today "somewhere" means `CroicuWS1` specifically —
and the roadmap is to eventually migrate the database to AWS RDS or Azure Database for PostgreSQL
(see the transport/endpoint-agnostic design decision in
`tasks/postgres_client_and_dimensions.md`). Baking `CroicuWS1`-specific scheduling (cron entries,
systemd timers naming that host, box-specific paths/secrets) into this **public** GitHub repo would
leak location-specific operational detail that doesn't belong in public source control, and would
need to be redone by hand on every future migration.

## Design decisions

- **Job definitions live in the database, not the git repo.** A `jobs` table (naming: avoided
  `tasks` — collides with this repo's own `tasks/*.md` planning docs and issue-status workflow) —
  schedule, last-run/next-run, status, and a flexible payload column live as *data*, not committed
  code. Data never appears in git history regardless of how location-specific it gets, which
  directly solves the "don't bleed box-specific detail into the public repo" problem: the public
  repo only ever holds the generic table schema (a migration) and generic job-running code (some
  dispatch-by-job-type mechanism), never actual row values naming a specific host.
- **Investigation phase — deliberately not solidifying yet.** Keep the initial design simple but
  extensible; revisit mechanism choice once there's a concrete maintenance need driving it, not
  speculatively now.

## Open questions

- **pg_cron vs. custom poller**: `pg_cron` is a Postgres extension where schedules live in a table
  inside the database itself — supported on self-managed Postgres, AWS RDS, and Azure Flexible
  Server, so it'd carry over cleanly across the planned migration path. But it only runs SQL/stored
  procedures directly; it can't make an IBKR network call itself. Likely split: `pg_cron` (or plain
  SQL) for pure-SQL maintenance (VACUUM/ANALYZE/reindexing), while jobs needing external Python
  (e.g. the ingest pull) still need an external trigger (cron/systemd timer today, cloud scheduler
  later) — the `jobs` table would hold that job's schedule/last-run/status either way, for
  portability, even though something host-specific still has to periodically check it.
- **Table structure**: not finalized — likely something like `jobs(id, name, schedule, payload
  jsonb, last_run_at, next_run_at, status, enabled)`, with `payload` as a jsonb blob so different
  job types don't need their own rigid columns. Needs a concrete first job (probably a DB
  maintenance one) to shake out the real shape.
- **Public/private repo split**: considering a separate private/intranet git repo for the actual
  on-the-box deployment specifics (systemd unit files naming `CroicuWS1`, actual job row values,
  anything host-identifying) once this concept solidifies, keeping quant-data itself generic. Not
  decided how that second repo would relate to this one (submodule? fully separate? deployed via
  SSH directly, no repo at all?) — revisit once there's an actual first job to deploy.
- **Role/permissions**: does `quant_writer` own this table, or does it need its own role? Not yet
  considered — revisit alongside the `quant_reader`/`quant_writer` split in
  `tasks/postgres_client_and_dimensions.md`.

## Implementation plan

<!-- Added when advancing to Implementation. -->

## Test results

<!-- Added when advancing to Testing / Ready to Submit. -->
