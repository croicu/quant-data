# SETUP.md

First-time setup checklist for the `quant-data` warehouse. See `docs/DATABASE.md` for the detail
behind each step.

Two scenarios are covered:
- **Database-only** (below) — Postgres already exists on its own box, `quant-data` runs from
  elsewhere (e.g. a Windows dev machine over an SSH tunnel).
- **Full environment on one Linux box** (further down) — app, both Postgres databases, and IB
  Gateway all running locally on the same machine, nothing tunneled. First done for CroicuWS2,
  `tasks/linux_migration.md`.

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

2. **Open an SSH tunnel** from your machine, for the `psql` steps below (see `docs/DATABASE.md`
   for a systemd-service version if you want it to persist). The Python client
   (`MarketData`/`create_postgres_provider`, `quant-ingest`) doesn't need this — it opens its own
   tunnel automatically when given `ssh_user`/`ssh_key_path`, see `docs/DATABASE.md`'s "Connection
   testing from Python":
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

## Next steps (database-only scenario)

This checklist stops at schema + dimensions — see `docs/DATABASE.md`'s "Populating real data"
section for `quant-ingest`, and `docs/ARCHITECTURE.md` for the full read/write client design.

---

## Full environment setup on a new Linux box

Standing up `quant-data` entirely on one machine — the app, `quant_data`, `quant_ingest`, and a
headless IB Gateway — so nothing depends on a Windows machine, an SSH tunnel, or a separately-hosted
Postgres server. Everything below was actually run once, on CroicuWS2.

### Prerequisites

