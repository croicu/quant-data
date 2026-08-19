# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Mission

This repo owns the market-data warehouse: the PostgreSQL schema, migrations, and the ingest/read
infrastructure for 1-minute OHLCV bars. It's the shared data layer behind
[`quant-scratch`](https://github.com/croicu/quant-scratch) (experiments and CLI tools) and
`quant-research` (published findings) — centralizing storage so experiments don't each re-fetch
and re-store the same historical bars from their own data providers.

The star schema, migrations, and docs shipped first; a Python read client
(`quant_data.MarketData`, re-exported at the package top level from
`quant_data.client.market_data.MarketData`; agnostic of the concrete backend, depending only on
`quant_data._internal.contracts.MarketDataProvider` — built today via
`quant_data.create_postgres_provider`, backed by
`quant_data._internal.shared.postgres.PostgresDatabase`)
and a `quant-ingest` CLI (pulling from Yahoo Finance) followed, along with the `quant_writer`/
`quant_reader` DB roles enforcing single-writer/many-reader at the privilege level — see
`docs/ARCHITECTURE.md`. `quant-ingest --catch-up` covers the narrowest slice of recurring
ingest (re-fetching a trailing window so a partial day gets caught up), but actually triggering it
on a schedule (cron/systemd) is still a manual, box-specific setup step outside this repo, and the
broader scheduled-jobs mechanism (issue #3) remains postponed. Swapping in IBKR as the real
intraday source remains open too; both become their own tasks if/when `quant-scratch` actually
needs them.

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

**Does this change need a cross-repo issue at all?** The `quant_data`/`_internal` public/private
split, plus `src/ingest/` being outside `quant_data` entirely (see "Architecture conventions"
below), exists specifically to make this cheap to answer by touched-folder alone, not by
re-deriving it from scratch each time:
- Touched only `src/quant_data/_internal/` or `src/ingest/`? No consumer import path or constructor
  signature could have changed — no cross-repo issue needed, *unless* the change alters externally
  observable behavior through the public surface anyway (e.g. a bug fix in
  `quant_data._internal.shared.postgres.PostgresDatabase.fetch_bars` that changes what
  `MarketData.fetch_bars` returns — still "a bug in returned data" per the rule above, so still
  announce it).
- Touched `src/quant_data/`? Default to assuming a cross-repo issue is needed, then confirm:
  anything reachable via `from quant_data import ...` is the actual contract, so a change to
  `__init__.py`'s re-exports, `protocols.py`'s `OHLCV`, or a public class's constructor/method
  signature (`MarketData`, `create_postgres_provider`) needs one; a change confined to
  `quant_data.client`'s internals that doesn't touch what's re-exported (e.g. a private helper
  method) doesn't.

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
`status:implementation`, `status:testing`, `status:ready-to-submit`, `status:ready-for-integration`.
There is no `status:done` label — reaching Done means closing the issue. (These labels don't exist
on a freshly-created repo — create them with `gh label create` before the first task needs one.)

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
3. **Testing** — relabel the issue `status:testing`. Verify correctness; post test results and any open issues as an issue comment. **For a `cross-repo` issue that originated from a consumer repo's own testing/diagnosis** (e.g. a bug first noticed running `quant-scratch`'s `day-chart`), quant-data's own verification — even a live check against the real database — confirms the fix works in isolation, but isn't the same as confirming the originally reported symptom is actually resolved: that requires the consumer to pull the updated `quant-data` code and re-test in its own context. Say so explicitly in the comment rather than implying it's fully confirmed.
4. **Ready to Submit** — relabel `status:ready-to-submit`. Run lint + tests; confirm docs are up to date; post a summary comment. This is as far as *this* repo's own work can confirm the issue.
5. **Ready for Integration** (`cross-repo` issues that need consumer-side verification only —
   see "Who closes an issue" below for which issues that is) — once the fix is actually merged/
   pushed to `main`, relabel `status:ready-for-integration` instead of leaving it at
   `status:ready-to-submit`. This is the label that actually names the gap: quant-data's own
   checks (lint/tests/docs, even a live check against the real database) can confirm the fix works
   in isolation, but not that the originally reported symptom is resolved — that needs the
   consumer (e.g. `quant-scratch`) to pull the updated `quant-data` and re-test in its own context.
   An issue with no such downstream dependency (a same-repo bug, nothing cross-repo) skips this
   stage entirely — `status:ready-to-submit` is already its terminal pre-close state.
   **The gate is real, verifiable output existing for the consumer to check, not just code having
   merged** — issue #21 (IBKR provider) was relabeled `status:ready-for-integration` when the
   provider code itself landed, but `fact_market_data_1min` was still empty at that point, so there
   was nothing yet for `quant-scratch` to actually pull and verify. A later, otherwise-unrelated
   commit (`f5b192f`, `quant-reconcile` promoting real `ibkr`-sourced rows into
   `fact_market_data_1min` for the first time) is what made the label meaningfully true. When
   relabeling, ask whether the consumer would actually see something different by pulling now —
   not just whether the relevant code merged.
6. **Done** — close the issue after merge. For a planned task, delete `tasks/<task-name>.md` once the issue is closed — the issue (body + comments) is the sole source of truth from that point on, so there's no reason to keep a stale duplicate on disk. (Only applies when a real issue holds the full history; a Done task with no issue keeps its local file.) Ad-hoc tasks have nothing to delete.

**Who closes an issue**: applies to issues opened "in the family" — by the repo owner themselves
(directly, or via a cross-repo issue from one of their own other repos like `quant-scratch`) —
which is the normal case today, since there are no external contributors yet. In that case,
whoever opened it is the one who closes it, not automatically whoever did the implementation work:
leave it open (at `status:ready-for-integration` once pushed, if it needed that stage; otherwise
`status:ready-to-submit`) and say so; don't close it, and don't use GitHub's auto-closing
commit-message keywords (`Closes #N`, `Fixes #N`, `Resolves #N`) for it, since those close on push
regardless of who's supposed to have that call — use a non-closing reference instead (`Ref #N`,
`Part of #N`, `Addresses #N`). This matters most for `cross-repo` issues diagnosed from a
consumer's own testing: the opener is the one positioned to actually verify the fix in that
original context, so closing is their call, not a mechanical side
effect of merging. The one exception even within the family: an issue Claude opened itself mid-task
(e.g. a `status:implementation` issue opened while executing a planned/ad-hoc task in this same
session) can be closed directly, since Claude is the opener there.

