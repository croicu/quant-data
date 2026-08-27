# DATABASE.md

Installing PostgreSQL, connecting to it, and populating dimension tables for `quant-data`.

**On hosting/transport**: everything below (Ubuntu box, SSH tunnel) describes *today's* hosting
choice, not an architectural requirement. If this database ever moves to AWS RDS, Azure Database
for PostgreSQL, or anywhere else, this file is what changes — `PostgresDatabase` (see
`docs/ARCHITECTURE.md`) only ever takes connection details from `settings.json`/
`settings.local.json`, never anything hardcoded, so a hosting change should never require a code
change in `quant-data` or any client.

## Installing PostgreSQL on the Ubuntu box

```bash
sudo apt update
sudo apt install -y postgresql postgresql-contrib
sudo systemctl enable --now postgresql

# Create a role and database for this project
sudo -u postgres createuser --interactive --pwprompt   # follow the prompts for a new role
sudo -u postgres createdb -O <role> quant_data
```

By default, PostgreSQL only listens on `localhost` and only accepts local `peer`-authenticated
connections. For remote access over SSH (recommended over opening the Postgres port directly to
the internet), leave `postgresql.conf`'s `listen_addresses` at `localhost` and connect via an SSH
tunnel instead (see below) rather than editing `pg_hba.conf` to accept remote passwords.

## Connecting over an SSH tunnel (for `psql` / direct admin access)

**This section is for `psql` only** — the commands below (applying migrations, populating
dimension tables, ad-hoc verification) talk to Postgres directly, not through any `quant_data`
Python code, so they still need a tunnel you start and keep running yourself. The Python client
(`MarketData`/`create_postgres_provider`, and `quant-ingest`) does **not** need this anymore — see
"Connection testing from Python" below for how it opens its own tunnel automatically when
configured to.

One-off tunnel, using your existing `~/.ssh` key-based auth to the box:

```bash
ssh -N -L 5433:localhost:5432 <ssh_user>@<ubuntu_host>
```

This forwards local port `5433` to the box's Postgres on `5432`. Leave that command running, then
connect as if Postgres were local:

```bash
psql -h localhost -p 5433 -U <role> -d quant_data
```

For a tunnel that survives reboots/reconnects rather than a manual `ssh` you have to babysit, a
systemd user service works well:

```ini
# ~/.config/systemd/user/quant-data-tunnel.service
[Unit]
Description=SSH tunnel to quant-data Postgres

[Service]
ExecStart=/usr/bin/ssh -N -L 5433:localhost:5432 <ssh_user>@<ubuntu_host>
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
```

```bash
systemctl --user enable --now quant-data-tunnel.service
```

## Applying the schema migration

```bash
psql -h localhost -p 5433 -U <role> -d quant_data -f migrations/001_init_schema.sql
```

See `docs/SETUP.md` for the full step-by-step first-time setup checklist.

## Verifying the schema

```bash
psql -h localhost -p 5433 -U <role> -d quant_data -c '\dt'
```

Expect to see `schema_migrations`, `dim_ticker`, `dim_date`, `dim_time`, and
`fact_market_data_1min`. Confirm the migration was recorded:

```sql
SELECT * FROM schema_migrations;
```

## Populating dimension tables

`dim_time` is fixed-size (one row per minute of the day) and can be populated in full immediately:

```sql
INSERT INTO dim_time (hour, minute, time_of_day)
SELECT h, m, h * 100 + m
FROM generate_series(0, 23) AS h
CROSS JOIN generate_series(0, 59) AS m
ON CONFLICT (hour, minute) DO NOTHING;
```

`dim_date` can be pre-populated for a working date range (adjust the bounds to taste — this
doesn't need to cover every date you'll ever ingest, just a comfortable range; re-run with new
bounds later, `ON CONFLICT` makes it safe to repeat):

```sql
INSERT INTO dim_date (date, day_of_week)
SELECT d::date, EXTRACT(ISODOW FROM d)::int - 1  -- ISODOW is 1 (Monday)..7 (Sunday); shift to 0..6
FROM generate_series('2020-01-01'::date, '2030-12-31'::date, '1 day'::interval) AS d
ON CONFLICT (date) DO NOTHING;
```

## Connection testing from Python

