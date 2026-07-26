# DATABASE.md

Installing PostgreSQL, connecting to it, and populating dimension tables for `quant-data`.

## Installing PostgreSQL on the Ubuntu box

```bash
sudo apt update
sudo apt install -y postgresql postgresql-contrib
sudo systemctl enable --now postgresql

# Create a role and database for this project
sudo -u postgres createuser --interactive --pwprompt   # follow the prompts for a new role
sudo -u postgres createdb -O <role> quant_scratch
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
psql -h localhost -p 5433 -U <role> -d quant_scratch
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
psql -h localhost -p 5433 -U <role> -d quant_scratch -f migrations/001_init_schema.sql
```

See `docs/SETUP.md` for the full step-by-step first-time setup checklist.

## Verifying the schema

```bash
psql -h localhost -p 5433 -U <role> -d quant_scratch -c '\dt'
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

## Connection testing from Python (future)

No Python database client ships with this bootstrap — see `tasks/postgres_client_and_dimensions.md`
for the follow-up that adds `quant_data`'s `PostgresDatabase`/`MarketDataProvider`. Once that
exists, a connection test will look roughly like:

```python
from quant_data.postgres import PostgresDatabase  # not implemented yet

db = PostgresDatabase(host="localhost", port=5433, user="<role>", password="...", dbname="quant_scratch")
bars = db.fetch_bars("AAPL", start_date="2026-01-15", end_date="2026-01-15")
print(len(bars))
```

Until then, `psql` (above) is the only supported way to verify connectivity and query the schema.