**If an issue ever comes from a genuine external contributor** (not the repo owner or one of their
own other repos), this whole rule doesn't apply — follow normal GitHub OSS etiquette instead
(auto-close via a merged PR's `Closes #N` is fine, maintainer discretion applies). Revisit this
section if/when that actually happens; it's not a case worth designing for speculatively before a
real external contributor shows up.

## Branching and PRs

`main` is branch-protected on GitHub (PR required, 0 required reviews, `enforce_admins: true`,
`lint-and-test` CI check required, no force-pushes/deletions) — set up 2026-08-13 to mirror
[`geo-places`](https://github.com/croicu/geo-places)'s setup. This means direct pushes to `main`
are rejected for everyone, including the repo owner, so all work (feature or ad-hoc) goes through
a branch:

1. Starting new work: create a feature branch off `main` (not on `main` directly).
2. Commit as usual — see "Before committing" below; the per-commit pause still applies on a
   feature branch exactly as it would have on `main`.
3. Once ready: push the branch and open a PR (`gh pr create`).
4. The user reviews and merges the PR themselves, then deletes the branch. Don't merge or delete
   the branch — that's the user's call, same spirit as "Who closes an issue" above.

This doesn't change the two-remotes workflow in "Git Remotes" below — `croicuws1` pushes still
happen from `main` after a PR merge, not from a feature branch.

## Before committing

Run these before every commit:

```bash
ruff format src/ tests/
ruff check src/ tests/
pytest
```

Then stop and let the user review the diff themselves (e.g. in VS Code's source control view) —
don't run `git commit` until they've explicitly confirmed, even if the change is already
implemented, tested, and the underlying issue looks done. Reviewing in git log after the fact
doesn't substitute for this.

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
  missing Python method. External consumers should use `quant_data.MarketData` with a provider
  from `quant_data.create_postgres_provider` (defaults to `quant_reader`), not `PostgresDatabase`
  directly.

## Data Pipeline Principles

**No information loss during the data processing stage — everything downstream of ingest must
stay re-creatable from `staging_market_data_1min` alone.** Ingest is the only stage that talks to
external providers; every later stage (`quant-reconcile`'s Tier 1-4 resolution,
`fact_market_data_1min`, `fact_reconciliation`/`fact_reconciliation_participant`,
`provider_pair_disagreement`) is a *derived* view of staging and must remain reproducible from it
without re-ingesting.

**Why**: surfaced concretely on 2026-08-07, investigating whether `quant-reconcile` could be
re-run against a "fresh" dataset without actually re-running `quant-ingest`. The existing lazy-purge
mechanism (`_promote_and_lazily_purge` / `purge_staging_bar`) already deletes a bar's per-provider
staging rows once resolved and no longer needed as a Tier-3 neighbor — so for the 6,939 bars where
the whistleblower was ever confirmed absent (croicu/quant-data#31) and a further ~41,913 bars where
only the winning provider's row survived, the original raw per-provider disagreement is already
gone. Moving `fact_market_data_1min` back into `staging_market_data_1min` could only reconstruct
the winner's own value (and only by assuming which provider that was), never a genuine
two-provider comparison — meaning `fact_reconciliation`'s own resolution can no longer actually be
re-derived from staging for those bars. That's a direct violation of this principle, and it was
only discovered because testing the outlier-detection recalibration needed a truly fresh run.

**How to apply**: any future change to what gets purged, archived, or deleted from
`staging_market_data_1min` (or any replacement for the lazy-purge mechanism) must be checked
against this rule before shipping — if deleting a row would make it impossible to regenerate
`fact_market_data_1min`/`fact_reconciliation` from staging alone, don't delete it outright; archive
it instead (e.g., a raw, append-only, provider-tagged log mirroring what staging captured before
any purge, distinct from staging's own prunable working copy). Reconciling `purge_staging_bar`'s
current behavior against this rule is itself a real follow-up — not yet scoped as its own task.

## Git Remotes

Two remotes exist for this repo, serving different purposes:

- **`origin`** (`https://github.com/croicu/quant-data.git`) — the public GitHub repo. `main`
  tracks `origin/main`; a plain `git push`/`git pull` targets this one.
- **`croicuws1`** (`ssh://alex@CroicuWS1/storage/Git/quant-data.git`) — a bare repo on the
  database box itself, referred to as "the local/secondary remote" in conversation. Not the
  tracked upstream, so it needs an explicit `git push croicuws1 main` — it does not receive a
  plain `git push` automatically. Its main use: keeping `~/quant-data` (the working checkout +
  venv on CroicuWS1, set up to run `quant-ingest`/`quant-reconcile` directly on the box without
  SSH-tunnel latency — see `docs/DATABASE.md`) in sync via `git pull` there, instead of the
  slower/error-prone `tar`-over-`ssh` workaround this repo used before code landing here was
  actually committed.

**After any local commit that should reach the box, push to both**:
```bash
git push origin main
git push croicuws1 main
```
If the second push is rejected as non-fast-forward, `croicuws1` has a commit `origin`/local
doesn't (see the `/local/` procedure below for the most common cause) — `git fetch croicuws1`
and inspect (`git log --oneline main..croicuws1/main`) before merging or force-pushing; never
force-push without confirming what would be discarded (see `.gitignore`'s `# Artifacts that go
to the secondary (non-GitHub) git remote only.` comment — anything under `/local/` only ever
exists tracked on `croicuws1`, so it's the one place a force-push could genuinely lose data
that isn't recoverable from GitHub).

**The `/local/` switch-to-local / push / switch-back procedure**: `/local/` is where
machine-specific or investigation-only artifacts (ad-hoc candlestick PNGs, CSV pulls from a
paid data source, one-off sync scripts — see `croicu/quant-data#28`-adjacent investigations
for an example) live when they're worth persisting *somewhere* but don't belong in the public
GitHub history. Normally gitignored (`/local/` in `.gitignore`). To actually push something
under `/local/` to the box:
1. **Switch to local**: remove the `/local/` line from `.gitignore` (temporarily).
2. **Push**: `git add local/ .gitignore`, commit, then `git push croicuws1 main` — deliberately
   *not* `git push origin main`, since this content is meant for the secondary remote only.
3. **Switch back**: restore the `/local/` line to `.gitignore`, `git rm -r --cached local/`
   (untracks without deleting the working-tree files), commit, then push that commit to *both*
   remotes so history stays in sync and `/local/` goes back to being ignored everywhere. This
   step is what keeps the cycle from leaving `croicuws1` permanently diverged — skipping it is
   exactly what caused a non-fast-forward rejection once already (commit `b35fe43`, merged back
   in and properly closed out in the commit right after this section was written).

## Architecture conventions

1. Internal processing uses strongly typed dataclasses.
2. **Package layout**: two top-level packages under `src/` (full rationale and history in `docs/ARCHITECTURE.md`'s Layout section):
   - `src/quant_data/` — the distribution's namespaced package: `__init__.py` (plain re-exports of `MarketData`, `OHLCV`, `LoggingSink`, `create_postgres_provider` — no laziness needed, see the circular-import note below), `protocols.py` (`OHLCV` plus the behavioral `LoggingSink` `Protocol`, **public**), `client/market_data.py` (`MarketData`, **public**, agnostic of the concrete backend — it only depends on `MarketDataProvider`), `client/postgres_provider.py` (`create_postgres_provider`, **public**, today's factory), and `_internal/` (**private**: `contracts.py` — behavioral `Protocol`s `MarketDataProvider`/`IntraDayProvider`/`ConnectionTransport` — and `shared/` — cross-app infra: `diagnostics.py`, `errors.py`, `settings.py`, `postgres.py`, `providers/`, `transports/`). Nothing under `_internal/` is exported by `quant_data`, and none of it should be imported directly by external consumers, even though Python doesn't stop you. Consumers (`quant-scratch`) should only ever write `from quant_data import MarketData, OHLCV, LoggingSink, create_postgres_provider`.
   - `src/ingest/` — the write-side CLI. Console script only (`quant-ingest`), no importable surface at all.

   `quant_data` needs its own distribution-specific namespace: **this deliberately does not mirror `quant-scratch`'s own flat top-level-package convention** (`defs`, `shared`, `day_chart`, ... with no common prefix) — that flat layout is fine for a repo that's never installed alongside anything else, but broke the moment `quant-data` was installed as a pip dependency *next to* `quant-scratch` in the same environment: both repos' top-level `defs`/`shared` packages shared the same import name, so whichever installed last silently shadowed the other's entirely (see croicu/quant-data#7, reproduced both installation orders). `_internal/` is *nested* under `quant_data`, not a second top-level package — nesting under `quant_data.` was already sufficient for collision safety (the private half never needed its own distribution-specific name; it just needed to not be bare/flat), and nesting also avoids a real circular-import failure mode a sibling top-level package doesn't (see the gotcha below). `ingest` has no known collision and no importable surface, so it doesn't need a prefix either way — it's a separate top-level package purely for the public/private split, not for collision safety.

   Cross-package imports are absolute with the full package prefix (`from quant_data._internal.shared.diagnostics import ...`, `from quant_data.protocols import ...`); same-package imports stay relative (`from .errors import ...`). setuptools' src-layout automatic discovery picks up every top-level package under `src/` (`quant_data`, `ingest`) with no extra `[tool.setuptools]` config needed, as long as each has `__init__.py`.

   **Circular-import gotcha (historical, resolved by nesting)**: `quant_data._internal.shared.postgres` needs `OHLCV` from `quant_data.protocols`, and `create_postgres_provider` needs `PostgresDatabase` from `quant_data._internal.shared.postgres` in turn — a two-way relationship between the public and private halves (see rule 8 below on keeping the dependency graph acyclic — this is the one case here where a `Protocol` alone couldn't fully remove the relationship, since `create_postgres_provider`'s whole job is bridging to the concrete `PostgresDatabase`). This used to be a real crash when `_internal` was a *separate top-level package* (`quant_data_internal`): eagerly importing at `quant_data/__init__.py`'s module scope meant `import quant_data_internal.shared.postgres` as the first import in a fresh process raised `ImportError: cannot import name 'PostgresDatabase' from partially initialized module`. Nesting `_internal` under `quant_data` fixed this *structurally*, not just by deferring it: importing a dotted path forces Python to fully finish the parent (`quant_data`) before even attempting the child segment, so the self-reference that caused the crash can't occur — verified directly by re-running the exact reproduction against the nested layout with a plain, eager `__init__.py`. No `__getattr__`/lazy-import workaround needed anymore; don't reintroduce one without a concrete reason, since it would be unnecessary cleverness at that point (rule 6).
3. `protocols.py` (in `quant_data`, the public half) contains public contracts: persisted/shared data (dataclasses, e.g. `OHLCV`) *and* behavioral `Protocol`s meant for a consumer to actually implement/inject (e.g. `LoggingSink` — see quant-data#20). The distinction from `contracts.py` isn't data-vs-behavior, it's public-vs-private: a behavioral `Protocol` belongs in `protocols.py` specifically when an external consumer is expected to supply their own implementation of it, not just when quant_data has some internal data type to describe. Behavior that merely *operates on* a data contract (as opposed to a `Protocol` a consumer implements) still belongs in a dedicated entity/service layer, not on the dataclass itself. Keep any behavioral `Protocol` placed here leaf-safe (rule 8) — default parameter values like a category string should be literals, not imports from `_internal`, even where `_internal` already defines the same constant.
4. `contracts.py` (in `quant_data._internal`, the private half) contains runtime behavioral interfaces (`Protocol` classes for things like workers/executors) that wire quant_data's *own* internals together — never imported by external consumers, unlike `protocols.py`'s behavioral `Protocol`s above.
5. Unit tests (`tests/unit/`) must run offline. Integration tests (`tests/integration/`), if the project has them, may hit real external services — that's a deliberate scope split, not a loophole in rule 4. Note `pytest.ini`'s `testpaths = tests` runs both by default, so adding an integration suite means accepting network calls in the default `pytest` invocation unless you also gate it behind a marker.
6. Prefer explicit, readable Python over clever abstractions.
7. Prefer constructor/parameter injection over monkeypatching this project's own module internals in tests — e.g. a component that talks to the outside world (network, filesystem, clock, database) should take that dependency as an argument, defaulting to the real implementation, so tests can pass a fake object instead of patching a function inside the module under test. Monkeypatching is still the right tool for faking a *third-party* library's own internals (e.g. a DB driver class you don't own) — the distinction is whether the thing being faked is your code or someone else's.
8. **The internal dependency graph must stay acyclic — break cycles with an abstract `Protocol`, not a runtime workaround.** If two concrete modules would otherwise need each other, introduce a `Protocol` in `quant_data._internal.contracts` (or `quant_data.protocols` if the public side needs to reference it) that one side depends on instead of the other's concrete type — see `MarketDataProvider`: `MarketData` depends on the protocol, not on `PostgresDatabase` concretely, which is exactly what keeps `quant_data.client.market_data` from ever needing anything from `quant_data._internal.shared.postgres` back. Check this mechanically, not by feel: list every module's static top-level `from quant_data...`/`from .` imports (`grep -E "^from (quant_data|\.)" -r src/`) and confirm no module is reachable from itself. As of this layout, this holds: `quant_data.protocols` is a genuine leaf (zero outgoing edges), so nothing depending on it — directly or transitively — can cycle back through it. A lazy import (`__getattr__`, deferred `import` inside a function) can *mask* a cycle by moving it from import-time to call-time — a legitimate fallback for cases a `Protocol` genuinely can't reach (e.g. a package needing to re-export a name without owning the dependency direction), but not the default fix, and not needed here anymore now that `_internal` is nested (see rule 2's gotcha). Reach for the `Protocol` first; reach for nesting under one namespaced package second; only reach for a lazy re-export if neither removes the cycle.

## Logging

- **Use `Logger`** (`from quant_data._internal.shared.diagnostics import Logger`) — not bare `print()`.
- **All features log success and errors** — no silent success, no swallowed errors.
- **Message length by severity**:
  - **Success (info)** — short: feature started, feature ended.
  - **Recoverable issues (warning)** — medium: enough context to understand what went wrong and why it was non-fatal.
  - **Errors (error/fatal)** — detailed: full context needed to reproduce and diagnose.
- **Level guide**:
  - `Logger.diagnostic` (`VERBOSE`) — one message per chunk of work, so a run's progress is visible and a hang is distinguishable from silence (e.g. `quant-ingest`'s `_ingest_one` logs one `VERBOSE` line per (ticker, date) pair it starts)
  - `Logger.info` — normal notable events (start, end, success, counts)
  - `Logger.warning` — recoverable problems (retries, skipped items)
  - `Logger.error` / `Logger.fatal` — unrecoverable failures
  - `Logger.perf(description, elapsed_seconds)` — duration markers for timing-sensitive spans
    (connection setup, query execution), always logged at `INFO` under a fixed category `perf`
    (`CATEGORY_PERF`, not the caller's choice — unlike every other `Logger` method). Message shape
    is `"duration: {elapsed:.3f}s - {description}"`, matching `quant-scratch`'s own `Logger.perf`
    convention so timing output reads the same across both repos. Added per
    [quant-data#19](https://github.com/croicu/quant-data/issues/19), where a ~130s stall on
    `PostgresDatabase.__init__`'s connect step was only diagnosable because `quant-scratch` had
    this instrumentation on its own side and quant-data didn't yet.
- **Categories** — every `Logger` method takes an optional `category: str = "general"`, filterable via `settings.json`'s `logCategories` (an open string, not a closed enum — `diagnostics.py` only defines `CATEGORY_GENERAL` as a starting constant). Console output is `[LEVEL][category] message`. **Effective default depends on whether `logLevel` is explicit** (see "Specific settings override generic ones on scope overlap" under Coding Style — this is that rule's origin case): if `settings.json`'s `logCategories` is left empty/absent, an explicit `logLevel` decides it outright — permissive (`verbose`/`info`/`warning`) resolves to `[]` (unfiltered), restrictive (`error`/`critical`) resolves to `["general"]` — regardless of `debug`. Only when `logLevel` is left at its implicit default does `debug` get consulted as the fallback (`debug: false` -> `["general"]`, `debug: true` -> `[]`), exactly as before. An explicit non-empty `logCategories` always overrides all of this outright. **`excludedCategories`** is a complementary deny-list, only in effect when the resolved `logCategories` is `[]` (the true unfiltered state) — inert against an explicit non-empty `logCategories` or the restrictive `["general"]` default.

## Coding Style

- **`protocols.py` holds public contracts, not implementations** — data (dataclasses, no methods) plus behavioral `Protocol`s meant for a consumer to implement/inject (e.g. `LoggingSink`). Either way, no concrete logic lives here — a dataclass has no behavior of its own (that lives in a separate entity/service layer), and a `Protocol`'s methods are signatures only (`...` bodies), never an implementation.
- **Explicit over brief** — if two implementations are equivalent, choose the one that is easier to read and debug, even if it is longer.
- **No list/dict/set comprehensions** — use explicit `for` loops. Comprehensions obscure control flow and make multi-step logic harder to follow.
- **No lambdas** — use named functions or plain `for` loops. Lambdas hide intent and cannot be stepped through in a debugger.
- **Import count as SRP signal** — more than 5–10 imports in a file is a hint that the file may be doing too much. Not a hard rule, but worth pausing to consider whether responsibilities should be split.
- **Don't build a DI factory/composition-root prematurely** — the same wait-for-evidence judgment as the import-count signal applies to DI wiring. A function picking up its second or third injectable parameter (e.g. `main(argv, provider=None, settings_path=None)`) is not yet a smell; extracting a shared factory/helper from a single data point risks guessing at the wrong abstraction shape. Wait for real duplication — a second call site needing the same wiring, or a parameter list that's genuinely grown unwieldy — before extracting one.
- **Ticker normalization**: always store and compare tickers uppercased (matching `quant-scratch`'s convention). The schema enforces this with a `CHECK` constraint on `dim_ticker.ticker` — Python code should still uppercase at the boundary rather than relying on the database to catch it.
- **Specific settings override generic ones on scope overlap** — when two configuration knobs can both influence the same outcome, the more specific/targeted one wins wherever they'd otherwise disagree, not the more generic/blanket one; the generic one only falls back into play when the specific one was left at its implicit default. Origin case: `settings.json`'s `logLevel` (a targeted verbosity control) vs. `debug` (a blanket flag) both used to influence the console log-category default, with `debug` winning outright — so setting `logLevel: "verbose"` alone did nothing, silently muted by `debug`'s separate default, which was surprising enough in practice to become this rule (see the Logging section above for the resulting behavior). Apply this whenever a new settings key's effect could overlap with an existing broader flag's — don't let a coarse toggle silently override an explicit, narrower setting the user actually configured.

## New Task
- **File**: [Ingestion layer spec](tasks/ingestion_layer_spec.md) (supersedes
  [Quote-bar enrichment ingest](tasks/quote_bar_ingest.md) — that file's problem statement/evidence
  still stand and are carried forward, but its open questions are answered here instead; kept on
  disk, not deleted, since tracking issue #60 below still points at it originally)
- **Status**: Landing-zone design converged and now real, live-verified code — not just schema.
  `provider_source_archive`/`archive_coverage` carry `method` in their key (coalesced migration,
  applied to CroicuWS2's `quant_ingest`). As of 2026-08-19, `ingest` itself fetches and archives
  three IBKR methods (`TRADES` + `BID_ASK` + `MIDPOINT`) by default — `IntraDayProvider.fetch_bars`
  gained an optional `method` param, `settings.ibkr.methods` (unset = all) restricts it, and both
  paths were live-verified against CroicuWS2's real IB Gateway (SPY 2026-08-17 archived TRADES+
  BID_ASK by default before MIDPOINT was added; SPY 2026-08-18 with `methods: ["TRADES"]` archived
  only that one; SPY 2026-08-13 archived all three, MIDPOINT confirmed real OHLC shape).
  `MIDPOINT` was added on an explicit collect-now-decide-later basis (repo owner's call — data is
  cheap to drop later via the existing `DELETE` grant, expensive to not have collected at all);
  `ADJUSTED_LAST` remains excluded, no concrete reason raised. `ruff`/`pytest` green (304 passed).
  Not yet committed, and no `status:implementation` GitHub issue opened for this slice specifically
  — tracking issue [croicu/quant-data#60](https://github.com/croicu/quant-data/issues/60)
  (`cross-repo`) is still labeled `status:brainstorm`; relabeling/opening the implementation issue
  and committing are both still the repo owner's call per this file's own workflow.
  `stage`/`fact_market_data_1min` actually consuming `BID_ASK`/`MIDPOINT` remain open — see
  `tasks/ingestion_layer_spec.md` §6/§7/§8 for full detail.
- **Key Context**: follow-on to `quant-scratch`'s prototype (croicu/quant-scratch#26/#27) validating
  that IBKR (`WAP`/trade-count from the existing `TRADES` call, plus a separate `BID_ASK` call) and
  Massive (`vw`/`n` free on the existing OHLCV response, no bid/ask on any tier below Stocks
  Advanced/Business) both expose usable per-minute enrichment fields beyond OHLCV. Follow-on to
  [croicu/quant-data#44](https://github.com/croicu/quant-data/issues/44) (Massive as a second OHLCV
  candidate), not a duplicate of it. A real, previously-unknown gap surfaced checking the spec's own
  open item against code: current IBKR ingestion ([ibkr.py:109-118](src/quant_data/_internal/shared/providers/ibkr.py#L109-L118))
  discards `ib_async`'s `BarData.average`/`.barCount` (WAP/trade-count) before archiving, so
  `provider_source_archive`'s "lossless, replayable" property does not actually hold for IBKR
  today — every historical IBKR row is affected, and reparsing WAP/trade-count for an already-
  archived day would need a genuine, pacing-limited re-fetch. Massive is unaffected (archive already
  stores the full raw JSON response). This fix is folded into #60's implementation rather than
  tracked separately.

- **File**: [Pipeline accuracy hardening](tasks/pipeline_accuracy_hardening.md) (supersedes
  [Per-ticker disagreement stats](tasks/per_ticker_disagreement.md) — that file's motivating
  evidence/ticker concentration data still stand and were carried forward, but its
  sample-count/graduation-threshold design was replaced)
- **Status**: Slice 1 (schema-only: `dim_field`, `provider_pair_disagreement` re-keyed to
  `(provider_id, ticker_id, field_id)`, `dataset_inception`) shipped and closed —
  [croicu/quant-data#28](https://github.com/croicu/quant-data/issues/28), see Completed Tasks
  below. Slice 2 (algorithm + CLI: per-field Tier 2/3 tolerance, per-ticker graduation at 2,000
  matched bars, `--backfill` + round-robin chunking, internal rate limiting, `yfinance` lazy-purge
  exemption) not yet started — its full converged design is carried in #28's body, ready to become
  its own `status:implementation` issue.
- **Key Context**: fixes two compounding pooling problems in `provider_pair_disagreement`, both
  traced to a single measured tolerance standing in for a population that isn't homogeneous —
  pooled across tickers (`DOG`'s true stuck rate 25.9% vs. `SPY`/`QQQ`/`DIA`'s ~0%, from the
  2026-08-03 full-dataset run) and pooled across fields (`yfinance` noise concentrates in
  `high`/`low` while `open`/`close` stay stable, so one `ohlc`-group tolerance let the noisy fields
  set the band for the stable ones).

- **File**: [--finalize targeted promotion](tasks/finalize_targeted_promotion.md)
- **Status**: Brainstorm, not converged — several open questions (date/time input format
  especially, given the UTC-vs-ET confusion earlier this session) need resolving before
  implementation.
- **Key Context**: prompted by reviewing the 3 pending `SPY` bars. An initial pass judged
  `yfinance`'s value correct for all three; a later look at candlestick charts plus the raw
  `staging_market_data_1min` rows **reversed that** — `ibkr` is internally consistent on all three
  days, while `yfinance` has one outlier extreme field per bar (alternating `L`/`H`/`L`), each
  landing suspiciously close to the *adjacent* day's closing price (see
  `tasks/finalize_targeted_promotion.md`'s Problem statement for the exact numbers). Further
  candlestick comparison across the full 3-day window (`ibkr`'s curve smooth throughout, `yfinance`'s
  has sporadic unexplained spikes with no `ibkr` counterpart, including more beyond just these 3
  bars) points at ordinary Yahoo/yfinance feed noise rather than a specific nameable bug — see the
  new `tasks/yahoo_data_sanitization.md` this spawned. So `ibkr` should actually win all three —
  currently the only way to promote that judgment is hand-writing SQL against three tables
  (`tasks/quant_reconcile.md`'s "Manual correction" section, "no dedicated tooling implied").
  Proposed: new `--finalize` arguments (ticker, date/time, field group, winner) for targeted
  single-bar promotion, always recorded as `resolution_path = 'manual_override'` regardless of
  which provider wins — the first *tooled* path for `yfinance` to ever reach
  `fact_market_data_1min`, correctly feeding `fact_reconciliation_participant`'s existing
  reputation tracking with no changes needed there.

- **File**: [DataBento stuck-bar verification](tasks/databento_stuck_bar_verification.md)
- **Status**: Brainstorm, not converged — several open questions (auto-tiebreaker vs.
  assistive-only, cost/budget, API access model, where a DataBento value would even live given
  `dim_provider.role`'s closed `candidate`/`whistleblower` `CHECK`) need resolving before
  implementation.
- **Key Context**: DV team proposal (2026-08-05) to make DataBento cross-checking of stuck bars
  an optional `quant-reconcile` flag, scoped only to the pending queue. Revisits
  `tasks/finalize_targeted_promotion.md`'s earlier explicit decision **not** to adopt DataBento as
  an ongoing/routine reference (paid source, one-off sanity check only) — narrower in scope
  (opt-in, stuck-bars-only) so may not actually conflict, but the tension needs to be resolved on
  purpose in the GitHub issue, not assumed away.

## Pending Tasks

- **Split `quant-ingest` into `ingest` (fetch + archive) and `stage` (archive → staging)** —
  [issue #56](https://github.com/croicu/quant-data/issues/56), `status:implementation`, ad-hoc task
  (opened directly, no `tasks/*.md`), picked up the same session it was proposed. #52 shipped
  `provider_source_archive` but explicitly deferred this exact split as a follow-on; picking it up
  meant resolving its three open questions first: **CLI shape** — `src/stage/` is a new top-level
  package (console script `quant-stage`, no importable surface, same convention as `src/ingest/`),
  not a flag on `quant-ingest`. **Parsing location** — moved entirely out of the provider classes
  and into `stage`; `ProviderFetchResult` dropped its `bars` field (see the #52 entry above),
  providers (`yfinance.py`/`ibkr.py`/`massive.py`) are now pure fetchers, and the actual
  `OHLCV`/data-quality logic (yfinance's NaN-or-zero-volume -> `INCOMPLETE` heuristic in particular)
  moved to new `stage/parsers/{yfinance,ibkr,massive}_parser.py` modules, dispatched by
  `stage/parsers/__init__.py`'s `parse_payload(provider, payload, ticker)`. yfinance's raw payload
  now preserves `NaN` as JSON `null` instead of coercing to `0.0` at fetch time — a small
  information-loss fix that fell out of the split, not a separately-scoped goal. **Rollout** —
  atomic: both processes landed in the same PR/branch (`split-ingest-stage`), since staging would
  get zero new data if `ingest`-only shipped first. `quant-ingest` now writes to `quant_ingest`
  only (no `quant_data` dependency at all — `_default_archive_writer_factory` raises if
  `archiveDbname` isn't configured, since there's nothing else for it to do); `quant-stage` reads
  `provider_source_archive` via a new `ProviderSourceArchiveReader` (picks the latest-`fetched_at`
  row per `(ticker, provider, trading_date)`, since the table has no uniqueness constraint on that
  key) and calls the same `write_staging_bars`/`record_ingestion_coverage` `quant-ingest` used to
  call directly — `ingestion_coverage`'s write path (#31) moved to `stage` accordingly, since it now
  describes `stage`'s own write, not `ingest`'s. A real behavior change: an archive-write failure
  used to be a tolerated secondary problem (staging still got its normal chance); now archiving is
  `ingest`'s entire job, so it's treated exactly like a fetch failure. **`--backfill` deliberately
  dropped, not carried over** — its old bookkeeping (`dataset_inception`/earliest-covered-date)
  spanned both databases in a way the split didn't resolve on its own (see `docs/ARCHITECTURE.md`'s
  `ingest` section for the specific reasoning); flagged as a follow-up rather than silently designed
  mid-split. `pyproject.toml` gained a `quant-stage = "stage.cli:main"` entry; `pip install -e
  ".[dev]"` re-run to pick it up. `docs/ARCHITECTURE.md`/`docs/PROTOCOL.md` updated for the new
  two-process shape. Live-verified end-to-end against CroicuWS2's local database (freshly
  clean-slate reset the same session): `quant-ingest --ticker SPY --start-date 2026-08-14` archived
  960 IBKR bars to `quant_ingest`, `quant-stage` with the same arguments then parsed and staged all
  960 into `quant_data.staging_market_data_1min`, `data_quality` all `accepted`, spot-checked
  against the raw values. `ruff`/`pytest` both green (287 tests, including the two live integration
  probes against real yfinance/IB Gateway on this box). Not yet committed — sitting on branch
  `split-ingest-stage`, awaiting the repo owner's diff review per this file's "Before committing"
  rule.

  **Follow-on fix, same session**: a real ingest of `SPY` `2026-08-02`–`2026-08-16` surfaced a
  genuine IBKR quirk — `reqHistoricalData` doesn't fail for a weekend `target_date`, it silently
  returns the *prior trading day's* full session instead (confirmed live: requesting
  `2026-08-15`/`2026-08-16` both archived `2026-08-14`'s bars again under a different
  `trading_date` key; requesting `2026-08-02`, the boundary Sunday, pulled in `2026-07-31`'s
  session — a day outside the requested range entirely). Since `stage` writes staging rows keyed
  by each bar's own real timestamp (not the requested date), this was harmless to data
  correctness — just wasted archive rows and redundant staging round trips. Fixed per explicit
  direction: **keep `ingest` fetching/archiving weekends as before** (no change there), but
  **`stage` now skips weekend dates outright** (`_is_weekend`, plain `weekday() >= 5` check) before
  ever consulting the archive — not counted toward its `succeeded`/`failed` totals either.
  Deliberately a calendar check, not a session-time heuristic, so it doesn't reintroduce the kind
  of US-equities-market-hours assumption `--catch-up`'s own design note already avoids baking into
  a schema with no session concept. Live-reverified: re-running `quant-stage` over the same range
  logged 5 weekend skips and left `staging_market_data_1min` unchanged (still 11 distinct days,
  10,560 bars for `SPY`) — confirming the fix eliminates the redundant work without disturbing
  already-correct data.

- **Immutable provider-fetch archive in a new `quant_ingest` database** —
  [issue #52](https://github.com/croicu/quant-data/issues/52), `status:implementation`. A separate
  database (not a table/schema inside `quant_data`) holding the immutable, append-only record of
  every provider fetch — motivated by ingestion being slow/costly and by `market_data_archive`
  (#35) only ever capturing a *candidate's* staging row, only *once purged*, never the whistleblower
  or the raw fetch itself. Deliberately separate from `quant_data`: Postgres has no cross-database
  foreign keys, so this repo's own routine clean-slate `DROP DATABASE quant_data` testing can never
  touch it structurally (concretely motivated by that same testing having swept up
  `market_data_archive` in a "clean slate" `TRUNCATE` list once already). `provider_source_archive`
  (ticker/provider/trading_date as plain checked text/date, `fetch_version`, `payload_kind` —
  `raw_api_response` for Massive's genuine raw JSON vs. `parsed_bars` for yfinance/IBKR, which have
  no raw payload this repo's code ever sees — `payload JSONB`) was originally immutable at the
  DB-privilege level (`quant_writer` gets `INSERT` + sequence `USAGE` only, no `UPDATE`/`DELETE`) —
  **update, 2026-08-17: `DELETE` was additionally granted** to `quant_writer` on
  `provider_source_archive`, at the repo owner's explicit request, for manual row cleanup, after
  being told this removes the DB-level guarantee that a compromised or buggy write path cannot
  delete archived data. A deliberate, informed relaxation, not an oversight.
  `ProviderSourceArchiveWriter.record_fetch` itself still only ever `INSERT`s; `UPDATE` remains
  ungranted (a row can be removed, never edited in place). See `docs/DATABASE.md`.
  `archive_coverage` is the archive-side equivalent of `ingestion_coverage`/#31, keyed by
  `(ticker, provider, fetch_version)` instead of just `(ticker, provider)`, so a version bump
  doesn't silently extend an old range. `IntraDayProvider.fetch_bars` now returns
  `ProviderFetchResult` (bars + payload + payload_kind) instead of a bare `list[OHLCV]` —
  **update, 2026-08-17: `bars` was dropped by #56** (see that entry below), so this is now
  `payload` + `payload_kind` only — and gained
  a `FETCH_VERSION: str` class attribute each provider bumps by hand when its own request
  construction changes. `ProviderSourceArchiveWriter` (new, not a `PostgresDatabase` method — no
  star-schema dependency at all) writes both tables in one transaction, called from `_ingest_one`
  immediately after a fetch succeeds and before the staging write, so even a bug in this repo's own
  parsing/staging code can't lose what a provider already returned. Purely additive: an unconfigured
  `settings.postgres.archiveDbname` (the default) disables archiving entirely, `quant-ingest` still
  writes to staging exactly as before. Deliberately excludes splitting `quant-ingest` into two
  decoupled "ingest"/"stage" processes (discussed, explicitly deferred to its own future issue) —
  this issue only ships the `quant_ingest` database and archiving as an additive step alongside
  today's existing staging-write path. Implemented, unit-tested; not yet live-verified against
  CroicuWS1 or committed.

- **Massive provider integration** — [issue #44](https://github.com/croicu/quant-data/issues/44),
  `status:implementation`. Adds Massive (formerly Polygon.io) as a second `candidate` alongside
  `ibkr`, the first time `fact_market_data_1min` would see a genuine two-candidate mixture. Design
  converged through several rounds (posted as issue comments, no task file kept) — settled provider
  naming/API approach/credentials/rate-limit strategy, a whistleblower-validity-gate fix for two
  real bugs a second candidate would expose in `src/reconcile/algorithm.py`'s Tier 1 (judging
  agreement against an outlier-rejected whistleblower value; a coverage regression when the
  whistleblower is confirmed-absent), and a graduation-key-granularity fix in `reconcile/cli.py`
  (`graduated_ticker_ids` is currently derived per-ticker only, which would permanently lock
  `massive` out of competing on any already-graduated ticker like `SPY` — the rollout plan's first
  target). Not yet implemented; see the issue's comment thread for the full converged design and
  required regression tests before code changes start.

- **Volume/noise correlation vs. the earlier DOG investigation — still not reconciled.** The
  per-bar volume regression used to calibrate `materiality_floor` (croicu/quant-data#40, closed —
  see Completed Tasks) **directly conflicts with the DOG stuck-rate investigation below (marked
  ⚠), which ruled out volume as an explanation on a different, larger sample.** Shipping the
  calibration didn't resolve this tension, it just used the correlation pragmatically (and even
  then, only partially — see #40's Completed Tasks entry for where it held up and where it
  didn't). Worth its own investigation (larger sample, control for price level properly, check
  `SH`/`RWM`/`IWM` once they have enough pending bars to measure) before either finding is treated
  as settled.

- **Fix ~130s SSH-tunnel connect stall; add `Logger.perf()` timing markers** — issue #19, opened by
  the repo owner from `quant-scratch`-side testing. `status:ready-for-integration`, fix pushed to
  `main` as `cd424a0` — **left open**, per this file's "Who closes an issue" rule:
  the opener verifies (once `quant-scratch` syncs to the new `quant-data` and confirms `day-chart`
  is actually fast) and closes it themselves, not automatically on merge.
  `SshTunnelTransport.open()` returned the bare hostname `"localhost"`, which `psycopg`/libpq
  resolves as dual-stack — with `SSHTunnelForwarder` bound to `0.0.0.0`, this fell back from an
  unreachable IPv6 loopback to IPv4 with a ~130s internal timeout instead of connecting immediately
  (diagnosed on the `quant-scratch` side via ad-hoc probe scripts, isolated to specifically the
  `psycopg.connect()` call — not the tunnel handshake, not a raw socket connect). Fixed by binding
  `local_bind_address=("127.0.0.1", 0)` explicitly and returning that literal address; also
  normalized any effective host of `"localhost"` to `"127.0.0.1"` in `PostgresDatabase.__init__`
  itself as defense-in-depth, since `DirectTransport`/any caller could hand back the same ambiguous
  hostname (`docs/DATABASE.md`'s own documented example did). Added `Logger.perf(description,
  elapsed_seconds)` (fixed `perf` category, same message shape as `quant-scratch`'s own
  `Logger.perf`) around `transport.open()`, `psycopg.connect()`, and each `fetch_bars`/`write_bars`
  call — this instrumentation is exactly what made the stall diagnosable in the first place.
  Verified live against CroicuWS1 (quant-data's own side only): 130s -> 1.4s for tunnel + connect.

- **Injectable `LoggingSink` so a host application can see quant-data's internal logging** —
  issue #20 (split from #19), opened by the repo owner. `status:ready-for-integration`, fix pushed
  to `main` as `cd424a0` — **left open**, same reason as #19 above:
  `quant-scratch` needs to actually wire up its own `Logger` via the new `logger=` param before the
  opener can confirm the unified stream works end-to-end. quant-data's `Logger` was entirely
  private, so its own internal log calls (e.g. the `Logger.perf()` markers #19 just added) were
  invisible to any consumer regardless of the consumer's own `settings.json` — flipping on `perf`
  category in `quant-scratch` showed only `quant-scratch`'s own markers. New public `Protocol`,
  `quant_data.protocols.LoggingSink` (mirrors the private `DiagnosticsLogSink`'s method surface
  exactly, so any `tpl-py`-descended repo's own existing `Logger` already satisfies it structurally
  with zero host-side changes). `create_postgres_provider`/`PostgresDatabase`/`MarketData` all
  gained an optional `logger: LoggingSink` parameter, defaulting to quant-data's own private
  `Logger` *class* itself (its methods are all `@staticmethod`s, so this behaves identically to the
  direct static calls it replaced internally) — additive, not breaking. This also redefines
  `protocols.py`'s scope: public contracts generally (data *or* behavioral-for-injection), not
  "dataclasses only" — see Architecture conventions rules 3/4 above. Verified live against
  CroicuWS1 with a custom logger object (not quant-data's own `Logger`), confirming every internal
  log call landed there instead.


- **File**: [Scheduled jobs](tasks/scheduled_jobs.md)
- **Status**: Brainstorm, postponed (see issue #3) — deprioritized, not actively worked
- **Key Context**: How recurring background/maintenance work (DB maintenance now, ingest scheduling
  later) gets tracked without baking `CroicuWS1`-specific detail into this public repo — leaning
  toward a `jobs` table living in the database itself (data, not committed code), still in
  investigation.

- **File**: [Ingest error classification](tasks/ingest_error_classification.md)
- **Status**: Brainstorm, postponed (see issue #13) — deprioritized, not actively worked
- **Key Context**: `quant-ingest`'s exit code can't currently distinguish an expected miss
  (weekend/holiday, surfaced by a real `--catch-up` run) from a genuine problem (bad ticker,
  fetch/write failure), which blocks eventually wiring it up to alerting. Interim step already
  shipped: Yahoo-Finance-sourced fetch failures are tagged with their own `yfinance` log category,
  separate from Postgres write failures — but the exit code itself is deliberately unchanged
  until the expected-vs-unexpected design converges.

- **File**: [Inverse-pair cross-check](tasks/inverse_pair_cross_check.md)
- **Status**: Brainstorm, postponed — deprioritized, not actively worked
- **Key Context**: `tasks/per_ticker_disagreement.md`'s per-ticker tolerance fixes false positives
  but risks masking real DOG-specific data errors by training DOG's own checker on DOG's own
  (currently unexplained, 25.9% stuck) history. `DOG`/`SH`/`PSQ` are inverse ETFs that track
  intraday tightly to -1× their long counterpart (`DIA`/`SPY`/`QQQ`, which already agree between
  providers essentially perfectly) — a trusted third reference for validating which provider is
  right, without needing `tasks/index_composite_check.md`'s full constituent-weight data. Explicitly
  postponed: for signal-research (not yet live trading), a signal in `QQQ` exists equally in `PSQ`
  sign-flipped, so the inverse tickers' own data quality isn't currently load-bearing — a known,
  accepted tradeoff, not a solved problem. `tasks/per_ticker_disagreement.md` proceeds without this
  as a prerequisite.

## Completed Tasks
- **Auto-manage the SSH tunnel via a `ConnectionTransport` abstraction** — closed issue #17.
  `psycopg`/libpq has no SSH transport of its own, so on-prem hosting structurally needs a tunnel —
  but that's specific to *today's* Ubuntu-box hosting choice, not an architectural requirement (a
  cloud-hosted Postgres needs no tunnel at all). New `ConnectionTransport` protocol
  (`quant_data._internal.contracts`) with `DirectTransport` and `SshTunnelTransport`
  (`sshtunnel`-backed) implementations under a new `shared/transports/` package keeps
  `PostgresDatabase` itself transport-agnostic — same pattern as `MarketDataProvider`. Additive to
  `PostgresSettings`/`create_postgres_provider` (optional `ssh_user`/`ssh_key_path`), not breaking.
  Along the way, fixed a real `sshtunnel`/`paramiko` incompatibility (`sshtunnel` 0.4.0
  unconditionally references the `paramiko.DSSKey` attribute a newer `paramiko` removed) via a
  small compatibility shim, rather than downgrading paramiko to an EOL version with since-patched
  CVEs. Verified live against CroicuWS1 (real tunnel, real fetch via `quant_reader`). Announced to
  `quant-scratch` via [croicu/quant-scratch#10](https://github.com/croicu/quant-scratch/issues/10)
  (additive, so informational/opt-in rather than a forced migration).
- **Nest all packages under `quant_data.*`** — fix done for issue #7 (bug), reopened pending
  verification from `quant-scratch`'s actual integration (croicu/quant-scratch#7) before closing.
  `src/defs/`, `src/shared/`,
  `src/ingest/`, `src/client/` moved to `src/quant_data/{defs,shared,ingest,client}/`; every
  cross-package import, `pyproject.toml`'s console script, and `.vscode/launch.json` updated
  accordingly. Fixes a real collision: quant-data's and quant-scratch's flat top-level `defs`/
  `shared` packages shared the same import name, so installing both into one environment (exactly
  what consuming quant-data as a pip dependency requires) made whichever installed last silently
  shadow the other's entirely. Verified by actually reproducing the two-repo collision in a
  scratch venv (broken before the fix, clean after) — not just re-running quant-data's own test
  suite in isolation.
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
  `pytest` grew to 32 passed. Announced to `quant-scratch` via
  [croicu/quant-scratch#7](https://github.com/croicu/quant-scratch/issues/7).
  **Postgres client + ingest is now closed out** (`tasks/postgres_client_and_dimensions.md`
  deleted, issues #4/#5/#6 are the source of truth) — IBKR-as-real-source and
  recurring/unattended scheduling were deliberately not carried forward as open items; they'll
  become their own tasks if/when `quant-scratch` actually needs them, rather than being
  speculatively tracked here.
- **Provision PostgreSQL on CroicuWS1 + populate dimensions** — ad-hoc infra task, see the closed
  GitHub issue. PostgreSQL 16 installed (data directory on the `storage` zpool), `quant_data`
  role/database created, `001_init_schema` applied, `dim_time`/`dim_date` populated
  (2000-01-01–2030-12-31).
- **Fix `fact_market_data_1min.timestamp` corruption from an unpinned session TimeZone** — closed
  issue #9 (raised by `quant-scratch`, see `docs/SCHEMA.md`'s `timestamp` row). Root cause:
  `PostgresDatabase` bound tz-aware UTC `datetime`s without pinning the connection's session
  `TimeZone`, so Postgres implicitly cast them `timestamptz -> timestamp` using the session's
  local zone (`America/Los_Angeles` on CroicuWS1), silently shifting every stored raw `timestamp`
  by that offset — 100% of rows across every ticker/date ingested to date, confirmed against the
  real database, not just the handful of anomalous-volume bars that surfaced it. Fixed by passing
  `options="-c TimeZone=UTC"` on connect; verified against a live `--catch-up`-style ingest
  (0/4,264 new rows mismatched). All 13,212 pre-existing historical rows backfilled from their
  independently-correct `dim_date`/`dim_time` keys and re-verified — 0/13,212 mismatched.
  Announced fixed to `quant-scratch` via
  [croicu/quant-scratch#9](https://github.com/croicu/quant-scratch/issues/9).
- **`quant-ingest --catch-up`** — closed issue #12. Re-fetches the trailing
  `settings.catchUpLookbackDays` days (default 7, excluding today) for every `settings.tickers`
  watchlist entry, relying on `write_bars`'s upsert to make re-ingesting an already-complete day a
  no-op — no gap-*detection* heuristic, since a session-close cutoff would bake US-equities
  market-hours assumptions into a schema that deliberately has no session concept. First concrete
  job out of the postponed scheduled-jobs brainstorm (issue #3) — deliberately the narrowest
  slice: no jobs table, no in-DB scheduling mechanism, just a CLI flag. Verified against a real
  multi-day, multi-ticker CroicuWS1 run (25,378 total rows, zero timestamp mismatches, weekend
  dates correctly absent).
- **Rename `yf` → `yfinance`; capture yfinance's own console noise; verbose per-chunk heartbeat**
  — closed issue #14. `providers/yf.py` → `providers/yfinance.py`, `CATEGORY_YF`/`"yf"` →
  `CATEGORY_YFINANCE`/`"yfinance"` throughout, to match the actual library's name. New
  `YFinanceLoggingAdapter` (`providers/yfinance_logging.py`) redirects yfinance's own
  `logging.getLogger('yfinance')` output (previously printing straight to stderr, e.g.
  `"possibly delisted"`, unfiltered and inconsistent with the rest of `quant-ingest`'s logging)
  into `Logger`, classified by regex pattern with a `WARNING` default for anything unrecognized —
  deliberately extensible, since only one yfinance message has actually been observed so far.
  Separately, `_ingest_one` now logs one `VERBOSE` "starting TICKER on DATE" line per (ticker,
  date) chunk, so a long `write_bars` run (many round trips per bar) is distinguishable from a
  hang. Follow-up opened for remaining yfinance noise beyond this one logger name: issue #15
  (brainstorm, not yet investigated).
- **Settings: explicit `logLevel` overrides `debug` for the log-category default** — closed issue
  #16. `Settings.load` used to resolve the console log-category default from `debug` alone,
  completely independent of `logLevel` — so setting `logLevel: "verbose"` without also flipping
  `debug: true` did nothing, silently muted. Now an explicit `logLevel` wins outright over `debug`
  in either direction whenever they'd disagree; `debug` only applies as a fallback when `logLevel`
  was left at its implicit default. New general convention recorded under Coding Style: "Specific
  settings override generic ones on scope overlap." Backported into
  [croicu/tpl-py](https://github.com/croicu/tpl-py) (the template this repo was generated from)
  via its addendum protocol, since the same bug exists in every repo generated from that template.
- **`quant-reconcile`: automatic tier resolution, `--finalize`, and a pending-manual-resolution
  queue** — closed issue #25 (commit `f5b192f`). Schema slice (`dim_provider.role`,
  `dim_field_group`, `fact_reconciliation`/`fact_reconciliation_participant`,
  `provider_pair_disagreement`) closed earlier as issue #24. `src/reconcile/algorithm.py`'s pure
  tier logic (completeness / agreement / boundary-fix, Welford's-algorithm running variance) plus
  `src/reconcile/cli.py`'s orchestration promote agreeing bars from `staging_market_data_1min` into
  `fact_market_data_1min`; `quant-ingest` writes only to staging (as of #22), so `quant-reconcile`
  is the only path anything reaches fact. Three refinements made while live-testing against the
  real database, all shipped in the same commit:
  - **Volume removed as an independently-reconciled field group** (`tasks/volume_reconciliation.md`,
    `migrations/005_remove_volume_field_group.sql`) — rides along with whichever provider wins
    `ohlc` instead of its own Tier 1-4 comparison, since it's a relative confidence signal
    downstream, not a value needing cross-provider corroboration, and `yfinance` has no pre-market
    volume at all.
  - **Seeding-lag fix**: the automatic pass repeats internally until a full pass resolves nothing
    new (provably terminating), reaching the real convergence floor in one invocation instead of
    requiring several manual re-runs.
  - **Lazy purge**: `promote_bar_to_fact`/`purge_staging_bar` split into separate calls — a
    resolved bar's staging rows are kept until neither adjacent minute is still unresolved, so a
    future run's boundary-fix check doesn't lose neighbor data the instant a bar promotes.
  - **`fact_pending_manual_resolution`** (`migrations/006_add_pending_manual_resolution.sql`) —
    same "presence of a row is the status" convention as `fact_reconciliation`. Plain
    `quant-reconcile` runs skip anything already flagged pending entirely instead of re-fetching/
    re-evaluating it every run; `--finalize` processes only the pending queue (no longer a superset
    of the plain pass). Fixes a real, compounding re-evaluation cost under a realistic
    daily-reconcile/weekly-finalize cadence. `run_reconciliation` dispatches to
    `_run_automatic_pass`/`_run_finalize_pass`.

  Live-verified at increasing scale against the real database: small samples (09:30-09:39 ET and
  05:00-05:09 ET windows — corrected from an earlier UTC/ET mislabeling), then the full 50,318-row
  dataset (20,595 resolved, 622 genuinely stuck — 50% via `completeness`, 45% `agreement`, 4.5%
  `boundary_fix`), then the pending-queue mechanism itself live-flagging that real backlog (`DOG`
  499/`PSQ` 66/`SH` 54/`SPY` 3, confirmed skipped by a follow-up run). Investigating why `DOG`
  disagreed at a 25.9% stuck rate (vs. `PSQ`/`SH`'s ~1.8%, `QQQ`/`DIA`/`SPY`'s ~0%) ruled out
  ticker-average volume, per-bar volume, and price level as explanations **(⚠ needs
  re-verification — see the volume/noise correlation finding under Pending Tasks below, which
  directly conflicts with this on a different, smaller sample; not yet reconciled)**, and spawned two
  follow-up tasks: `tasks/per_ticker_disagreement.md` (give `provider_pair_disagreement` a
  `ticker_id` dimension) and `tasks/inverse_pair_cross_check.md` (explicitly postponed — use
  `DOG`/`SH`/`PSQ`'s already-reliable long counterpart as a third-reference anomaly flag on
  `ibkr`). A third follow-up, `tasks/finalize_targeted_promotion.md` (CLI-tooled single-bar manual
  correction), was prompted by reviewing 3 pending `SPY` bars by hand.
- **`MarketData.fetch_pending_resolution_bars`** — closed issues #26 and #27 (both ad-hoc, opened
  and closed by Claude mid-task per this file's "Who closes an issue" exception; commits `9bed431`
  and `7b4423b`). New public `quant_data.protocols.PendingResolutionBar` (`field_group`,
  `provider`, `role: ProviderRole`, `bar: OHLCV`), new public `ProviderRole(Enum)`
  (`CANDIDATE`/`WHISTLEBLOWER`, mirroring `dim_provider.role`'s `CHECK` constraint — modeled as an
  `Enum` rather than a plain `str` since it's a genuinely closed set, matching
  `_internal.shared.diagnostics.TelemetryLevel`'s precedent, the one other closed-set string column
  in this codebase; #27 was a same-session follow-up fixing this after `provider`-only made the
  whistleblower unidentifiable in the returned list), plus `MarketDataProvider`/`PostgresDatabase`/
  `MarketData`'s `fetch_pending_resolution_bars(ticker, start_date, end_date)`.
  `fact_pending_manual_resolution` itself holds no `OHLCV` columns, only the key marking a (bar,
  field group) as still disputed, so the method joins it against `staging_market_data_1min` (and
  `dim_provider`, for `role`) to return one entry per (bar, field group, provider) still in
  dispute — surfacing the actual disagreement and which side is the reference, rather than just
  that a bar is stuck. Deliberately exposed on the public, `quant_reader`-backed surface (external
  consumers, not just internal `--finalize` tooling) — the first public method exposing anything
  from the reconciliation domain, so `docs/ARCHITECTURE.md`'s "Contracts" section (previously
  claiming `fetch_bars` was the *only* external contract) was updated to match. Required a new
  `quant_reader` grant on `staging_market_data_1min`/`fact_pending_manual_resolution`/
  `dim_field_group`/`dim_provider` (`docs/DATABASE.md`'s "Granting quant_reader access to new
  tables") — role/grant setup isn't tracked in `migrations/`, so this was applied by hand by the
  repo owner directly on CroicuWS1, not run by Claude (paste-and-run convention for privileged DB
  ops); #27 needed no additional grant (`dim_provider` was already covered). Live-verified
  end-to-end against the real database at both stages: row counts matched the known 2026-08-03
  pending backlog exactly on every ticker with a backlog (SPY 6, DOG 998, SH 108, PSQ 132 — each
  pending bar's count doubled, one row per reporting provider), and `role` correctly resolved to
  `ProviderRole.CANDIDATE`/`ProviderRole.WHISTLEBLOWER` on every row. Announced to `quant-scratch`
  via [croicu/quant-scratch#15](https://github.com/croicu/quant-scratch/issues/15) (additive, so
  informational/opt-in rather than a forced migration — same pattern as issue #17's announcement);
  its body was edited in place (not left stale) once #27 landed, since #15 was still
  `status:brainstorm`/unintegrated at that point.
- **Pipeline accuracy hardening, slice 1 (schema only)** — closed issue #28 (commit `1e67718`),
  opened and closed by Claude mid-task per this file's "Who closes an issue" exception. New
  `dim_field` dimension (mirrors `dim_provider`'s shape, seeded `open`/`high`/`low`/`close`);
  `provider_pair_disagreement` re-keyed from `(provider_id, field_group_id)` to `(provider_id,
  ticker_id, field_id)`, fixing two compounding pooling problems traced to a single global
  `stddev` standing in for a non-homogeneous population — pooled across tickers (`DOG`'s 25.9%
  true stuck rate vs. `SPY`/`QQQ`/`DIA`'s ~0%) and pooled across fields (`yfinance` noise
  concentrates in `high`/`low` while `open`/`close` stay stable, so one `ohlc`-group tolerance let
  the noisy fields set the band for the stable ones). Existing pooled rows discarded rather than
  migrated forward, same precedent set by the ticker-only predecessor of this change
  (`tasks/per_ticker_disagreement.md`) — every `(provider, ticker, field)` starts at zero. New
  single-row `dataset_inception` table (`CHECK (id = 1)` enforced), left empty — the actual
  `inception_date` value and the `--backfill` mechanism that consumes it are slice 2's work, not
  this one's. Schema-only by design: `quant-reconcile` keeps reading/writing the pre-migration
  shape until slice 2's algorithm/CLI changes land as a follow-up issue (design already converged,
  carried in #28's body). Migration `007_add_dim_field_and_dataset_inception`, applied by the repo
  owner directly via `psql` as `quant_data` (the schema-owner role, whose credentials aren't held
  by Claude or in `settings.local.json`) — Claude verified the result live and read-only via
  `quant_reader` both before applying (confirmed not yet applied) and after (confirmed the new key
  shape, seeded `dim_field` rows, and discarded `provider_pair_disagreement` rows).
- **`yfinance` outlier-detection mechanism** — closed issue #32 (repo owner's call, confirmed
  explicitly). `reconcile/outlier_detection.py`'s intra-provider MAD-based reversal/trend check
  (pure, unit-tested, no DB access) plus `data_quality_thresholds` (migration 010, per-(provider,
  ticker) coefficient overrides, no rows seeded) and `quant-reconcile`'s new outlier-detection pass
  (runs before Tiers 1-4 so a newly-rejected bar's candidate can auto-promote in the same run).
  Builds on #32's own tri-state `data_quality` schema foundation (`accepted`/`incomplete`/
  `rejected` replacing the old boolean `incomplete`).

  The seed coefficients (3/6/4/8) were never validated before real-data testing — the first live
  run rejected 42.3% of whistleblower bars, confirmed by spot-check to be ordinary price ticks, not
  outliers. Root cause: the original `± 2`-minute MAD window was self-contaminating (the target's
  own diffs partly formed the reference scale judging them). Fixed by widening the window to
  `± BACKGROUND_HALF_WINDOW_MINUTES` (20) and excluding the target from its own reference sample;
  re-tuned globally on real data (`k_reversal_oc=300, k_trend_oc=600, k_reversal_hl=400,
  k_trend_hl=800`) — no per-ticker exemption needed once fixed properly, resolving what had looked
  like a genuine `DOG`-specific noise floor under the broken window.

  A second real bug found live: the literal first/last bar of every session segment (9:30 open,
  16:00 close) was structurally unevaluable — no legitimate same-segment neighbor on one side by
  construction. Confirmed via a known-bad SPY 2026-07-29 16:00 ET tick (`high` frozen at 740.4873
  while the real price fell ~$12) that survived every prior run. Fixed with a frozen, shared
  reference window per segment tail (instead of shrinking per-bar as the edge approaches) plus a
  one-sided check against a separately-calibrated `k_boundary_oc`/`k_boundary_hl` for bars with
  only one usable neighbor — real-data calibration here put the confirmed case at the p99 ratio,
  with ordinary boundary bars at p97.5 or below.

  Final live result: 188/23,938 whistleblower bars rejected (0.79%), including all 3 original
  DataBento-confirmed SPY cases, spot-checked clean of false positives after both fixes. Full
  detail (numbers, methodology, what's explicitly out of scope) was in
  `tasks/yahoo_data_sanitization.md` before its deletion per this file's "delete once the issue
  closes" convention — see issue #32's comments for the same summary.
- **Add `timestamp` to `fact_pending_manual_resolution`** — closed issue #36, opened by the repo
  owner (surfaced building a Power Query/Excel dashboard against `quant-data` from `quant-scratch`'s
  `open-quant-data` tool, croicu/quant-scratch#19/#20). Schema-consistency fix: every other
  fact/staging table already carried a denormalized `timestamp` alongside its `date_id`/`time_id`
  keys, forcing this one table's consumers into an extra `dim_date`/`dim_time` join. Migration `012`
  adds the column and backfills existing rows from `dim_date.date` + `dim_time.hour`/`minute`;
  `PostgresDatabase.mark_pending_manual_resolution` gained a `timestamp` parameter, sourced from the
  bar's own `StagingRow.timestamp` at the one call site (`reconcile/cli.py`'s `_run_automatic_pass`).
  Verified live against CroicuWS1: 215/215 existing pending rows correctly backfilled, `NOT NULL`
  applied cleanly with no remaining nulls.
- **Archive candidate rows before purge; `market_data_archive`** — closed issue #35 (commit
  `473ccd0`), opened and closed by Claude mid-task per this file's "Who closes an issue" exception.
  `purge_staging_bar` now archives a candidate's staging row into the new, permanent
  `market_data_archive` table before deleting it (one transaction) — closes the information-loss
  gap where a resolved bar's raw disagreement evidence was gone for good once purged (only 4,101 of
  52,953 resolved bars checked 2026-08-07 still had both providers' original staging rows intact).
  Whistleblower rows unaffected, still never purged. `dim_provider.role` gained a third value,
  `'advisor'` (seeds `'manual'`/`'databento'`) — can suggest a value but has no autonomous authoring
  rights, unlike `'candidate'`. `fact_reconciliation_participant` gained a nullable `archive_id`,
  back-filled once a participant's row is actually archived. The two-API finalize surface (accept
  candidate/whistleblower, accept value) that would let a human write directly to the archive was
  deliberately deferred to `tasks/finalize_targeted_promotion.md`'s own not-yet-converged scope —
  this migration only ships the schema/plumbing it depends on.

  Live-verified against a restored CroicuWS1 snapshot twice: once as an incremental fix alongside
  discovering and fixing a second, unrelated bug (below), once via a full clean-slate rebuild
  (drop/restore/migrate/reconcile from scratch) — both produced identical final numbers. Confirmed
  correct `archive_id` back-fill (candidate rows only), whistleblower never archived, zero
  unaccounted-for data across every stuck/pending/resolved bar traced.

  Testing surfaced two follow-up gaps, both recorded in Pending Tasks above rather than silently
  dropped: `ingestion_coverage`'s `quant-ingest` write path is still missing (issue #31) — its own
  one-time backfill had gone stale, stranding ~8,900 real candidate bars in staging behind a
  since-fixed coverage gate — and its backfill query itself needed a fix (union
  `staging_market_data_1min` with `market_data_archive`, since a fully-purged date is now invisible
  to a staging-only query). Separately, candidate-missing bars (the mirror image of #31's
  whistleblower-missing case) were found to have no resolution path at all, not even into the
  pending queue — a new, not-yet-designed gap.
- **Materiality floor on reconciliation tolerance** — closed issue #40 (commit `c861a9b`), opened
  and closed by Claude mid-task per this file's "Who closes an issue" exception. New
  `materiality_floor` table (`provider_id`, `ticker_id`, `field_id`, `floor_value`, `floor_type`),
  exactly mirroring `provider_pair_disagreement`'s grain, bounding Tier 2/3 tolerance
  (`k * stddev * reference_value`) below by an economically meaningful minimum — an honestly-
  converging `stddev` estimate was pushing economically trivial disagreements (a fraction of a
  cent) to Tier 4 purely because they exceeded an ever-tightening relative threshold. New
  `FieldTolerance(stddev, floor_value=0.0, floor_type)` dataclass threaded through
  `algorithm.py`'s tolerance chain; a missing `(provider, ticker, field)` falls back to
  `floor_value = 0.0` (no floor, unchanged behavior) — the same "ship schema, seed real values once
  validated" precedent as `data_quality_thresholds`, except this time seeded immediately with a
  real, data-driven calibration rather than left empty.

  Seed values came from a genuine finding, not a gut prior: per-bar `ibkr` volume correlates with
  `ibkr`/`yfinance` disagreement (log-log regression over the full pending-manual-resolution
  backlog, `R² = 0.32`). Live-verified against a restored CroicuWS1 snapshot via two full
  clean-slate rebuilds: the volume-derived defaults alone took the backlog from 215 to 103 pending
  bars (52%) — `SPY` 94%, `DIA` 85%, `QQQ` 72% resolved — but `PSQ` and `DOG` got zero benefit,
  since both sit well above the fitted line (`DOG`'s own observed average diff was more than double
  its regression-derived floor; `PSQ`'s was triple). Overriding those two with their own P90 (90th
  percentile of their pending backlog's diff distribution) instead of the cross-ticker model took
  the total to 42 pending (80% reduction from 215), both landing within a hair of the P90-implied
  ~10% residual — confirming the per-ticker-distribution approach works precisely where the
  population model didn't. `IWM` deliberately left unseeded (only 12 pending bars, too thin to
  calibrate responsibly) — revisit once more data accumulates; it isn't in the active ingestion
  watchlist, so that won't happen automatically.

  A real limitation surfaced during testing, documented rather than worked around: the floor only
  affects a bar's *first* evaluation — `fetch_staging_rows_for_reconciliation` excludes any bar
  already in `fact_pending_manual_resolution`, so a newly-added or newly-tuned floor can never
  retroactively unstick the *existing* backlog on its own. Confirmed directly: applying the floor
  to an already-pending backlog resolved nothing (0 groups) until the database was rebuilt from
  scratch so every bar got evaluated fresh with the floor already in place. A dedicated "retry
  Tiers 1-3 against the pending queue with current stats" mechanism would be needed to close that
  gap — not built here. The volume/noise correlation itself also surfaced a real, still-unresolved
  tension with an earlier investigation — see Pending Tasks above.
- **`quant-ingest`'s `ingestion_coverage` write path** — issue #31, opened by the repo owner.
  `status:implementation`, fix pushed — **left open**, per this file's "Who closes an issue" rule:
  the opener verifies and closes it themselves. The schema, one-time backfill, and
  `quant-reconcile`'s consuming side shipped earlier; what was still missing was `quant-ingest`
  itself ever recording/coalescing coverage on a successful fetch, so the table only ever reflected
  the one-time backfill's moment and went stale the instant real ingestion resumed. Surfaced
  concretely twice in the same session live-testing `massive` as a second candidate (issue #44):
  both `SPY` and `QQQ` needed a hand-written `INSERT` before `quant-reconcile`'s
  candidate-confirmed-absence path (issue #49) could do anything, since that fix depends entirely
  on `ingestion_coverage` reflecting reality. `PostgresDatabase.record_ingestion_coverage`
  (`ingest/cli.py`'s `_ingest_one` calls it once fetch *and* write both succeed for a
  `(ticker, date)`) resolves `(ticker_id, provider_id, date_id)`, finds every existing range that
  already contains or is immediately adjacent to that `date_id`, and either no-ops (already
  covered), extends the one touching range, or merges two touching ranges into one — never leaves
  one row per day. Recording coverage failing on its own is logged and skipped, not fatal to that
  `(ticker, date)` pair, matching `_ingest_one`'s existing per-step fetch/write failure tolerance.
  Live-verified against CroicuWS1: ingesting one more day for an already-covered `(SPY, massive)`
  range extended the existing row in place (`2026-07-23`–`2026-08-05` → `2026-07-23`–`2026-08-06`)
  rather than creating a second one.
- **Candidate-confirmed-absent bars have no resolution path at all** — closed issue #49, opened and
  closed by Claude mid-task per this file's "Who closes an issue" exception. This is exactly the
  gap this file previously tracked as "Candidate-missing bars have no resolution path at all"
  (found 2026-08-14, no issue opened yet at the time) — `fetch_staging_rows_for_reconciliation`
  required *every* configured candidate provider present before a bar_key became eligible at all;
  live-tested with `massive` as a second candidate (issue #44), this left 449 real `ibkr` `SPY`
  bars silently unevaluated because `massive` simply never reported those exact minutes (small
  session-edge coverage differences between providers). Fix generalizes issue #31's own
  whistleblower-confirmed-absent mechanism to every required provider: `postgres.py`'s
  `fetch_staging_rows_for_reconciliation` now requires only *at least one* candidate present (the
  exact-count `HAVING` dropped entirely), and `reconcile/cli.py`'s per-bar readiness filter checks
  each individually-missing required provider — whistleblower or candidate — against
  `ingestion_coverage`, removing the bar_key only if a missing provider's absence isn't confirmed.
  **`algorithm.py` needed zero changes** — every tier already iterated whatever candidates happened
  to be present in `bars`, never assuming a fixed count, so a bar_key that survives the filter with
  only `ibkr` present resolves using `ibkr` alone, subject to the same whistleblower validation as
  any other bar. Deliberately kept simple, per explicit discussion: matched-bar/graduation counting
  stays untouched (a confirmed-absent-candidate resolution doesn't feed that candidate's own
  calibration — correct, since there's no comparison to measure — but the properly-precise version
  would make matched-bar counting per-(candidate, whistleblower) pair rather than requiring the
  full provider set; left as a deferred refinement). Live-verified against CroicuWS1 after manually
  seeding the missing `ingestion_coverage` row: all 449 previously-orphaned `SPY` bars resolved via
  `completeness` in the very next run, 0 newly pending.
- **Material-disagreement check on the Tier 2 tiebreak** — closed issue #50, opened and closed by
  Claude mid-task per this file's "Who closes an issue" exception. Before this, when more than one
  candidate agreed with the whistleblower, `_pick_preferred` promoted
  `settings.reconcile.preferredProvider` outright with no check on how far it actually diverges
  from the *other* agreeing candidate(s). Live-tested on `SPY`: `ibkr` won that tiebreak in 3,892 of
  3,894 dual-agreement bars, `massive` only the 2 where `ibkr` itself was the outlier — harmless on
  that data (checked: median divergence $0.00, p99 ~1 cent, max 6 cents), but nothing would have
  caught it if the divergence had been real. New `_candidates_disagree_materially` reuses the
  winner's own already-computed `materiality_floor`-derived floor (`_materiality_floor`, factored
  out of `_tolerance` for reuse) rather than a new stat — a floor of `0.0` (the existing
  "unconfigured" default) means the check doesn't engage, preserving prior behavior exactly
  wherever no floor was ever seeded. Deliberately scoped to Tier 2 only, not Tier 3
  (`_resolve_boundary_fix`'s own unrelated "first agreeing candidate wins" quirk is out of scope
  here) — Tier 3 fired only 6 times out of 9,598 resolved `SPY` bars, a small fraction. Live-tested
  against CroicuWS1: re-ran reconciliation from the same pre-reconcile snapshot with the fix in
  place and confirmed zero bars actually flipped — traced the one candidate top-divergence case
  found earlier and confirmed it was never a genuine dual-agreement bar to begin with (`massive`
  had already failed its own tolerance check against the whistleblower, so it was never in Tier 2's
  `agreeing` set for the new check to even consider) — a reassuring null result on real data, not
  an untested code path.
