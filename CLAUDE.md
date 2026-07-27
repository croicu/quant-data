# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Mission

This repo owns the market-data warehouse: the PostgreSQL schema, migrations, and the ingest/read
infrastructure for 1-minute OHLCV bars. It's the shared data layer behind
[`quant-scratch`](https://github.com/croicu/quant-scratch) (experiments and CLI tools) and
`quant-research` (published findings) — centralizing storage so experiments don't each re-fetch
and re-store the same historical bars from their own data providers.

The star schema, migrations, and docs shipped first; a Python read client
(`MarketDataProvider`/`PostgresDatabase`) and a first-cut `quant-ingest` CLI (pulling from Yahoo
Finance) followed — see `docs/ARCHITECTURE.md`. Remaining open work (a dedicated read-only
`quant_reader` DB role, swapping in IBKR as the real intraday source, batching/scheduling) is
tracked in `tasks/postgres_client_and_dimensions.md`.

## Template Sync

- **Source**: [croicu/tpl-py](https://github.com/croicu/tpl-py)
- **Synced to**: 2026-07-26T00:00:00Z (set at instantiation time, since `tpl-py`'s own
  `ADDENDUM.md` had no entries yet as of that date)

This repo is either `tpl-py` itself or was generated from it. `tpl-py`'s `ADDENDUM.md` is a
curated, timestamped log of changes meant for downstream instances (new/changed rules,
base-module fixes, obsoleted patterns) — routine housekeeping doesn't get an entry. Which
protocol below applies depends on which repo you're in.

### Reading the addendum (applies in an instance)

1. Fetch `tpl-py`'s `ADDENDUM.md` over plain HTTPS (e.g. `WebFetch` against the raw content
   URL) — no `gh` CLI, no `git clone`, no persistent git remote required.
2. Compare each row's timestamp against this repo's `Synced to` value above.
3. For rows newer than that, fetch only that entry's individual file under `addendum/` (not the
   whole history) and decide whether/how to apply it here.
4. After applying (or deliberately skipping) everything newer, bump `Synced to` above to the
   latest entry's timestamp.

### Writing an addendum entry (applies only in `tpl-py` itself)

1. When making a change meant for downstream instances, add a new file under `addendum/`
   (filename prefixed with an ISO timestamp) describing what changed, why, and what an instance
   should do about it.
2. Append a row to `ADDENDUM.md`'s table (timestamp, title, filename).

## Cross-Repo Coordination

