# SETUP.md

First-time setup checklist for the `quant-data` warehouse. See `docs/DATABASE.md` for the detail
behind each step.

## Prerequisites

- [ ] PostgreSQL installed and running on the Ubuntu box
- [ ] SSH key-based access to that box already working (`ssh <user>@<host>` connects without a
      password prompt)

## Steps

1. **Create the database and a role**, on the box:
   ```bash
   sudo -u postgres createuser --interactive --pwprompt
   sudo -u postgres createdb -O <role> quant_data
   ```

2. **Open an SSH tunnel** from your machine (see `docs/DATABASE.md` for a systemd-service version
   if you want it to persist):
   ```bash
   ssh -N -L 5433:localhost:5432 <ssh_user>@<ubuntu_host>
   ```

3. **Apply the schema migration**, through the tunnel:
   ```bash
   psql -h localhost -p 5433 -U <role> -d quant_data -f migrations/001_init_schema.sql
   ```

4. **Verify the schema exists**:
   ```bash
   psql -h localhost -p 5433 -U <role> -d quant_data -c '\dt'
   ```
   Expect `schema_migrations`, `dim_ticker`, `dim_date`, `dim_time`, `fact_market_data_1min`.

5. **Populate `dim_time` and `dim_date`** — one-time bulk population, see the SQL snippets in
   `docs/DATABASE.md`'s "Populating dimension tables" section.

## Next steps

This bootstrap stops here — schema and dimensions only, no data loaded and no ingest tool yet.
Follow-up work (reading/writing bars from Python, populating `fact_market_data_1min` from a real
provider) is tracked in `tasks/postgres_client_and_dimensions.md`.