- [ ] Ubuntu 24.04 (or similar) with SSH key-based access already working
- [ ] PostgreSQL 16 installed and running locally
- [ ] Outbound internet access (package installs, IBKR's servers, GitHub)

### 1. Clone the repo

```bash
git clone https://github.com/croicu/quant-data.git ~/quant-data
```

### 2. Node.js 22+ and Claude Code CLI (optional — for running Claude Code natively on the box)

Ubuntu 24.04's default apt repo only carries Node 18, below Claude Code's minimum (22+) — use
NodeSource instead of the distro package:

```bash
curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash -
sudo apt-get install -y nodejs
sudo npm install -g @anthropic-ai/claude-code
```

Bootstrap the project-scoped memory directory by actually running a session — login followed by a
real message and `/exit`, not just login-then-exit immediately; an empty session doesn't create it:

```bash
cd ~/quant-data
claude
# send at least one real message, then /exit
```

This creates `~/.claude/projects/<slug>/memory/`, where `<slug>` is the absolute working directory
with every `/` replaced by `-` (e.g. `/home/alex/quant-data` → `-home-alex-quant-data`). To carry
over an existing memory directory from another machine: zip it, copy it to this path, unzip — then
verify inside a session with `/memory`, or better, ask it something only the transferred memory
would know, to confirm it's not silently starting fresh.

### 3. Postgres: create and migrate both databases

```bash
psql -U <role> -d postgres -c "CREATE DATABASE quant_data;"
psql -U <role> -d postgres -c "CREATE DATABASE quant_ingest;"

for f in migrations/*.sql; do psql -U <role> -d quant_data -v ON_ERROR_STOP=1 -f "$f"; done
psql -U <role> -d quant_ingest -v ON_ERROR_STOP=1 -f migrations/quant_ingest/001_init_provider_source_archive.sql
```

Populate dimensions (`docs/DATABASE.md`'s "Populating dimension tables" has the exact SQL) — skip
this and every `quant-ingest` write fails with `No dim_date row for <date>`.

If connecting over TCP (`127.0.0.1`) rather than the local Unix socket, the role needs a real
password — Postgres's default `pg_hba.conf` only trusts the local socket via `peer`, not TCP:

```sql
ALTER ROLE <role> WITH PASSWORD '...';
```

### 4. Python venv

```bash
sudo apt-get install -y python3-venv python3-pip   # ensurepip is often missing from the base image
cd ~/quant-data
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

### 5. `settings.local.json`, pointing at localhost

```json
{
  "settings": {
    "postgres": {
      "host": "127.0.0.1",
      "port": 5432,
      "user": "<role>",
      "password": "...",
      "dbname": "quant_data",
      "archiveDbname": "quant_ingest"
    },
    "providers": ["ibkr"]
  }
}
```

No `sshUser`/`sshKeyPath` needed — Postgres is local now, not tunneled.

### 6. IB Gateway, headless, via IBC + Xvfb + systemd

Install Xvfb (a virtual framebuffer — Gateway is a GUI app and needs *some* display, even headless):

```bash
sudo apt-get install -y xvfb
```

Download IB Gateway's standalone installer and run it unattended, **to the default `~/Jts` path**
— IBC's own docs warn against a custom install directory, and it's not just a style preference:
several of IBC's path-derivation and file-management steps assume `~/Jts` specifically.

```bash
curl -sS -o ibgateway-installer.sh 'https://download2.interactivebrokers.com/installers/ibgateway/stable-standalone/ibgateway-stable-standalone-linux-x64.sh'
chmod +x ibgateway-installer.sh
./ibgateway-installer.sh -q -dir ~/Jts
```

Download IBC (IBController) from its GitHub releases (`IbcAlpha/IBC`) and extract it, e.g. to `~/ibc`.

**Known gotcha — installer layout mismatch.** IBKR's "standalone" installer lays files out flatly
(`~/Jts/jars`), but IBC's `gatewaystart.sh`/`ibcstart.sh` expect the classic multi-version layout,
`~/Jts/ibgateway/<version>/jars` (plus the sibling `ibgateway.vmoptions` and `.install4j/`). Fix by
actually *moving* the files into place — don't symlink: IBC's own "rename the launcher to prevent a
bypass restart" safety step mutates files inside that directory, which breaks a symlinked structure
the next time it runs.

```bash
mkdir -p ~/Jts/ibgateway/<version>
mv ~/Jts/jars ~/Jts/ibgateway/<version>/jars
mv ~/Jts/.install4j ~/Jts/ibgateway/<version>/.install4j
mv ~/Jts/ibgateway.vmoptions ~/Jts/ibgateway/<version>/ibgateway.vmoptions
mv ~/Jts/ibgateway ~/Jts/ibgateway/<version>/ibgateway   # the launcher binary occupies the name the directory needs -- move it in, don't delete it
```

(`<version>` is IBC's "major version" number — e.g. Gateway 10.45 → `1045`. Read it off
`Help > About IB Gateway`, or off the install's own `.install4j/i4jparams.conf` `majorVersion`
value.)

Configure `~/ibc/config.ini` and `~/ibc/gatewaystart.sh`:
- `TWS_PATH=~/Jts`, `IBC_PATH=~/ibc`, `TWS_MAJOR_VRSN=<version>`, `TRADING_MODE=paper`
- Leave `TWSUSERID`/`TWSPASSWORD` blank in `gatewaystart.sh` if credentials should be supplied by a
  person directly on the box rather than through an agent or committed anywhere.
- `TradingMode=paper` in `config.ini` too
- `AcceptIncomingConnectionAction=accept` and `ExistingSessionDetectedAction=primary` — so a real
  API client can connect and this session isn't silently displaced, with no dialog for anyone to
  click (there usually isn't anyone watching).
- **`AcceptNonBrokerageAccountWarning=yes`** — critical for paper accounts specifically. Logging
  into a paper account shows a "this is not a brokerage account" dialog that blocks *all* API
  connections (`Error 10141: Paper trading disclaimer must first be accepted for API connection`)
  until it's dismissed. IBC's own doc comment says this defaults to `yes`, but the downloaded
  template had it set to `no` — verify explicitly rather than trusting the documented default.

systemd units — Xvfb first, Gateway depends on it, launched with `-inline` (the script's default
mode opens an `xterm` window and backgrounds itself, which breaks `Type=simple`'s process tracking;
`-inline` `exec`s the launch script directly instead):

```ini
# /etc/systemd/system/xvfb-ibgateway.service
[Unit]
Description=Virtual framebuffer for IB Gateway

[Service]
ExecStart=/usr/bin/Xvfb :1 -screen 0 1024x768x16
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

```ini
# /etc/systemd/system/ibgateway.service
[Unit]
Description=IB Gateway (via IBC), paper trading
After=network.target xvfb-ibgateway.service
Requires=xvfb-ibgateway.service

[Service]
Type=simple
User=<user>
Environment=DISPLAY=:1
ExecStart=/home/<user>/ibc/gatewaystart.sh -inline
Restart=on-failure
RestartSec=30

[Install]
WantedBy=multi-user.target
```

**Known gotcha — paper/live session conflict.** `Error 162: Trading TWS session is connected from a
different IP address` on historical-data requests, even with only one Gateway session running, if
the *same underlying login* is active anywhere else — including the **live** account's Client
Portal in a browser, since a paper account shares its parent login's market-data session with the
live account. Log out of every other session (paper or live) tied to that login before testing —
Client Portal counts, TWS/Gateway elsewhere counts, a mobile app counts.

**Verify it's actually working**, not just that the port accepts a connection — confirm a real
fetch through quant-data's own provider:

```bash
source .venv/bin/activate
quant-ingest --ticker SPY --start-date <a recent weekday>
```

### 7. Connect via VS Code Remote-SSH

Open a new VS Code window, Remote-SSH into the box, open the cloned `~/quant-data` folder. Claude
Code run from an integrated terminal in that window executes natively on Linux from that point on
— no further path/OS juggling.
