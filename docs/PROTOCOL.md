# PROTOCOL.md

CLI signature and file format schemas for `quant-data`.

## CLI

<!-- Command name, arguments, flags, exit codes. -->

### `quant-data`

- Usage: `quant-data [--debug]`
- Placeholder only — logs a start/end message and exits `0`. No read/write commands yet; those
  arrive with the follow-up in `tasks/postgres_client_and_dimensions.md`.
- `--debug` overrides `settings.json`'s `debug` flag.
- Exit codes: `0` success, `1` settings load failure, `2` argument parsing error (argparse's
  default behavior on missing/bad args).

## File formats

<!-- Schemas for any files this project reads or writes. -->

This repo's primary "file format" is the database schema itself — see `docs/SCHEMA.md` for the
four-table star schema (`dim_ticker`, `dim_date`, `dim_time`, `fact_market_data_1min`) and
`migrations/001_init_schema.sql` for the exact DDL.

### Migration files (`migrations/*.sql`)

Plain numbered SQL files (`NNN_description.sql`), applied manually via `psql` in order — see
`docs/DATABASE.md`. Each migration wraps its DDL in a single transaction and records itself in the
`schema_migrations` table on success.
