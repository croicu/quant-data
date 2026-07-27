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

## Connecting over an SSH tunnel

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
connects as `quant_reader` by default (`SELECT`-only, trust-authenticated, no password needed over
the tunnel):

```python
from datetime import date
from quant_data import MarketData, create_postgres_provider

provider = create_postgres_provider(host="localhost", port=5433, dbname="quant_data")
with MarketData(provider) as client:
    bars = client.fetch_bars("AAPL", start_date=date(2026, 1, 15), end_date=date(2026, 1, 15))
    print(len(bars))
```

(`MarketData` is context-manageable — `close()` runs automatically on exit. Calling `close()`
manually, without `with`, still works too.)

`quant_writer` (password-protected, read/write) is for `ingest` only — see
`quant_data_internal.shared.postgres.PostgresDatabase` if you need the concrete read/write implementation directly
(e.g. writing your own ingest tooling), with connection details from
`settings.json`/`settings.local.json`'s `postgres` section (see `docs/ARCHITECTURE.md` for the
full shape).

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
```

`psql` (above) remains useful for direct verification/debugging, but isn't the only way to
interact with the schema anymore.
