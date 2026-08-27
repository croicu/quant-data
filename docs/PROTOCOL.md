# PROTOCOL.md

CLI signature and file format schemas for `quant-data`.

## CLI

<!-- Command name, arguments, flags, exit codes. -->

Every command below also accepts a response file via argparse's native `fromfile_prefix_chars="@"`
support: `quant-ingest @some-file.args` reads additional arguments from `some-file.args`, one per
line, expanded in place before parsing — standard `@file` convention (same as GCC/MSVC). Useful for
saving a recurring override (e.g. `--providers yfinance,massive` to skip IBKR for a backfill run)
instead of hand-editing `settings.local.json` for a one-off invocation and remembering to revert it
afterward. A response file can bundle any combination of flags, not just `--providers` — e.g.
`configs/spy-backfill-yfinance-massive.args` combines `--ticker`/`--providers`/`--start-date`/
`--end-date` into one reusable preset; `configs/ibkr-trades-only.args` combines `--providers ibkr`
with `--ibkr-methods TRADES` to restrict a run to just the primary trade method;
`configs/all-providers.args` (`yfinance,massive,ibkr`) is the explicit full-set preset now that
`settings.local.json` no longer carries a baked-in `providers` default — `Settings.providers`
itself has **no implicit fallback** (empty by default, not `["yfinance"]` as before — a silent
single-provider default was judged confusing). `quant-ingest`/`quant-stage`/`quant-reconcile` all
fail fast with a clear error if `settings.providers` ends up empty after CLI overrides are applied,
rather than picking a provider on the caller's behalf.

### `quant-ingest`

- Usage: `quant-ingest [--start-date YYYY-MM-DD [--end-date YYYY-MM-DD] | --catch-up] [--ticker TICKER] [--providers NAME,...] [--ibkr-methods METHOD,...] [--debug]`
- Fetches 1-minute bars from every provider in `settings.providers` (no default — must be
  configured, or passed via `--providers`/a response file, e.g. `@configs/all-providers.args`) and
  archives each provider's fetch into the separate
  `quant_ingest` database's `provider_source_archive` (`settings.postgres.archiver.dbname`, required
  — croicu/quant-data#52). **As of croicu/quant-data#56, this is `quant-ingest`'s entire job — it
  writes to `quant_ingest` only and no longer touches `quant_data` at all**, not even
  `staging_market_data_1min`. A separate command, `quant-stage` (see below), reads what's archived
  here and writes `staging_market_data_1min`; `quant-reconcile` (see further below) then promotes
  agreeing staging rows into `fact_market_data_1min`. See `docs/ARCHITECTURE.md` for the full design
  and the reasoning behind the split.
- `--ticker` — single ticker (e.g. `AAPL`); omit to use every ticker in `settings.tickers` instead.
- `--start-date` — first trading date, `YYYY-MM-DD`; omit to use `settings.startDate`.
- `--end-date` — last trading date (inclusive); omit (with `--start-date` given) for a single day.
  Requires `--start-date` — rejected on its own.
- `--catch-up` — re-fetches the trailing `settings.catchUpLookbackDays` days (default 7),
  excluding today, instead of a `--start-date`/`--end-date` range. Rejected in combination with
  `--start-date`/`--end-date`. Meant for an unattended nightly run (cron/systemd timer, set up
  outside this repo — see `tasks/scheduled_jobs.md`) that re-covers any day a prior run only
  partially archived; safe to run against already-archived days too — `provider_source_archive` has
  no uniqueness constraint on `(ticker, provider, trading_date)`, a re-fetch is simply a new row.
- `--backfill` is **not currently supported** here (removed by croicu/quant-data#56's split — its
  old bookkeeping, `dataset_inception`/earliest-covered-date, spanned both `quant_data` and
  `quant_ingest` in a way the split didn't resolve on its own; see `docs/ARCHITECTURE.md`'s `ingest`
  section for why, and the issue for the follow-up).