Consumers (e.g. `quant-scratch`) should use `MarketData`, not `PostgresDatabase` directly.
`MarketData` is agnostic of the concrete backend — it takes a provider (built via a factory, e.g.
`create_postgres_provider`) rather than connection details directly. `create_postgres_provider`
connects as `quant_reader` by default (`SELECT`-only, trust-authenticated, no password required).

**No manual tunnel needed for this path.** `create_postgres_provider`/`PostgresDatabase` resolve
their own `ConnectionTransport` (see `docs/ARCHITECTURE.md`) from the `ssh_user`/`ssh_key_path`
arguments (or `settings.postgres.sshUser`/`sshKeyPath`, both optional and must be set together):

- **Cloud-hosted Postgres, or an already-running manual tunnel** — omit `ssh_user`/`ssh_key_path`;
  `host`/`port` are connected to directly, exactly as before this existed:

  ```python
  from datetime import date
  from quant_data import MarketData, create_postgres_provider

  provider = create_postgres_provider(host="localhost", port=5433, dbname="quant_data")
  with MarketData(provider) as client:
      bars = client.fetch_bars("AAPL", start_date=date(2026, 1, 15), end_date=date(2026, 1, 15))
      print(len(bars))

      # One entry per (bar, field group, provider) still awaiting manual resolution -- see
      # docs/SCHEMA.md's fact_pending_manual_resolution section.
      pending = client.fetch_pending_resolution_bars("SPY", start_date=date(2026, 8, 3), end_date=date(2026, 8, 3))
      for candidate in pending:
          print(candidate.provider, candidate.role.value, candidate.field_group, candidate.bar.close)

      # yfinance values a per-provider plausibility check flagged implausible -- distinct from
      # fetch_pending_resolution_bars, since a rejected value with an accepted candidate resolves
      # automatically and never becomes pending, so this is the only way to see it.
      rejected = client.fetch_rejected_whistleblower_bars("SPY", start_date=date(2026, 8, 3), end_date=date(2026, 8, 3))
      for entry in rejected:
          print(entry.provider, entry.bar.timestamp, entry.bar.close)
  ```

- **CroicuWS1's on-prem hosting** — pass `ssh_user`/`ssh_key_path`; `host`/`port` now mean the
  remote box's SSH host and its Postgres port (typically `5432`, not a local forwarded port), since
  the local forwarded port is chosen automatically:

  ```python
  provider = create_postgres_provider(
      host="<ubuntu_host>", port=5432, dbname="quant_data",
      ssh_user="<ssh_user>", ssh_key_path="/home/<you>/.ssh/id_ed25519",
  )
  with MarketData(provider) as client:
      bars = client.fetch_bars("AAPL", start_date=date(2026, 1, 15), end_date=date(2026, 1, 15))
  ```

  No pre-existing `ssh -N -L ...`/systemd tunnel needed — `PostgresDatabase` opens and tears down
  its own tunnel for the lifetime of that instance. Key-based auth only (no passphrase/agent
  support).

(`MarketData` is context-manageable — `close()` runs automatically on exit, which also tears down
the tunnel if one was opened. Calling `close()` manually, without `with`, still works too.)

Both `create_postgres_provider` and `MarketData` also accept an optional `logger=` matching the
`quant_data.LoggingSink` `Protocol` — pass your own application's logger to route quant-data's
internal connect/query timing and status messages into your own log stream instead of quant-data's
private one (see `docs/ARCHITECTURE.md`'s `LoggingSink` section).

`quant_writer` (password-protected, read/write) is for `ingest` only — see
`quant_data._internal.shared.postgres.PostgresDatabase` if you need the concrete read/write implementation directly
(e.g. writing your own ingest tooling), with connection details from
`settings.json`/`settings.local.json`'s `postgres` section (see `docs/ARCHITECTURE.md` for the
full shape).

## Granting `quant_reader` access to new tables

Role/grant setup isn't tracked in `migrations/` (it was done ad hoc when `quant_reader` was
created — see `docs/SCHEMA.md`'s "Roles" note) — new tables don't automatically become readable by
`quant_reader` just because a new `MarketData` method queries them. As of
`MarketData.fetch_pending_resolution_bars`, `quant_reader` additionally needs `SELECT` on
`staging_market_data_1min`, `fact_pending_manual_resolution`, `dim_field_group`, and `dim_provider`
(join targets of that query) — run this once, connected as the schema-owner role, against each
environment that should serve it:

```sql
GRANT SELECT ON staging_market_data_1min, fact_pending_manual_resolution, dim_field_group, dim_provider TO quant_reader;
```

Without this, `fetch_pending_resolution_bars` fails with a real Postgres `permission denied`, the
same enforcement `docs/ARCHITECTURE.md` documents for the write side.

Migration `011` (`market_data_archive`, croicu/quant-data#35) similarly needs its own grant, per the
decision to keep a single reader role rather than a more granular set of accounts:

```sql
GRANT SELECT ON market_data_archive TO quant_reader;
```

## Granting `quant_writer` access to new tables

Same gap as `quant_reader` above, on the write-role side: `quant_writer` doesn't automatically get
access to a table created after its own grants were set up, even one `quant-ingest` itself needs to
query. `quant-ingest --backfill` (croicu/quant-data#28) added `PostgresDatabase.fetch_dataset_inception_date`,
which reads `dataset_inception` — a table added by migration `007`, after `quant_writer`'s original
grants. Run this once, connected as the schema-owner role, against each environment that should run
`--backfill`:

```sql
GRANT SELECT ON dataset_inception TO quant_writer;
```

`fetch_earliest_covered_date` needs no new grant — it only reads `staging_market_data_1min`,
`fact_market_data_1min`, `dim_ticker`, and `dim_date`, all tables `quant_writer` already reads/writes
via `quant-ingest`'s and `quant-reconcile`'s existing paths.

`dataset_inception` itself is also still empty on the real database as of the migration that added
it — insert the actual value once decided (connected as the schema-owner role):

```sql
INSERT INTO dataset_inception (inception_date) VALUES ('2020-01-01');
```

`quant-ingest --backfill` fails with a clear `AppError` (not a silent no-op) until this row exists.

Migration `011` (`market_data_archive`, croicu/quant-data#35) needs `quant_writer` to both insert
archived rows and back-fill the `archive_id` it just created onto the corresponding
`fact_reconciliation_participant` row (`PostgresDatabase.purge_staging_bar`'s archive-then-delete):

```sql
GRANT INSERT ON market_data_archive TO quant_writer;
GRANT USAGE ON SEQUENCE market_data_archive_archive_id_seq TO quant_writer;
GRANT UPDATE (archive_id) ON fact_reconciliation_participant TO quant_writer;
```

The sequence grant is the easy one to miss — `archive_id`'s `SERIAL` default calls `nextval()`
under the hood, which needs its own `USAGE` grant separately from the table's `INSERT` grant. This
is also the first table `quant_writer` inserts into that has a `SERIAL` primary key (every table it
writes to so far uses a composite natural key), so it's the first time this gap has come up.

Migration `013` (`materiality_floor`, `tasks/materiality_floor_tolerance.md`) needs `quant_writer`
to read it (`PostgresDatabase.fetch_materiality_floors`, called once per `quant-reconcile` run):

```sql
GRANT SELECT ON materiality_floor TO quant_writer;
```

No `quant_reader` grant needed — like `data_quality_thresholds`, this is a purely internal
reconcile-tuning table, never read via `MarketData`.

Migration `019` (`candidate_pair_mad_band`, `tasks/retroactive_revision.md`) needs `quant_writer`
to read it (`PostgresDatabase.fetch_candidate_pair_mad_bands`, called once per `quant-reconcile`
run, same pattern as `materiality_floor` above):

```sql
GRANT SELECT ON candidate_pair_mad_band TO quant_writer;
```

No `quant_reader` grant needed here either — same reasoning as `materiality_floor`. Applied live on
CroicuWS2 (2026-08-26) alongside migrations `019`/`020` themselves.

## Setting up the `quant_ingest` database

`quant_ingest` (croicu/quant-data#52) is a separate database on the same Postgres instance as
`quant_data` — the immutable, append-only record of every provider fetch, kept structurally
isolated from `quant_data`'s own routine clean-slate `DROP DATABASE`/`TRUNCATE` testing. Create and
migrate it the same way as `quant_data` was originally set up, connected as the schema-owner role:

```bash
psql -h localhost -p 5433 -U quant_data -d postgres -c "CREATE DATABASE quant_ingest OWNER quant_data;"
psql -h localhost -p 5433 -U quant_data -d quant_ingest -f migrations/quant_ingest/001_init_provider_source_archive.sql
```

Grant `quant_writer` access — originally deliberately asymmetric between the two tables, since only
`provider_source_archive` was meant to be immutable:

```sql
GRANT CONNECT ON DATABASE quant_ingest TO quant_writer;
GRANT SELECT, INSERT ON provider_source_archive TO quant_writer;
GRANT USAGE ON SEQUENCE provider_source_archive_id_seq TO quant_writer;
GRANT SELECT, INSERT, UPDATE, DELETE ON archive_coverage TO quant_writer;
GRANT USAGE ON SEQUENCE archive_coverage_coverage_id_seq TO quant_writer;
```

**Update (2026-08-17): `DELETE` was additionally granted on `provider_source_archive`** at the repo
owner's explicit request, for manual row cleanup:

```sql
GRANT DELETE ON provider_source_archive TO quant_writer;
```

This was a deliberate, informed relaxation, not an oversight — confirmed explicitly after being
told it removes the DB-level enforcement that a compromised or buggy write path cannot delete
archived data. `ProviderSourceArchiveWriter.record_fetch` itself still only ever `INSERT`s; nothing
in the application code exercises this grant. `UPDATE` remains ungranted on
`provider_source_archive` — a row can be removed, never edited in place. `archive_coverage` is
maintained summary state (same as `quant_data`'s own `ingestion_coverage`), so it always had the
full read/write grant. No `quant_reader` grant on either table — this is an internal audit log, not
part of `MarketData`'s public read surface; add one later if a read need actually shows up.

Then point `quant-ingest` at it via `settings.postgres.archiver.dbname` (`settings.local.json`, same
`host`/`port`/`user`/`password`/`sshUser`/`sshKeyPath` as the rest of `settings.postgres` — just a
different `dbname`; `archiver` carries no `user`/`password` of its own, unlike `worker`/`scheduler`
below, since it deliberately reuses `settings.postgres.user`/`.password` — `quant_writer` already
spans `quant_data` + `quant_ingest`, so there's no separate role to configure):

```json
{
  "settings": {
    "postgres": {
      "host": "localhost",
      "port": 5433,
      "user": "quant_writer",
      "password": "...",
      "dbname": "quant_data",
      "archiver": {
        "dbname": "quant_ingest"
      }
    }
  }
}
```

Omitting `archiver` disables archiving entirely (the default, backward-compatible with any
`settings.json` predating this feature) — `quant-ingest` still writes to `staging_market_data_1min`
exactly as before.

## Setting up the `quant_schedule` database

`quant_schedule` (croicu/quant-data#66) is a separate database on the same Postgres instance as
`quant_data`/`quant_ingest` — the table-driven schedule `quant-dispatch` reads. Deliberately its
own database, not a table inside `quant_data`: `jobs` is meant to eventually schedule work against
other databases this repo doesn't own too (a future `quant_trades`, `quant_accounting`, ...), so it
shouldn't share `quant_data`'s lifecycle (this repo's own routine clean-slate `DROP DATABASE
quant_data` testing must never touch it). Create and migrate it the same way as `quant_ingest`,
connected as the schema-owner role:

```bash
psql -h localhost -p 5433 -U quant_data -d postgres -c "CREATE DATABASE quant_schedule OWNER quant_data;"
psql -h localhost -p 5433 -U quant_data -d quant_schedule -f migrations/quant_schedule/001_add_jobs_table.sql
psql -h localhost -p 5433 -U quant_data -d quant_schedule -f migrations/quant_schedule/002_add_dependencies_and_run_once.sql
```

`002_add_dependencies_and_run_once.sql` (croicu/quant-data#68) adds `jobs.run_once` (a job that
succeeds is disabled instead of rescheduled) and the `job_dependencies` table (a job is only
dispatched once every job it depends on has succeeded) — what `quant-schedule` needs to decompose a
bulk backfill into an ordered job graph. Apply it right after `001` — the role grants below cover
`job_dependencies`, so it needs to exist first.

`quant_schedule` gets its own role split, deliberately separate from `quant_writer`/`quant_reader`
— `quant_scheduler` (full CRUD on `jobs`/`job_dependencies` — `SELECT`, `INSERT`, `UPDATE`,
`DELETE` — what `quant-schedule` (croicu/quant-data#68) authenticates as to create and manage a
work item's job graph) and `quant_worker` (read/update on `jobs`, read-only on `job_dependencies` —
what `quant-dispatch` itself authenticates as; `fetch_due_jobs`'s dependency-gating query joins
against `job_dependencies`, so `quant_worker` needs `SELECT` there too even though it never writes
to it, matching the "ships empty, real rows inserted by hand" precedent already set for
`dataset_inception`):

```sql
CREATE ROLE quant_scheduler LOGIN PASSWORD '...';
CREATE ROLE quant_worker LOGIN PASSWORD '...';

GRANT CONNECT ON DATABASE quant_schedule TO quant_scheduler, quant_worker;

GRANT SELECT, INSERT, UPDATE, DELETE ON jobs, job_dependencies TO quant_scheduler;
GRANT USAGE ON SEQUENCE jobs_job_id_seq TO quant_scheduler;

GRANT SELECT, UPDATE ON jobs TO quant_worker;
GRANT SELECT ON job_dependencies TO quant_worker;
```

No `INSERT`/sequence grant for `quant_worker` — nothing in `ScheduleDatabase` ever inserts a row.
No `quant_reader` grant on either role — `jobs`/`job_dependencies` are purely operational, never
read via `MarketData`.

Then point `quant-dispatch` at it via `settings.postgres.worker` (`settings.local.json`, same
`host`/`port`/`sshUser`/`sshKeyPath` as the rest of `settings.postgres` — but its own `dbname`/
`user`/`password`, unlike `archiver` above, since `quant_worker` is a distinct role from
whatever `settings.postgres.user` holds). `settings.postgres.worker`/`.scheduler` are named for the
role each one holds credentials for (`quant_worker`/`quant_scheduler`), not for the CLI that uses
them:

```json
{
  "settings": {
    "postgres": {
      "host": "localhost",
      "port": 5433,
      "user": "quant_writer",
      "password": "...",
      "dbname": "quant_data",
      "archiver": {
        "dbname": "quant_ingest"
      },
      "worker": {
        "dbname": "quant_schedule",
        "user": "quant_worker",
        "password": "..."
      },
      "scheduler": {
        "dbname": "quant_schedule",
        "user": "quant_scheduler",
        "password": "..."
      }
    }
  }
}
```

`settings.postgres.worker` is required to run `quant-dispatch` — unlike `archiver`, there's
no degraded-but-working mode, since `jobs` is `quant-dispatch`'s entire reason to exist; omitting it
makes `quant-dispatch` raise a clear `AppError` at startup rather than failing on the first query.
`settings.postgres.scheduler` is required to run `quant-schedule` (croicu/quant-data#68), same
treatment — omitting it raises a clear `AppError` at startup.

## Populating real data

`quant-ingest` fetches bars from Yahoo Finance over an inclusive date range and writes them into
the warehouse — the actual write path described above, run for real:

```bash
# Single ticker, single day (--end-date omitted defaults to --start-date)
quant-ingest --ticker AAPL --start-date 2026-01-15

# Single ticker, a date range
quant-ingest --ticker AAPL --start-date 2026-01-12 --end-date 2026-01-16

# Every ticker in settings.tickers (omit --ticker), one day
quant-ingest --start-date 2026-01-15

# Everything from settings (tickers + startDate/endDate) -- no flags at all
quant-ingest

# Catch up any partially-ingested trailing day (settings.catchUpLookbackDays)
quant-ingest --catch-up

# Walk every settings.tickers ticker one settings.backfillChunkDays chunk further back toward
# dataset_inception.inception_date -- requires that table to have a row first (see the "Granting
# quant_writer access to new tables" section above); run repeatedly (e.g. on a schedule) to walk
# the whole configured universe back over time.
quant-ingest --backfill
```

`psql` (above) remains useful for direct verification/debugging, but isn't the only way to
interact with the schema anymore.