This repo has a real data-contract relationship with
[croicu/quant-scratch](https://github.com/croicu/quant-scratch) (experiments and CLI tools):
quant-data is the producer, quant-scratch (and any future consumer repos) is the client.
Coordination happens via GitHub issues, not a changelog file — unlike the template-propagation
model in "Template Sync" above, which suits a one-to-many static template but not an active
two-way contract between two independently-evolving repos.

**Placement rule**: a cross-repo issue lives in whichever repo owns the actionable follow-up, not
necessarily where the need originated:
- **quant-data ships a breaking or notable change** (schema migration, changed contract, a
  deprecated column) → open an issue in **quant-scratch** (and any other consumer repo) announcing
  it, since that's where the reacting work happens.
- **quant-scratch needs something from quant-data** (new ticker/column support, a schema change, a
  bug in returned data) → open an issue in **quant-data** requesting it, since that's where the
  building work happens.

**Conventions**:
- Label every cross-repo issue `cross-repo` (alongside the normal `status:*` label) so these
  threads are filterable apart from each repo's own internal work.
- Always cross-link: the issue body must reference the originating repo/issue/commit (e.g. "See
  croicu/quant-scratch#7" or "Requested by croicu/quant-scratch's day-chart work"), so either side
  is navigable from the other.
- Use `gh issue create --repo <owner>/<repo>` to open a cross-repo issue directly from wherever
  you're working — no need to switch working directories first.

**Future: multiple consumers.** Not built yet — there's only one consumer (`quant-scratch`) as of
2026-07-26, and building this ahead of a second real consumer would be speculative. When a second
consumer repo actually arrives, extend the convention above with:

- **Consumer registry**: keep a short list of known consumer repos right here in `CLAUDE.md` (just
  `quant-scratch` today), so a breaking change has a concrete list to notify rather than relying on
  memory of who depends on this schema.
- **Fan-out on breaking changes**: instead of one announcement issue in one consumer repo, open one
  in *every* registered consumer, each cross-linking back to the same migration/change.
- **Rollout tracking (the actual "handshake")**: open a tracking issue in `quant-data` itself with
  a checklist linking to each per-consumer issue. Close the tracker only once every consumer issue
  is closed — that's the confirmation that each client actually adapted, not just that they were
  notified. Useful gate before doing something irreversible on the schema side, e.g. dropping a
  column once superseded.

## Collaboration rules

- Before implementing any feature or non-trivial change, ask clarifying questions until the intent is unambiguous.
- If anything is unclear or could be interpreted multiple ways, ask — do not assume and implement.
- This repo touches a real (if small) production concern — a Postgres box reachable over SSH — once
  ingest work starts. Treat schema migrations and any write path as harder to reverse than typical
  application code: confirm before running a migration or write against the real database, not
  just a local/test one.

### Task workflow

Tasks are tracked as GitHub issues in this repo, status via labels: `status:brainstorm`,
`status:implementation`, `status:testing`, `status:ready-to-submit`. There is no `status:done`
label — reaching Done means closing the issue. (These labels don't exist on a freshly-created
repo — create them with `gh label create` before the first task needs one.)

Tasks come in two flavors, which affects whether step 1 below applies:

- **Planned tasks** — a `tasks/<task-name>.md` already exists (or is being freshly authored as a
  deliverable in its own right) before implementation discussion starts, e.g. dropped in by the
  user ahead of time. Follow all stages below, starting with Brainstorm.
- **Ad-hoc tasks** — the task emerges organically from conversation (no pre-existing or
  deliberately-authored task file). Skip straight to Implementation: no `tasks/<task-name>.md` gets
  created at all, just open the GitHub issue directly once the discussion has converged. Don't
  create a task file first just to immediately trim/delete it — that's churn, not documentation.

For any non-trivial feature or change, follow these stages:

1. **Brainstorm** (planned tasks only) — copy `tasks/new_task.md` to `tasks/<task-name>.md` with the problem statement; update it with conclusions as the design discussion progresses. This is scratch space for live back-and-forth — an issue isn't required at this stage, but a lightweight tracking issue labeled `status:brainstorm` can be opened for backlog visibility if wanted; either way, `tasks/<task-name>.md` (not the issue) stays the working document until the design converges.
2. **Implementation** — open a GitHub issue (`gh issue create`) with the converged problem statement + conclusions as the body, labeled `status:implementation`. Write the code. For a planned task, `tasks/<task-name>.md` is no longer the source of truth once the issue exists — trim it to a one-line pointer at the issue (or delete it) rather than maintaining both. For an ad-hoc task, there's no file to trim — the issue was the first artifact.
3. **Testing** — relabel the issue `status:testing`. Verify correctness; post test results and any open issues as an issue comment.
4. **Ready to Submit** — relabel `status:ready-to-submit`. Run lint + tests; confirm docs are up to date; post a closing summary comment.
5. **Done** — close the issue after merge. For a planned task, delete `tasks/<task-name>.md` once the issue is closed — the issue (body + comments) is the sole source of truth from that point on, so there's no reason to keep a stale duplicate on disk. (Only applies when a real issue holds the full history; a Done task with no issue keeps its local file.) Ad-hoc tasks have nothing to delete.

## Before committing

Run these before every commit:

```bash
ruff format src/ tests/
ruff check src/ tests/
pytest
```

## Documentation rule

After any change that affects the public interface, CLI, database schema, or file formats, update
the relevant docs:

- `CLAUDE.md` — commands, architecture notes
- `docs/ARCHITECTURE.md` — modules, data flow, contracts
- `docs/PROTOCOL.md` — CLI signature, file format schemas
- `docs/SCHEMA.md` — table definitions, indexes, query examples
- `docs/DATABASE.md` — installation, connection, migration instructions

## Commands

```bash
# Install (editable, with dev deps)
pip install -e ".[dev]"

# Run
quant-data

# Lint
ruff check src/ tests/
ruff format src/ tests/

# Test
pytest
pytest tests/unit/test_foo.py::test_bar   # single test
```

## Database

- **Engine**: PostgreSQL, hosted on a remote Ubuntu box, reachable over SSH.
- **Migrations**: plain numbered SQL files under `migrations/` (`001_init_schema.sql`, ...),
  applied manually via `psql` for now — no migration-runner tool yet. See `docs/SETUP.md` for the
  exact commands and `docs/DATABASE.md` for connection/tunnel setup.
- **Schema**: a four-table star schema (`dim_ticker`, `dim_date`, `dim_time`,
  `fact_market_data_1min`) — see `docs/SCHEMA.md` for the full design and rationale.
- **Roles**: `quant_data` (schema owner, used for migrations/admin), `quant_writer` (ingest's
  read/write role, password-protected), and `quant_reader` (`SELECT`-only, trust-authenticated —
  no DB password; the SSH tunnel/key is the actual gate for `127.0.0.1`/`::1` connections).
  Single-writer, many-reader is fully enforced at the DB-privilege level now — verified directly:
  a write attempt through `quant_reader` gets a real Postgres `permission denied`, not just a
  missing Python method. External consumers should use `client.market_data.MarketData` (defaults
  to `quant_reader`), not `PostgresDatabase` directly.

## Architecture conventions

1. Internal processing uses strongly typed dataclasses.
2. **Package layout**: `src/` follows `quant-scratch`'s convention — one folder per app (e.g. `src/ingest/` for the write-side CLI, `src/client/` for the read-side library external consumers import), plus two repo-wide non-app packages: `src/shared/` (cross-app infra: `diagnostics.py`, `errors.py`, `settings.py`, `postgres.py`, `providers/`) and `src/defs/` (`protocols.py` + `contracts.py`). `defs/` is kept separate from `shared/` on purpose: contracts are the specification, not owned by whichever package happens to implement them. There is no top-level `quant_data` package or generic `quant-data` console script — each app gets its own named script where it has one (e.g. `quant-ingest`; `client/` is a plain importable library with no script), matching `quant-scratch` having no generic script either. Cross-package imports are absolute (`from shared.diagnostics import ...`, `from defs.contracts import ...`); same-package imports stay relative (`from .errors import ...`). setuptools' src-layout automatic discovery picks up every package under `src/` with no extra `[tool.setuptools]` config needed, as long as each has `__init__.py`.
3. `protocols.py` (in `src/defs/`) contains persisted/shared data contracts — pure data only, no behavior. Behavior that operates on protocol types belongs in a dedicated entity/service layer, not on the protocol classes themselves.
4. `contracts.py` (in `src/defs/`) contains runtime behavioral interfaces (`Protocol` classes for things like workers/executors), not data.
5. Unit tests (`tests/unit/`) must run offline. Integration tests (`tests/integration/`), if the project has them, may hit real external services — that's a deliberate scope split, not a loophole in rule 4. Note `pytest.ini`'s `testpaths = tests` runs both by default, so adding an integration suite means accepting network calls in the default `pytest` invocation unless you also gate it behind a marker.
6. Prefer explicit, readable Python over clever abstractions.
7. Prefer constructor/parameter injection over monkeypatching this project's own module internals in tests — e.g. a component that talks to the outside world (network, filesystem, clock, database) should take that dependency as an argument, defaulting to the real implementation, so tests can pass a fake object instead of patching a function inside the module under test. Monkeypatching is still the right tool for faking a *third-party* library's own internals (e.g. a DB driver class you don't own) — the distinction is whether the thing being faked is your code or someone else's.

## Logging

- **Use `Logger`** (`from shared.diagnostics import Logger`) — not bare `print()`.
- **All features log success and errors** — no silent success, no swallowed errors.
- **Message length by severity**:
  - **Success (info)** — short: feature started, feature ended.
  - **Recoverable issues (warning)** — medium: enough context to understand what went wrong and why it was non-fatal.
  - **Errors (error/fatal)** — detailed: full context needed to reproduce and diagnose.
- **Level guide**:
  - `Logger.info` — normal notable events (start, end, success, counts)
  - `Logger.warning` — recoverable problems (retries, skipped items)
  - `Logger.error` / `Logger.fatal` — unrecoverable failures
- **Categories** — every `Logger` method takes an optional `category: str = "general"`, filterable via `settings.json`'s `logCategories` (an open string, not a closed enum — `diagnostics.py` only defines `CATEGORY_GENERAL` as a starting constant). Console output is `[LEVEL][category] message`. **Effective default depends on `debug`**: if `settings.json`'s `logCategories` is left empty/absent, `debug: false` resolves it to `["general"]` (only `general` shown), `debug: true` resolves it to `[]` (unfiltered, show everything); an explicit non-empty `logCategories` always overrides this regardless of `debug`. **`excludedCategories`** is a complementary deny-list, only in effect when the resolved `logCategories` is `[]` (the true unfiltered `debug: true` state) — inert against an explicit non-empty `logCategories` or the plain `debug: false` default.

## Coding Style

- **Protocols are pure data** — `protocols.py` holds dataclasses only. No methods, no logic. Behavior lives in a separate entity/service layer.
- **Explicit over brief** — if two implementations are equivalent, choose the one that is easier to read and debug, even if it is longer.
- **No list/dict/set comprehensions** — use explicit `for` loops. Comprehensions obscure control flow and make multi-step logic harder to follow.
- **No lambdas** — use named functions or plain `for` loops. Lambdas hide intent and cannot be stepped through in a debugger.
- **Import count as SRP signal** — more than 5–10 imports in a file is a hint that the file may be doing too much. Not a hard rule, but worth pausing to consider whether responsibilities should be split.
- **Don't build a DI factory/composition-root prematurely** — the same wait-for-evidence judgment as the import-count signal applies to DI wiring. A function picking up its second or third injectable parameter (e.g. `main(argv, provider=None, settings_path=None)`) is not yet a smell; extracting a shared factory/helper from a single data point risks guessing at the wrong abstraction shape. Wait for real duplication — a second call site needing the same wiring, or a parameter list that's genuinely grown unwieldy — before extracting one.
- **Ticker normalization**: always store and compare tickers uppercased (matching `quant-scratch`'s convention). The schema enforces this with a `CHECK` constraint on `dim_ticker.ticker` — Python code should still uppercase at the boundary rather than relying on the database to catch it.

## New Task
- **File**: [Scheduled jobs](tasks/scheduled_jobs.md)
- **Status**: Brainstorm, postponed (see issue #3) — deprioritized, not actively worked
- **Key Context**: How recurring background/maintenance work (DB maintenance now, ingest scheduling
  later) gets tracked without baking `CroicuWS1`-specific detail into this public repo — leaning
  toward a `jobs` table living in the database itself (data, not committed code), still in
  investigation.

## Pending Tasks
- **File**: [Postgres client](tasks/postgres_client_and_dimensions.md)
- **Status**: Brainstorm — first cut, batch/settings, and read-client increments shipped (closed
  issues #4, #5, #6), remaining scope open
- **Key Context**: The read client (`client.market_data.MarketData`, `quant_reader` role) and a
  Yahoo-Finance-backed `quant-ingest` CLI are built, with settings-driven ticker lists/date ranges
  and multi-ticker/multi-day batching (see `docs/ARCHITECTURE.md`). Left open: swapping in IBKR as
  the real intraday source, and recurring/unattended scheduling.

## Completed Tasks
- **Repository bootstrap** (schema, migrations, docs, Python scaffold) — seeded from
  `quant-scratch`'s `tasks/bootstrap_quant_data.md` and `tasks/database_layer.md`.
- **Postgres read/write client + Yahoo Finance ingest CLI (first cut)** — closed issue #4.
  `defs.protocols.OHLCV`/`contracts.MarketDataProvider`/`IntraDayProvider`,
  `shared.postgres.PostgresDatabase`, `shared.providers.yf.YahooFinanceIntraDay`,
  `ingest.cli`'s `quant-ingest` command, `quant_writer` DB role, and
  `migrations/002_add_incomplete_flag.sql`. Verified against the real database (953 real AAPL bars
  written and read back) plus 10 mocked unit tests.
- **quant-ingest batch mode: settings-driven tickers + date ranges** — closed issue #5.
  `Settings.tickers`/`Settings.start_date`/`Settings.end_date` (all optional CLI overrides,
  `settings.local.json`-only for the personal watchlist/date default), multi-ticker/multi-day
  batching tolerant of individual failures, a corrected `incomplete` heuristic (literal-zero
  volume, not just `NaN`), and a real `Settings.load()` `local_path` isolation bug fix. Verified
  against the real database (6 tickers ingested, fully settings-driven invocation with zero CLI
  args). `pytest` grew to 28 passed.
- **MarketData read-only client + quant_reader role** — closed issue #6.
  `client.market_data.MarketData` (thin, read-only, `quant_reader`-by-default wrapper
  around `PostgresDatabase` — what `quant-scratch` actually imports), `shared.errors.
  DateOutOfRangeError`, and the `quant_reader` role created for real (trust-authenticated,
  `SELECT`-only). Verified directly against the real database: reads work with no password over
  the tunnel, and a write attempt through `quant_reader` gets a real Postgres `permission denied`.
  `pytest` grew to 32 passed.
- **Provision PostgreSQL on CroicuWS1 + populate dimensions** — ad-hoc infra task, see the closed
  GitHub issue. PostgreSQL 16 installed (data directory on the `storage` zpool), `quant_data`
  role/database created, `001_init_schema` applied, `dim_time`/`dim_date` populated
  (2000-01-01–2030-12-31).