- `--debug` overrides `settings.json`'s `debug` flag; also re-raises the underlying exception
  instead of printing a one-line error, for upfront failures (settings load, no ticker/date
  configured at all, every configured provider failing to connect, `archiver` unconfigured).
- `--providers` — comma-separated provider names (e.g. `yfinance,massive`) overriding
  `settings.providers` entirely for this invocation; omit to use `settings.providers` as-is. Still
  no per-ticker override — scoping a provider to a subset of tickers (e.g. a pilot rollout) means a
  separate invocation. Added by croicu/quant-data#64, replacing the old workaround of hand-editing
  `settings.local.json`'s `providers` array and reverting it afterward.
- `--ibkr-methods` — comma-separated IBKR method names (e.g. `TRADES,BID_ASK`) overriding
  `settings.ibkr.methods` entirely for this invocation; omit to use `settings.ibkr.methods` as-is
  (or `IBKRIntraDay.DEFAULT_METHODS` if that's also unset). Case is passed through as-is, **not**
  lowercased like `--providers` — IBKR's own `whatToShow` literals (`TRADES`, `BID_ASK`,
  `MIDPOINT`) are meaningfully uppercase. Also added by croicu/quant-data#64.
- `settings.providers` (array of strings, default `[]` — **no implicit provider**, changed by
  croicu/quant-data#64: an unconfigured `settings.providers` used to silently default to
  `["yfinance"]`, judged confusing) — which providers to run each invocation, applied uniformly
  across every ticker in `settings.tickers`; unrecognized names fail fast at startup, and so does an
  empty list (after CLI overrides are applied) — `quant-ingest --debug` shows this as an `AppError`
  same as any other upfront misconfiguration. `settings.ibkr` (`host`/`port`/`clientId`, all optional — default to
  `IBKRIntraDay`'s own defaults, IB Gateway's paper port `4002`) — only consulted when `"ibkr"` is
  in `settings.providers`. `settings.ibkr.methods` (optional array of strings, e.g. `["TRADES"]` —
  croicu/quant-data#60) restricts which IBKR method(s) `quant-ingest` fetches per (ticker, date);
  **omitted (the default) means all of `IBKRIntraDay.DEFAULT_METHODS`** (`TRADES`, `BID_ASK`, and
  `MIDPOINT` today), archived as separate `provider_source_archive` rows under their own `method`
  — not just the primary/OHLCV one. This roughly triples IBKR call volume per (ticker, date) versus
  before croicu/quant-data#60, within `settings.ibkr.rateLimit`'s existing ceiling. No equivalent setting
  exists for `yfinance`/`massive` — both are genuinely single-method, so there's nothing to
  restrict. `settings.massive` (`apiKey`, required if the object is present — no
  usable local default, a free-tier account credential) — only consulted when `"massive"` is in
  `settings.providers`; `"massive"` in `settings.providers` without a configured `settings.massive`
  fails fast at startup. `settings.ibkr.rateLimit`/`settings.yfinance.rateLimit`/
  `settings.massive.rateLimit` (each `{requestsPerWindow, windowSeconds}`, both keys required
  together if the object is present) pace that provider's `fetch_bars` calls via a sliding-window
  `RateLimiter` — IBKR defaults to `50`/`600` and Massive to `5`/`60` even when omitted (real
  external ceilings that always apply once the provider is configured at all); yfinance defaults to
  unlimited when omitted. `MassiveIntraDay` also retries on HTTP 429 internally (3 attempts, 15s
  apart) since Massive's documented rate limit isn't strictly enforced in practice
  (croicu/quant-scratch#24's live testing) — the pre-emptive `RateLimiter` above is the primary
  defense, this is a fallback for the cases it doesn't strictly hold. `settings.postgres.archiver.dbname`
  (croicu/quant-data#52) — a second database on the same server/role as `settings.postgres`'s
  existing `host`/`port`/`user`/`password`/`sshUser`/`sshKeyPath`, just a different `dbname`.
  **Required** as of croicu/quant-data#56 (`quant-ingest` raises at startup if unconfigured — there
  is nowhere else for it to write).
- Exit codes: `0` every (ticker, date) pair had at least one provider succeed; `1` settings load
  failure, no ticker/date-range configured at all, `archiver` unconfigured or unreachable,
  every configured provider failing to connect, or one or more (ticker, date) pairs where every
  provider failed (an individual provider failing for one pair — bad ticker on that source, gateway
  unreachable, its own archive write failing — logs a warning and the run continues with whatever
  providers/pairs still work, rather than aborting — `1` here can mean "partial success", not
  necessarily "nothing happened"); `2` argument parsing error (argparse's default behavior on
  missing/bad args, e.g. malformed dates or `--end-date` without `--start-date`).

### `quant-stage`

- Usage: `quant-stage [--start-date YYYY-MM-DD [--end-date YYYY-MM-DD] | --catch-up] [--ticker TICKER] [--providers NAME,...] [--debug]`
- The second half of what used to be a single `quant-ingest` process (croicu/quant-data#56). For
  every provider in `settings.providers`, reads the most recently archived fetch for each (ticker,
  date) from `quant_ingest`'s `provider_source_archive`, parses it into `OHLCV` bars
  (`stage.parsers`, one module per provider — this is where the old provider-side data-quality
  determination, e.g. yfinance's NaN/zero-volume-as-incomplete heuristic, now lives), and writes the
  result into `quant_data.staging_market_data_1min`. Also updates `ingestion_coverage`
  (`record_ingestion_coverage`, croicu/quant-data#31, moved here from `quant-ingest` by the split)
  once a provider's parse and staging write both succeed for a `(ticker, date)` —
  `quant-reconcile`'s candidate-confirmed-absence handling depends on this table actually reflecting
  what's been staged. A `(ticker, provider, date)` with nothing archived is skipped, not an error —
  fetching it is `quant-ingest`'s job. **Weekend dates are skipped outright**, before ever reading
  the archive — `quant-ingest` still fetches/archives them (IBKR returns the prior trading day's
  session for a weekend request rather than failing), but staging that would just be a redundant
  re-upsert of a day already staged correctly under its own date. Does **not** write
  `fact_market_data_1min` directly — promoting agreeing staging rows there is `quant-reconcile`'s
  job (see below).
- `--ticker`/`--start-date`/`--end-date`/`--catch-up`/`--providers`/`--debug` — identical semantics
  to `quant-ingest`'s own flags above, just applied to the archive-read/staging-write step instead
  of the provider-fetch step. `--backfill` is not supported here either, for the same reason as
  `quant-ingest`.
- `settings.providers`/`settings.postgres.archiver.dbname` are read the same way as `quant-ingest`
  (which providers to process; where to read archived fetches from — **required** here too, same
  reasoning). `settings.postgres` (the primary `quant_data` connection) is also required, same as
  every other command in this repo.
- Exit codes: `0` every (ticker, date) pair had at least one provider stage successfully; `1`
  settings load failure, no ticker/date-range configured at all, either database unconfigured or
  unreachable, or one or more (ticker, date) pairs where every provider either had nothing archived
  or failed to parse/write; `2` argument parsing error, same shape as `quant-ingest`'s.

### `quant-reconcile`

- Usage: `quant-reconcile [--finalize | --reevaluate-unadjudicated] [--debug]`
- Reads `staging_market_data_1min`, resolves each not-yet-resolved (bar, field group) — today just
  `ohlc`, since `volume` no longer has its own field group and simply rides along with whichever
  provider wins `ohlc` (see `tasks/volume_reconciliation.md`) — against the providers that reported
  for it, and promotes a bar into `fact_market_data_1min` once every field group has resolved.
  Staging rows purge once that's safe — deferred while an adjacent minute is still unresolved, so a
  future run's boundary-fix check doesn't lose that neighbor's raw data. Whistleblower-role
  providers' rows (`yfinance` today) are never purged at all, even once safe by the rule above —
  permanently retained for irreplaceability (croicu/quant-data#28), not just this neighbor-safety
  window.
- No date-range/ticker flags — processes everything currently eligible in staging each run, unlike
  `quant-ingest`. "Eligible" now also requires the ticker to have *graduated* (croicu/quant-data#28):
  a ticker below `GRADUATION_THRESHOLD_MATCHED_BARS` (1,400) matched bars gets no Tier 1-4 attempt
  at all, regardless of individual bars' own completeness/agreement — see `docs/ARCHITECTURE.md`
  for the full graduation-batch mechanism. Graduation itself is tracked per `(candidate provider,
  ticker, field)`, not per ticker alone (croicu/quant-data#44) — once any candidate has graduated on
  a ticker, that ticker stays eligible permanently, but a *second* candidate (e.g. `massive` joining
  `ibkr`) still runs its own graduation batch for whichever `(provider, field)` combinations it's
  still missing, without recomputing or overwriting an already-graduated candidate's existing stats.
- Eligibility only requires **at least one candidate** to have reported, not every configured
  provider (croicu/quant-data#31 for the whistleblower, generalized to every candidate too by
  croicu/quant-data#49) — a bar missing the whistleblower, or missing one or more candidates (e.g.
  `massive` simply never reported that exact minute while `ibkr` did), can still resolve using
  whoever *did* report, but only once `ingestion_coverage` confirms each missing provider's date
  range was actually ingested for that ticker (a real "nothing here," not "not scanned yet");
  otherwise the whole bar is left alone untouched, same as always. See `docs/ARCHITECTURE.md`'s
  `reconcile` section for the exact mechanism.
- Tier 2 (agreement)/Tier 3 (boundary-fix) tolerance is now measured **per ticker per field**
  (`open`/`high`/`low`/`close` independently), not pooled across tickers or across the whole `ohlc`
  group — fixes two compounding false-positive/false-negative sources found in the pooled design
  (see `docs/ARCHITECTURE.md`'s `reconcile` section). OHLC still promotes as one atomic unit; only
  the comparison is per-field.
- **Without `--finalize`** (the plain, day-to-day invocation): only the automatic tiers run
  (completeness / raw agreement / boundary-misalignment), and only against bars with no
  `fact_pending_manual_resolution` row — anything already flagged pending from an earlier run is
  skipped entirely, not re-evaluated. Repeats internally until a pass resolves nothing new, so a
  single invocation reaches the real convergence floor rather than needing several manual re-runs
  (`tasks/quant_reconcile.md`'s seeding-lag fix). A (bar, group) that exhausts all three tiers gets
  a `fact_pending_manual_resolution` row inserted — an expected steady state, not a failure, and the
  deliberate hand-off point to `--finalize`/manual correction. This is what makes a realistic
  cadence (e.g. plain `quant-reconcile` run daily) cheap even with a growing backlog of genuinely
  unresolved bars: each plain run only ever touches what's new since the last one.
- **`--finalize`**: force-resolves *only* what's currently in `fact_pending_manual_resolution`,
  using `settings.reconcile.preferredProvider`'s raw value (`resolution_path = 'finalized'`) — no
  automatic-tier re-attempt, those already failed. It does **not** also evaluate not-yet-attempted
  bars; a `--finalize` run with nothing pending yet has nothing to do. Meant to be run separately
  from the plain cadence (e.g. weekly, after a person has had a chance to look at what's
  accumulated and optionally retune `settings.reconcile`) — run plain `quant-reconcile` first if you
  want it to also pick up anything from today that hasn't been attempted yet.
- `--debug` overrides `settings.json`'s `debug` flag; also re-raises the underlying exception
  instead of printing a one-line error.
- `settings.reconcile.preferredProvider` (default `"ibkr"`) — which candidate provider wins
  `--finalize`'s fallback, any Tier 2 tie-break among multiple agreeing candidates, and (since
  croicu/quant-data#44) the automatic pass's `'unadjudicated'`/`'historical_mad_agreement'`
  fallbacks: whenever no `ACCEPTED` whistleblower row exists to adjudicate between two or more
  valid candidates (an outlier-rejected or confirmed-absent whistleblower, or — structurally, for
  the entire period before `yfinance`'s ~30-day rolling window can reach — the historical period),
  the bar resolves to `preferredProvider`'s raw value. If this ticker has a fully-seeded
  `candidate_pair_mad_band` (`tasks/retroactive_revision.md`), the two candidates are actually
  checked against each other first (`resolution_path = 'historical_mad_agreement'` on agreement;
  disagreement beyond the band leaves the bar stuck at Tier 4 instead of promoting) — otherwise it
  falls back to the original unconditional promotion (`resolution_path = 'unadjudicated'`, no
  tolerance comparison ever attempted at all). Only ever a provider with `dim_provider.role =
  'candidate'`, never the whistleblower. `settings.reconcile.k` (default `3.0`, must be positive) —
  the tolerance multiplier (`tolerance = k * stddev * reference_value`) applied against
  `provider_pair_disagreement`'s measured variance (unrelated to `candidate_pair_mad_band`'s own
  separately-configured `k`).
- **`--reevaluate-unadjudicated`** (`tasks/reevaluate_unadjudicated_bars.md`): a one-off backlog
  pass, not part of the plain/`--finalize` cadence — retroactively re-checks every existing
  `resolution_path = 'unadjudicated'` bar for a ticker with a (possibly since-seeded)
  `candidate_pair_mad_band`, using each candidate's original value from `market_data_archive`
  (`staging_market_data_1min` is already purged for these bars by the time this would run).
  **Never touches `fact_market_data_1min` or `winning_provider_id`** — only `resolution_path`
  changes: confirmed agreement relabels to `'historical_mad_agreement'` (the same label the live
  tier uses); confirmed disagreement relabels to `'unadjudicated_disputed'`, flagging it for a
  future manual-review pass to find via a plain query, not retracting the already-published value
  (this repo has no mechanism to un-publish a fact row, and building one was explicitly out of
  scope for this task). A bar that can't be re-evaluated (band not fully seeded for its ticker, or
  not exactly two archived candidates) is left untouched. Mutually exclusive with `--finalize`.
- Exit codes: `0` the run completed (regardless of how many groups ended up stuck — that's a
  normal outcome, not a failure); `1` settings load failure, `settings.postgres` not configured;
  `2` argument parsing error.
- A whistleblower provider's value (`yfinance` today) only ever reaches `fact_market_data_1min`
  through manual correction — directly hand-editing `staging_market_data_1min`/
  `fact_market_data_1min`, no dedicated tooling — never through `--finalize`'s algorithm. A person
  doing this should also delete the corresponding `fact_pending_manual_resolution` row (if one
  exists) as part of the same manual edit — nothing else does this automatically for a hand
  correction the way `--finalize` does for its own resolutions.

### `quant-dispatch`

- Usage: `quant-dispatch [--debug]`
- One-shot job dispatcher (croicu/quant-data#66): checks the `jobs` table once for every enabled,
  currently-idle row whose `next_run_at` has arrived, runs each due job's `command` as a
  subprocess (e.g. `quant-ingest --catch-up`), records the exit code/error/next schedule, and
  exits. Not a daemon — something host-specific (a cron entry or systemd timer, set up outside
  this repo) is responsible for invoking `quant-dispatch` repeatedly; job *definitions* live in
  `jobs` as data instead, so the public repo never has to name a specific host.
- Each due job's `status` is flipped to `'running'` immediately before its subprocess launches and
  back to `'idle'` once it finishes (success or failure alike), so a `quant-dispatch` invocation
  overlapping a still-running prior one skips that job rather than double-dispatching it (e.g. two
  concurrent `quant-reconcile` runs against the same database).
- `next_run_at` is rescheduled to the moment this dispatch actually ran a job, plus that job's own
  `interval_seconds` — not the job's prior `next_run_at` — so a dispatcher that was down or delayed
  doesn't pile up a burst of immediately-due catch-up runs the moment it resumes. Exception: a
  `run_once` job (croicu/quant-data#68) that just succeeded is disabled instead — `next_run_at` is
  still recorded but irrelevant, since `fetch_due_jobs` filters on `enabled`. A `run_once` job that
  failed still reschedules/retries normally.
- A job is only considered due if every job it depends on (`job_dependencies`, croicu/quant-data#68)
  has already succeeded (`last_exit_code = 0`) — a gated job is simply excluded that cycle, with no
  separate bookkeeping, since its own `next_run_at` doesn't move.
- `--debug` overrides `settings.json`'s `debug` flag; also re-raises the underlying exception
  instead of printing a one-line error.
- `settings.postgres` is required, same as every other command in this repo, **and
  `settings.postgres.worker` is required specifically for `quant-dispatch`** — `jobs` lives in its
  own `quant_schedule` database, not `quant_data` (croicu/quant-data#66's design pivoted away from
  the original `quant_data`-embedded plan before it shipped, once it was clear `jobs` should
  eventually schedule work against other databases this repo doesn't own too). `quant-dispatch`
  connects to `quant_schedule` as `quant_worker` (read/update on `jobs`, read-only on
  `job_dependencies`); a separate `quant_scheduler`
  role (full CRUD) is what `quant-schedule` (croicu/quant-data#68) connects as to create job rows —
  `quant-dispatch` never authenticates as it. Omitting `settings.postgres.worker` raises a clear
  `AppError` at startup.
- Exit codes: `0` no jobs were due, or every due job's subprocess exited `0`; `1` settings load
  failure, `settings.postgres`/`settings.postgres.worker` not configured, or one or more due jobs
  exited non-zero/failed to launch; `2` argument parsing error.
- `jobs` ships empty by `migrations/quant_schedule/001_add_jobs_table.sql` — real job rows are
  created by `quant-schedule` (connecting as `quant_scheduler`) or inserted by hand via the same
  role; see `docs/DATABASE.md`/`docs/SCHEMA.md`'s `quant_schedule`/`jobs` sections.
- **Deployment assumptions** (relevant to whatever cron entry/systemd timer invokes
  `quant-dispatch`): a job's `command[0]` is resolved against `sys.executable`'s own directory
  first (falling back to a plain `PATH` lookup if not found there) — not the inherited `PATH` —
  since `quant-ingest`/`quant-stage`/`quant-reconcile` are installed as siblings of
  `quant-dispatch` in the same venv, and an unattended cron/systemd `PATH` is often a minimal
  system default that was never told about that venv. `cwd` is left to whatever `quant-dispatch`
  itself was invoked with — already required to be the repo root for `quant-dispatch`'s own
  `Settings.load()` to find `settings.json` in the first place, so a due job's subprocess inherits
  that same correct `cwd` for free.

### `quant-schedule`

- Usage: `quant-schedule --ticker TICKER --start-date YYYY-MM-DD [--end-date YYYY-MM-DD]
  [--providers NAME,...] [--ibkr-methods METHOD,...] [--retry-interval-seconds N] [--dry-run]
  [--debug]`
- Decomposes a bulk backfill request into a job graph and writes it into `quant_schedule.jobs`/
  `job_dependencies` (croicu/quant-data#68) — it never runs anything itself; `quant-dispatch` picks
  the jobs up as they become due. `--ticker`/`--start-date` are required (unlike `quant-ingest`,
  there's no "every ticker in `settings.tickers`" mode — a work item is always one ticker).
  `--end-date` defaults to `--start-date`.
- One ingest job per (calendar day, provider), weekends included (`quant-ingest` handles a weekend
  date correctly on its own, marking it covered without data instead of wasting an API call) — or
  per (day, method) for `ibkr` specifically, since it's the only provider with more than one
  method — followed by one staging
  job depending on every ingest job, followed by one reconcile job depending only on the staging
  job (`quant-reconcile` itself takes no ticker/date arguments, so this is always the bare
  `quant-reconcile` command). Every created job is `run_once=True`: it's disabled once it succeeds,
  and simply retries on `--retry-interval-seconds` (default 300) if it fails.
- `--providers`/`--ibkr-methods` default to `settings.providers`/`settings.ibkr.methods` (falling
  back to `ibkr`'s own default method list if neither is set), same resolution as `quant-ingest`.
- `--dry-run` prints the planned jobs (name, command, `run_once`, `depends_on`) without writing
  anything — the way to review a plan before committing it to the real database.
- All jobs for one work item are created in a single transaction — either the whole graph is
  created, or none of it is. Re-submitting the same work item (same ticker/date range/providers)
  fails with a clear error instead of a raw database error, since job `name`s collide.
- `settings.postgres` is required, and **`settings.postgres.scheduler` is required specifically for
  `quant-schedule`** — it connects to `quant_schedule` as `quant_scheduler` (full CRUD — `SELECT`,
  `INSERT`, `UPDATE`, `DELETE` — on `jobs`/`job_dependencies`), distinct from `quant_worker`
  (`quant-dispatch`'s own connection, `settings.postgres.worker`); see `docs/DATABASE.md`.
  Omitting it raises a clear `AppError` at startup (skipped on `--dry-run`, which never opens a
  database connection at all).
- Exit codes: `0` success (including a completed `--dry-run`); `1` settings load failure,
  `settings.postgres`/`settings.postgres.scheduler` not configured, no provider configured, or job
  creation failed (e.g. a name collision); `2` argument parsing error.

There is no generic `quant-data` command — `quant-ingest`/`quant-stage`/`quant-reconcile`/
`quant-dispatch`/`quant-schedule` (write side, packages `ingest`/`stage`/`reconcile`/`dispatch`/
`schedule`, outside the `quant_data` namespace — no importable surface, console script only) and
`quant_data.MarketData`
(read side — a library, not a CLI) are the consumer-facing entry points. `MarketData`, `OHLCV`, `DataQuality`, `LoggingSink`, `PendingResolutionBar`, `ProviderRole`,
`RejectedWhistleblowerBar`, and `create_postgres_provider` are re-exported at the `quant_data` top
level (`from quant_data import MarketData, OHLCV, create_postgres_provider, ...`);
`quant_data._internal.*` is private (nested, not a separate package) and should not be imported
directly by external consumers. `LoggingSink` is the injectable logging contract — pass an
optional `logger=` matching its shape to `create_postgres_provider`/`MarketData` to route
quant-data's internal logging into your own log stream (see `docs/ARCHITECTURE.md`).

## File formats

<!-- Schemas for any files this project reads or writes. -->

This repo's primary "file format" is the database schema itself — see `docs/SCHEMA.md` for the
four-table star schema (`dim_ticker`, `dim_date`, `dim_time`, `fact_market_data_1min`) and
`migrations/001_init_schema.sql` for the exact DDL.

### Migration files (`migrations/*.sql`)

Plain numbered SQL files (`NNN_description.sql`), applied manually via `psql` in order — see
`docs/DATABASE.md`. Each migration wraps its DDL in a single transaction and records itself in the
`schema_migrations` table on success.

`migrations/quant_ingest/*.sql` (croicu/quant-data#52) is a separate, independently-numbered
sequence for the `quant_ingest` database — a different database on the same server, with its own
`schema_migrations` table. Applied the same way (`psql ... -d quant_ingest -f
migrations/quant_ingest/NNN_description.sql`), just never mixed into the main `migrations/`
sequence, since the two databases' schema histories are unrelated.
