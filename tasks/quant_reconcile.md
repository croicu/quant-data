# quant-reconcile

## Status: Done — schema slice closed (issue #24), CLI implemented, unit-tested, live-verified at
full scale, and committed (`f5b192f`, issue #25, `status:ready-to-submit`). Manual correction
itself needs no code (direct hand-edit, per design) — though see `tasks/finalize_targeted_promotion.md`
for a proposed CLI-tooled alternative to that hand-editing. Follow-up work continues in
`tasks/per_ticker_disagreement.md`, `tasks/inverse_pair_cross_check.md`, and
`tasks/finalize_targeted_promotion.md`.

## Problem statement

`staging_market_data_1min` now fills up for real: every `quant-ingest` run writes each configured
provider's raw bars there independently (`yfinance` and `ibkr` today — see issue #22), and
`fact_market_data_1min` — the table `MarketData.fetch_bars` actually reads — no longer gets
written at all. Nothing yet reads staging back out and promotes agreeing bars into fact, so the
warehouse is currently accumulating provider data with no path to becoming the trusted, queryable
dataset it's meant to produce. `quant-reconcile` is that missing piece: a new CLI
(`src/reconcile/cli.py`, mirroring `src/ingest/`'s shape — console script only, no importable
surface, outside the `quant_data` namespace) that reads staging, promotes what agrees or can be
automatically explained, and defers genuine disagreements to a deliberate second pass rather than
blocking on manual review of every one — not viable at real ingest volume.

This file picks up where `tasks/ibkr-provider-reconciliation.md` (the original umbrella brainstorm
covering `dim_provider`/staging/IBKR-provider/reconciliation together) left off — that file's
schema (#18), IBKR provider (#21), and provider-wiring (#22) slices are all done; this is the
remaining, not-yet-built piece: reconciliation itself.

## Design decisions

Carried forward from `tasks/ibkr-provider-reconciliation.md`, already converged there:

- **Separate CLI, not folded into `--catch-up`.** `quant-ingest`'s job ends at "run every
  configured provider, write staging"; `quant-reconcile`'s job is "read staging, compare, promote
  to `fact_market_data_1min`." The two can be scheduled independently.
- **Wait for every configured provider.** If any *currently configured* provider
  (`settings.providers`) hasn't yet written a staging row for a given bar, that bar isn't
  evaluated at all yet — left alone until every provider has reported.
- **Staging rows are purged once a bar fully reconciles into fact** (i.e. once every consistency
  group for that bar has resolved — see "Field consistency groups" below).

**Field consistency groups** — fields that must come from a single provider together vs. fields
that resolve independently:

- **OHLC** (`open`/`high`/`low`/`close`) forms one group — must come from a single provider
  together, since assembling it from two different providers' individual fields risks an
  internally inconsistent bar (e.g. `low` > `close`).
- **Volume was originally its own independent group** — see "Superseded: volume as an
  independent group" below. As of `tasks/volume_reconciliation.md`, it no longer is: volume
  simply rides along with whichever provider wins the `ohlc` group.
- Modeled as a proper dimension, `dim_field_group` (mirrors `dim_ticker`/`dim_provider`'s shape),
  not a code-only mapping — extensible the same way those are: new fields added to
  `fact_market_data_1min` later get assigned to an existing group or a new group row at that time,
  a data change, not a schema change.
- **A bar promotes to `fact_market_data_1min` only once every group has resolved** — the fact
  table's columns are all `NOT NULL`, so there's no such thing as a partial row. Different groups
  of the same bar can resolve via different tiers at different times (e.g. OHLC via raw
  agreement, volume via completeness), and once a second candidate provider exists, different
  groups could come from different *candidates* — they're just never mixed *within* a group, and
  never come from the whistleblower outside of manual correction.

**Reconciliation outcomes are modeled as a proper fact/dimension pair**, not a bar-level status
flag:

- `dim_field_group(field_group_id, name)` — `'ohlc'`, `'volume'`, extensible.
- `fact_reconciliation(ticker_id, date_id, time_id, field_group_id, winning_provider_id,
  resolution_path, resolved_at)` — one row per resolved (bar, group). Presence of a row *is* "this
  group is resolved"; absence *is* "still stuck in staging" — no separate boolean/status column
  needed. `resolution_path` distinguishes how it resolved (`completeness` / `agreement` /
  `boundary_fix` / `finalized` / `manual_override` — see "Finalize" and "Manual correction" below
  for the difference between the last two).
- `fact_reconciliation_participant(ticker_id, date_id, time_id, field_group_id, provider_id, won)`
  — one row per provider that competed for that (bar, group), win or lose — **`yfinance` gets a
  row here too, like any other provider that wrote a staging row for that bar**, not just the
  candidate(s); it's a real participant (see "`dim_provider.role`" below), just one that's
  structurally never eligible to win outside manual correction. Needed because `dim_provider` is
  explicitly designed for more than two providers — a fixed `winning_provider_id`/
  `losing_provider_id` column pair only works for exactly two and would need revisiting the moment
  a third provider exists. This table doubles as the reputation record: "who tends to lose, over
  what window" is `WHERE won = FALSE` grouped by provider, filtered to
  `resolution_path = 'manual_override'` per the reputation design below — no separate reputation
  table needed. For `yfinance` specifically, `won = TRUE` rows (necessarily all
  `manual_override`, since that's its only path to winning at all) are themselves a meaningful
  signal: how often a person actually reached for the whistleblower's value specifically, as
  opposed to hand-typing something else entirely.
- **Retention: keep everything for now, revisit only if it becomes a real problem.** Sized
  roughly: `fact_reconciliation` grows at ~2× `fact_market_data_1min`'s row rate (one row per bar
  per field group), `fact_reconciliation_participant` roughly another ~2× on top of that with
  today's 2 providers — real growth, but deliberately not solved ahead of an actual need, matching
  this repo's existing posture on `tasks/scheduled_jobs.md` and `docs/SCHEMA.md`'s "no schema
  bloat ahead of an actual need." If it does become a problem, `docs/SCHEMA.md`'s existing
  planned-but-not-built partitioning strategy for `fact_market_data_1min` (declarative range
  partitioning by `date_id`) is the natural mechanism to reuse here too, rather than inventing a
  second one.

**`yfinance` is a whistleblower, not a peer — it never gets promoted to `fact_market_data_1min`
except via manual correction (see "Manual correction" below).** Its only job is to keep the real
candidate provider(s) — `ibkr` today — honest: an independent check to compare against, not a
second source of truth competing to win.

**This role lives on `dim_provider` itself, as data, not duplicated in `settings.json`.** New
column `dim_provider.role TEXT NOT NULL DEFAULT 'candidate' CHECK (role IN ('candidate',
'whistleblower'))`, seeded `ibkr` = `'candidate'`, `yfinance` = `'whistleblower'`. Chosen over a
`settings.reconcile.candidateProviders`/`whistleblowerProvider` settings-side list (an earlier
draft of this doc) once `yfinance` was decided to be a tracked `fact_reconciliation_participant`
row like any other provider (see above) — the role that governs *why* it never wins is squarely a
fact about the provider itself, and keeping it in the same table `quant-reconcile` already reads
avoids a second, independently-editable list that could drift out of sync with what's actually
seeded (e.g. someone adds a new provider to `dim_provider` but forgets to also update
`settings.json`). `quant-ingest`'s own `settings.providers` (which providers *write* to staging at
all) is unaffected and stays exactly as-is — `role` only governs reconciliation's read side.

**Disagreement variance lives in the database, per candidate provider, per field group — not a
per-provider "precision" that can't actually be derived.** With no ground-truth reference, there's
no way to statistically attribute observed disagreement to one side or the other (a known
identifiability problem — see "Why not per-provider precision" below); what *can* legitimately be
measured is the variance of the difference between a candidate and the whistleblower. New table
`provider_pair_disagreement(provider_id, field_group_id, sample_count, running_mean, running_m2,
stddev, updated_at)` — one row per provider with `dim_provider.role = 'candidate'` (currently just
`ibkr`; the whistleblower never appears in this table's `provider_id`, since it's always the fixed
other side of the comparison), tracking `Var(candidate − yfinance)` via Welford's algorithm,
`stddev` denormalized for fast reads.
No ticker dimension — same rationale as before, noise is a property of methodology, not of
individual tickers. (Earlier draft of this table had an ordered `provider_id_a`/`provider_id_b`
pair, for an imagined N-real-providers-compared-pairwise design — dropped once the whistleblower
framing made clear there's only ever one fixed comparison side, so no pair, and no ordering
ambiguity, is needed at all.)

- **`stddev` is stored as a relative/fractional value, not an absolute unit** — same reasoning as
  before: an absolute dollar/share figure can't mean the same thing across tickers of very
  different price/volume scale. Scaled against an actual reference value (e.g. neighbor-average
  price) at comparison time to get a bar-specific absolute tolerance.
- **The comparison tolerance comes directly from this measured pairwise value**:
  `tolerance = k × stddev_pair × reference_value`, **`k = 3`**. No `sqrt(σ_a² + σ_b²)` combination
  needed — the pairwise variance already *is* that combined quantity, measured directly rather
  than reconstructed from two individually-unmeasurable numbers.
- **Only Tier 2 (in-band) observations update the rolling variance.** Flagged/overridden
  disagreements from `--finalize`/manual correction deliberately do **not** feed back into it —
  including them would let exactly the outlier cases the threshold is meant to catch gradually
  widen "normal" and make the check less sensitive to future ones. Tier 3 boundary-fixes are also
  excluded, for the same reason: a resolved-but-still-real disagreement isn't "normal in-band"
  data.
- **Seeding (cold start)**: seeded with an illustrative starting value per field group (not
  measured data — revisit once real reconciliation history exists), pseudo-count 100 so it fades
  slowly:

  | provider (vs. `yfinance`) | field_group | stddev (relative) | sample_count |
  |---|---|---|---|
  | ibkr | ohlc | `0.0008` (8 bps) | 100 |
  | ibkr | volume | `0.03` (3%) | 100 |

**Why not per-provider precision (rejected)**: an earlier version of this design tried to derive
each provider's *individual* precision from reconciliation outcomes (lower-stddev-wins as the
tie-break, updating the winner's own stddev on every resolution). That's circular — with no third
independent reference, "the winner's deviation from itself" is trivially ~0 and never actually
tests whether the winner was right; the mechanism could only ever measure Yahoo's distance from
whatever IBKR said, never independently validate IBKR itself. If the IBKR-favoring belief were
ever wrong, nothing in that design could have caught it. Replaced with: honestly-measurable
pairwise disagreement (above) for threshold *width*, and a plain static preference for which
provider actually wins (below) — an explicit, revisable belief, not something the system pretends
to have derived.

**`preferredProvider` is a static, manually-set config value**
(`settings.reconcile.preferredProvider`, e.g. `"ibkr"`) — who wins whenever a choice has to be
made among providers with `dim_provider.role = 'candidate'`: Tier 2's tie-break among
in-band-agreeing providers, Tier 3's boundary-fix source, and `--finalize`'s fallback for flagged
disagreements all use this same single value. **It only ever ranges over `role = 'candidate'`
providers, never the whistleblower** — with exactly one candidate today, `preferredProvider` is
trivially `"ibkr"` and this setting isn't really doing any work yet; it starts to matter once a
second candidate provider exists and the two disagree with each other. This is a belief, not
derived data, and is expected to stay that way absent a real third reference to calibrate against
(see "Deferred" below for the closest thing to a path off this).

**Automatic pass — `quant-reconcile`, default behavior, no flag.** Runs per bar, per consistency
group, in this order — first tier that resolves a group wins:

1. **Completeness** — if exactly one provider (candidate or whistleblower) has valid data for the
   group (the other has a NaN/missing OHLC field, or a literal-zero placeholder volume — the
   existing "no real trade data" signal from `docs/ARCHITECTURE.md`) and the other's data is
   genuinely valid, the complete candidate provider's group wins outright, no tolerance comparison
   at all — this tier only ever promotes a *candidate*'s value, even if it's the whistleblower's
   completeness that made the call possible. If both are complete (or both genuinely zero/missing
   — a real no-trades minute), fall through.
2. **Raw agreement** — every field in the group agrees, within the pairwise-derived tolerance
   (see above), between the candidate and the whistleblower → promote the candidate's raw value
   (never the whistleblower's — see "`yfinance` is a whistleblower, not a peer" above). With one
   candidate today this is unambiguous; once a second candidate exists and both agree with the
   whistleblower but not each other, `preferredProvider` breaks the tie.
3. **Boundary-misalignment resolution** — for a group still disagreeing raw, compare a 3-bar
   windowed average (`t-1`, `t`, `t+1`) per provider per field against the same tolerance. If the
   windowed averages agree, promote the candidate's **raw** (not averaged/synthesized) value for
   that group — every promoted value is always something an actual provider reported.
4. **Still disagreeing (beyond the k-sigma pairwise-disagreement threshold)** → group stays
   unresolved; left in staging as the deliberate inspection window before `--finalize`. This is
   deliberately the trigger for a human glance, not just an automatic pass-through — a bar landing
   here means the candidate and the whistleblower disagree by more than their own normal historical
   pattern, worth a look before anything gets forced through to fact.

**Staging is the analysis window between the automatic pass and `--finalize`, by design — no
separate dry-run/report tooling.** `staging_market_data_1min` retains every provider's full raw
values for any unresolved bar/group, queryable directly (`psql`) at any point before `--finalize`
resolves it — the interface for spotting genuinely anomalous disagreement patterns before they get
force-resolved, and the same surface a future dedicated analysis tool would eventually read from.
**Kept as two distinct steps, not merged** — the deliberate pause to inspect staging (and
optionally retune `preferredProvider`, the tolerance `k`, or anything else in
`settings.reconcile`) before a value gets pushed to fact is exactly the point.

**`quant-reconcile --finalize`, run separately/later, once a human has looked.** Reads bars with
at least one group still unresolved after the automatic pass (i.e. beyond the k-sigma
pairwise-disagreement threshold), and applies **the currently-configured resolution algorithm** —
today, that's just "promote `preferredProvider`'s raw value" — to **fill in only the
still-unresolved group(s)**: full group, not cherry-picked fields, so OHLC internal consistency is
never at risk. Groups the automatic pass already resolved are left untouched. Once every group for
a bar has a value, the bar promotes and its staging rows purge. `--finalize` is manual in the
sense that a person chooses *when* to run it (typically after eyeballing staging and possibly
adjusting config) — the resolution itself, once run, is deterministic and algorithmic, not a
per-bar interactive prompt. "Promote `preferredProvider`" is deliberately the *only* algorithm in
scope for this task; a smarter one (e.g. pattern-informed) is real follow-up work, not designed
here (see "Deferred" below).

**Manual correction — a separate, always-available escape hatch, not part of `--finalize`.** A
person can directly hand-correct a specific bar/group (editing `staging_market_data_1min` or
`fact_market_data_1min` directly, no new tooling implied) whenever they've judged the algorithm's
answer — or the candidate provider's own data — to be actually wrong. **This is the only path
`yfinance`'s value can ever reach `fact_market_data_1min`** — `preferredProvider` never resolves
to the whistleblower, by construction, so the whistleblower can only get promoted if a person
deliberately overrides in. Recorded with `resolution_path = 'manual_override'`, distinct from
`--finalize`'s `resolution_path = 'finalized'`.

- **Reputation events (`fact_reconciliation_participant` rows with `resolution_path =
  'manual_override'`) fire only on true manual correction**, not on `--finalize`'s
  `preferredProvider` promotions (`resolution_path = 'finalized'`) or the automatic pass's own
  resolutions. Rationale sharpened from the earlier draft: agreement/completeness/boundary-fix
  aren't "someone was wrong" moments (unchanged), but neither is `--finalize` — it's an algorithm
  mechanically forcing `preferredProvider` through, not a human judgment that the candidate was
  actually correct. Only an actual person overriding a specific value is real evidence about which
  provider was wrong, so only that counts toward reputation.

**Updated (2026-08-03): explicit pending-manual-resolution queue, `fact_pending_manual_resolution`.**
The original design (Tier 4 above, and `--finalize`'s own description) treated "still stuck" as an
*implicit* state — inferred by absence of a `fact_reconciliation` row, with every plain
`quant-reconcile` run re-fetching and re-evaluating every such bar regardless of whether anything
relevant had changed since the last attempt. Under a realistic operational cadence (e.g. plain
`quant-reconcile` run daily, `--finalize` run only weekly) this compounds for days between any
relief, and would keep compounding indefinitely for any residual disagreement automatic tiers never
resolve (real anomalies are *supposed* to stay unresolved — that's the point of the state; the
re-processing cost was the actual problem, not the state itself). Fixed by making the state
explicit, following the same "presence of a row is the status" pattern `fact_reconciliation`
already uses:

- `fact_pending_manual_resolution(ticker_id, date_id, time_id, field_group_id, flagged_at)` — one
  row per (bar, group) that exhausted Tiers 1-3 within a run's automatic pass and still didn't
  resolve. Presence *is* "awaiting `--finalize`/manual correction"; absence (for a bar otherwise
  still in staging) means either not yet attempted at all, or not yet fully evaluated this run.
  Same grain as `fact_reconciliation`, on purpose — if a second field group is ever added, pending
  state stays trackable per-group, not just per-bar. Partial index on this table's own rows (it
  only ever holds the pending subset, so no `WHERE` qualifier needed on the index itself) keeps
  `--finalize`'s fetch fast regardless of how large `staging_market_data_1min` grows.
- **Plain `quant-reconcile` only ever fetches/evaluates (bar, group)s with no row here.** Once the
  automatic pass's fixed-point loop (see the seeding-lag fix above) concludes for a run and a group
  still hasn't resolved, it gets a row inserted here — a new write path; previously nothing was
  written for a stuck group at all. Every future plain run then skips it entirely, rather than
  paying fetch-and-evaluate cost for an outcome that can't have changed.
- **`--finalize` fetches *only* what has a row here** and runs the finalize algorithm directly (no
  Tier 1-3 re-attempt — already known to fail). It is deliberately **not** a superset of the plain
  pass; it doesn't also evaluate not-yet-attempted (bar, group)s. This is a clean two-step split —
  plain runs own "attempt and flag," `--finalize` owns "sweep what's flagged" — matching the
  intended cadence above. Consequence, accepted as correct rather than a bug: a `--finalize` run
  with no plain run ever having happened first has nothing to do yet.
- Once `--finalize` (or manual correction) resolves a pending (bar, group), its
  `fact_pending_manual_resolution` row is deleted as part of the same promotion — the table only
  ever holds what's genuinely still awaiting attention.
- Trades away automatic re-resolution of a flagged (bar, group) if conditions later improve enough
  that Tiers 1-3 would have cleanly resolved it (e.g. `tasks/per_ticker_disagreement.md`'s
  per-ticker training maturing after the flag was set) — accepted as a one-time, bounded cost:
  once a ticker's tolerance is properly calibrated, *future* bars simply won't reach this state in
  the first place, so this only affects bars already flagged before that point, not an ongoing drag.
- **Known, accepted gap in the lazy-purge interaction**: a `--finalize` run doesn't re-check
  purge-eligibility for an already-resolved bar whose only blocker was a *different*, still-pending
  neighbor (that neighbor isn't fetched in `--finalize`'s narrower scope, so it can't be seen as
  newly-unblocked). Self-heals on the very next plain run instead, which re-fetches the
  already-resolved bar (no Tier 1-3 re-attempt, since `fact_reconciliation` already has it) and
  re-checks the now-cleared neighbor. Not solved further, since the intended cadence bounds it to
  at most one extra day's delay.

**Live-verified against the real database, 2026-08-03.** `migrations/006_add_pending_manual_resolution.sql`
applied, then `quant-reconcile` run against the real leftover staging data from the full-dataset run
(9,006 rows: the 622 genuinely-stuck bars, ~400 already-resolved bars purge-blocked by a stuck
neighbor, and 6,962 single-provider gaps). **Result: 0 resolved, 622 newly marked pending manual
resolution** — matching exactly `DOG` 499 / `PSQ` 66 / `SH` 54 / `SPY` 3 (0 for `QQQ`/`DIA`, as
expected). A second run immediately after touched **0 rows newly** and its own
`fetch_staging_rows_for_reconciliation` fetch dropped from 2,044 rows to 800 — the 622 pending
bars (1,244 rows) are now excluded entirely, confirming the skip mechanism works against real
production data, not just the unit tests. `staging_market_data_1min` still holds all 9,006 rows
(marking pending doesn't purge — only `--finalize` or manual correction resolving a bar does).

Tracked as [issue #25](https://github.com/croicu/quant-data/issues/25), `status:ready-to-submit`
— implemented, tested, live-verified, and committed (`f5b192f`) alongside the rest of
`quant-reconcile`'s implementation.

**Noise-source coverage** — what the above actually handles vs. defers:

| Source | Handled by |
|---|---|
| Bar-boundary misalignment | Automatic pass, Tier 3 (windowed-average match) |
| Completeness / thin coverage (e.g. Yahoo premarket gaps) | Automatic pass, Tier 1 (per-group completeness) |
| Genuine market volatility (both providers correct) | Self-resolves at Tier 2 (raw agreement) — a tolerance-calibration concern, not a logic gap |
| Feed composition differences (busted prints, odd lots) | Deferred — looks like a genuine outlier, can't be told apart from a real move without deeper analysis |
| Systematic/sustained bias (missed split adjustment, scaling drift) | Deferred — near-term, `--finalize`'s `preferredProvider` algorithm just force-resolves these, possibly wrongly; accepted gap until the deferred tool exists (or a person catches it via manual correction) |

**Deferred — not part of this task, own future work**: a separate tool that reads bars still
unresolved *after the automatic pass but before `--finalize` resolves them*, inspects the actual
pattern of disagreement (adjacent-tick continuity, systematic per-candidate bias) and evolves
reconciliation criteria from real observed data. This is also the closest realistic path off a
purely static `preferredProvider`: without a third independent reference, this tool still can't
*prove* which candidate is more accurate, but it could surface enough pattern evidence (e.g.
consistent directional bias on a specific field) to justify a deliberate, human-reviewed change to
`preferredProvider` or to the field-group split — an informed revision to the belief, not an
automatically-derived one. Must run between the automatic pass and `--finalize`, since `--finalize`
consumes exactly the rows it needs.

**Schema-evolution merge path** (separate from new-bar reconciliation above): when a bar already
has a row in `fact_market_data_1min` and a later `quant-ingest` run writes a staging row
containing a field that doesn't exist in that fact row yet (added by a later migration),
reconciliation merges just the new field's group in once every configured provider has reported
it, subject to the same tiers above, scoped to that group alone. Already-published groups are
untouched — fact wins them unconditionally, staging's version isn't reconsidered even if it now
disagrees.

## Open questions

None remaining — design has converged (see resolved note below).

- ~~Migration numbering~~ — one bundled migration, `004`: `ALTER TABLE dim_provider ADD COLUMN
  role` (seeding `ibkr` = `'candidate'`, `yfinance` = `'whistleblower'` on the existing rows) +
  `CREATE TABLE dim_field_group` + `fact_reconciliation` + `fact_reconciliation_participant` +
  `provider_pair_disagreement`, same reasoning as `003`'s precedent: none of these are
  independently useful without the others.

## Implementation plan

Sliced the same way the earlier IBKR work was (schema #18 → provider #21 → wiring #22): schema
first, as its own narrow, independently-reviewable/applyable issue, with `quant-reconcile`'s
actual logic (automatic pass, `--finalize`, manual correction) as separate, later work.

**Schema-only slice** (this pass — no Python code, since nothing consumes these tables yet):

1. `migrations/004_add_reconciliation_tables.sql`: `ALTER TABLE dim_provider ADD COLUMN role`
   (seeding `ibkr` = `'candidate'`, `yfinance` = `'whistleblower'`), `CREATE TABLE
   dim_field_group` (seeded `'ohlc'`/`'volume'`), `CREATE TABLE fact_reconciliation`, `CREATE
   TABLE fact_reconciliation_participant`, `CREATE TABLE provider_pair_disagreement` (seeded with
   illustrative cold-start values), plus the two new indexes noted in `docs/SCHEMA.md`. Wrapped in
   `BEGIN`/`COMMIT`, records itself in `schema_migrations`, matching `001`–`003`'s existing style.
2. `docs/SCHEMA.md`: document all five new/changed pieces (`dim_provider.role`, `dim_field_group`,
   `fact_reconciliation`, `fact_reconciliation_participant`, `provider_pair_disagreement`) and the
   two new indexes, alongside the existing description.
3. Apply the migration against the real CroicuWS1 database via `psql` — **requires explicit
   go-ahead first**, per this repo's rule to confirm before running a migration against the real
   database.
4. Open a GitHub issue scoped to just this slice (schema only), labeled `status:implementation`,
   cross-linking back to this task file — same pattern issue #18 used for `003`.

`quant-reconcile`'s own CLI/logic (the automatic pass, `--finalize`, manual correction, and the
`preferredProvider`/`k` settings they read) is separate, later work — a follow-up issue once this
schema slice lands.

## Implementation plan (`quant-reconcile` CLI itself)

1. `Settings.reconcile` (`ReconcileSettings`: `preferred_provider` default `"ibkr"`, `k` default
   `3.0`), parsed from `settings.json`'s `reconcile` object (`preferredProvider`/`k`).
2. `src/reconcile/algorithm.py` — pure functions, no database access:
   `resolve_automatic` (Tiers 1-3), `resolve_finalize` (the `preferredProvider` fallback),
   `welford_update`/`stddev_from_stats`/`relative_diffs_for_stats_update` (the running-variance
   machinery). `ProviderBar`/`Resolution`/`DisagreementStats` are this module's own domain
   types — deliberately not shared with `quant_data._internal.shared.postgres`, which stays
   unaware of any reconciliation concept.
3. `quant_data._internal.shared.postgres.PostgresDatabase` gains reconcile-facing read/write
   methods (`fetch_dim_providers`, `fetch_dim_field_groups`, `fetch_provider_pair_disagreement`,
   `fetch_staging_rows_for_reconciliation`, `fetch_resolved_field_groups`,
   `record_reconciliation`, `promote_bar_to_fact`, `save_provider_pair_disagreement`) — same
   connection-owning class as `ingest`'s writes, not a second implementation. Returns plain row
   dataclasses (`ProviderRow`, `FieldGroupRow`, `StagingRow`, `DisagreementStatsRow`), keeping
   `quant_data._internal` acyclic with respect to `reconcile` (rule 8).
4. `src/reconcile/cli.py` — `quant-reconcile [--finalize] [--debug]`. `run_reconciliation` fetches
   every dimension/stats table plus every staging row belonging to a bar where every provider in
   `settings.providers` has reported (one bulk fetch per table, not a round trip per bar — Tier
   3's neighbor-minute lookups are served from the same in-memory staging rows via each row's own
   `timestamp` column), then resolves each not-yet-resolved (bar, field group), promoting a bar to
   `fact_market_data_1min` once every group has a resolution.
5. `pyproject.toml`: `quant-reconcile = "reconcile.cli:main"` console script; `src/reconcile/` is a
   new top-level package, same shape as `src/ingest/` (no importable surface).
6. `docs/ARCHITECTURE.md`/`docs/PROTOCOL.md`/`docs/SCHEMA.md` updated.
7. Unit tests: `tests/unit/test_reconcile_algorithm.py` (pure tier logic), `tests/unit/
   test_postgres.py` (new reconcile-facing methods, mocked `psycopg`), `tests/unit/
   test_reconcile_cli.py` (`run_reconciliation` end-to-end against an in-memory
   `FakeReconcileDatabase`, `tests/mocks/reconcile_database.py`), `tests/unit/test_settings.py`
   (`ReconcileSettings` parsing).
8. Live verification against the real CroicuWS1 database — **requires explicit go-ahead first**,
   same rule as the schema migration itself.

## Test results

**Schema-only slice: done.** Applied to the real CroicuWS1 database directly (via `psql` on the
box itself, as `quant_data`); verified independently, read-only, via `quant_reader`:

- `schema_migrations` records `004_add_reconciliation_tables`.
- `dim_provider.role`: `yfinance` = `'whistleblower'`, `ibkr` = `'candidate'`.
- `dim_field_group`: seeded with `'ohlc'`, `'volume'`.
- `provider_pair_disagreement`: seeded exactly as designed — `ibkr`/`ohlc` (100 samples, `stddev`
  `0.0008`), `ibkr`/`volume` (100 samples, `stddev` `0.03`).
- All 11 tables present (`dim_date`, `dim_field_group`, `dim_provider`, `dim_ticker`, `dim_time`,
  `fact_market_data_1min`, `fact_reconciliation`, `fact_reconciliation_participant`,
  `provider_pair_disagreement`, `schema_migrations`, `staging_market_data_1min`).
- `fact_reconciliation`/`fact_reconciliation_participant` empty, as expected — no Python code
  consumed them yet at that point.

**`quant-reconcile` CLI itself: implemented, unit-tested, not yet verified live.** 39 new unit
tests: 14 in `test_reconcile_algorithm.py` (each tier, the preferred-provider tie-break, Welford
update/stddev, relative-diff computation including the division-by-zero guard), 11 in
`test_postgres.py` (every new reconcile-facing method, commit/rollback), 10 in
`test_reconcile_cli.py` (`run_reconciliation` end-to-end: agreement promotes + purges staging,
completeness resolves the actual Yahoo-premarket-gap scenario, a bar missing a configured
provider is left untouched, a real disagreement stays stuck until `--finalize`, `--finalize`
promotes `preferredProvider`'s raw value, agreement updates `provider_pair_disagreement`, plus
`main()`/`parse_args` argument handling), 4 in `test_settings.py`
(`ReconcileSettings` parsing/defaults/validation). Full suite: 141 passed (the one pre-existing
IBKR integration test failure is environmental — no local IB Gateway running — not a regression).
`ruff format`/`ruff check` clean.

**Live-verified against CroicuWS1, 2026-08-03 — small deliberate sample, not the full dataset
yet.** Rather than run against the full 50,318-row backup immediately, `staging_market_data_1min`
was pared down (backup preserved first, per the confirm-before-real-DB-writes rule) to a
297-bar/594-row sample: all 6 configured tickers (`SPY`, `SH`, `QQQ`, `PSQ`, `DIA`, `DOG`), all 5
sessions (2026-07-27 through 2026-07-31), just the 10:00-10:09 ET window — small enough to
inspect every result by hand before trusting a full run.

- **First automatic pass**: 588 evaluated groups (294 bars × 2 field groups; 6 rows/3 bars
  excluded entirely as missing one provider's report), 503 resolved / 85 stuck. **OHLC resolved
  100% (294/294)** — 293 via agreement, 1 via completeness. **Volume was the entire gap**: 199
  agreement, 9 boundary-fix, 1 completeness, 85 stuck.
- **Seeding-lag re-runs**: re-running the plain automatic pass (no code/flag change) repeatedly —
  since a stuck group has no `fact_reconciliation` row, it's re-evaluated every run with
  whatever's current in `provider_pair_disagreement` — took the volume-stuck count from 85 → 68 →
  63 → 61, then **0 further change on a 4th run**: a real, empirically-found convergence floor.
  ~28% of the original stuck count (24/85) was purely the seed `stddev` (`0.03`) not yet having
  caught up to the real measured value (converged to `~0.047`) — not a real disagreement, not
  requiring any tolerance-logic change, just letting the running stats settle.
- **Tested and disproved**: hypothesized that 3 of the most extreme outliers (`DIA`/7-31,
  `SPY`/7-29, `SPY`/7-31, all at the window's first minute, 10:00 ET) were stuck only because
  Tier 3's windowed-average check had no `t-1` neighbor (deleted by the sample's own truncation).
  Restored the actual missing minutes (09:59/10:10 ET) from the backup and re-ran — **the 3 bars
  still didn't resolve**; their disagreement (IBKR at roughly 1/5 to 1/7 of Yahoo's volume) is too
  large for a 3-bar average to smooth over. The staging-only neighbor lookup (see below) is still
  a real gap worth fixing, but it doesn't explain this specific pattern — an asserted cause that
  didn't survive being tested.
- **Ticker-level pattern in the 61-bar convergence floor**: volume stuck rate was sharply
  ticker-dependent — `QQQ` 43%, `DIA` 43%, `SPY` 20%, `DOG` 15%, `PSQ` 2%, `SH` 0% — and
  consistent in direction across every long/inverse pair (long always worse: `SPY` > `SH`, `QQQ`
  > `PSQ`, `DIA` > `DOG`). Argues against "thin volume inflates the % diff" as the main driver,
  since the inverse ETFs are the thinner-traded names and agree almost perfectly. Logged as
  grounding data for `tasks/index_composite_check.md`'s "volume anomaly signal shape" open
  question, not yet explained.
- **Structural gap identified, then fixed (2026-08-03)**: Tier 3's windowed neighbor lookup only
  searched `staging_market_data_1min`. Once a neighboring bar was fully promoted, its row was
  purged, and any bar next to it permanently lost that neighbor for boundary-fix on every future
  run. Fixed via **lazy purge**, not a fact-table fallback (see below) — `run_reconciliation` now
  defers purging a resolved bar's staging rows until neither `t-1` nor `t+1` (same ticker) is still
  unresolved, so the data survives exactly as long as a future run might genuinely need it.
- **Seeding lag: also fixed (2026-08-03)**. The re-run-until-it-stops-changing behavior found above
  is now automatic, not manual: `run_reconciliation` repeats its pass over `bars` internally until
  one resolves nothing new, so a single `quant-reconcile` invocation reaches the same convergence
  floor that previously took four manual re-runs. Both fixes are mechanical, not a change to the
  tolerance/decision logic itself — see "Superseded: volume as an independent field group" below
  for the earlier distinction between the two categories of fix; the seed *values* themselves
  (`stddev`, pseudo-count 100) are unchanged.
- Full 50,318-row backup restored to `staging_market_data_1min` on the local machine, then pruned
  to a second small deliberate sample (all 6 tickers, all 5 sessions, the 09:30-09:39 ET window
  this time — the market open — 599 rows) to live-verify the volume removal and the two fixes
  above before trusting a full run. **Result: 299 groups resolved, 0 stuck, on a single
  `quant-reconcile` invocation** — no
  manual re-running needed. All 299 resolved via `agreement`. `fact_market_data_1min` got exactly
  299 rows; `staging_market_data_1min` was left with exactly 1 row, the one bar in the window
  missing one provider's report — correctly excluded from reconciliation and correctly untouched,
  not force-processed. Every other bar's staging rows were fully purged, confirming lazy purge
  doesn't leave stray rows behind once nothing needs them as a neighbor. `provider_pair_disagreement`'s
  `ohlc` stats were already well-converged from the earlier live run, so this wasn't a cold-seed
  stress test of the fixed-point loop specifically — but it is the first clean, real, end-to-end
  confirmation that the redesigned pipeline (no volume field group, internal convergence, lazy
  purge) works correctly against real data. The full 50,318-row dataset itself has not yet been
  run through `quant-reconcile` — still pending its own explicit go-ahead.
- **Third live sample, deliberately pre-market this time**: backup restored again, pruned to
  05:00-05:09 ET (454 rows, real pre-market coverage — not full: `ibkr` had 290 rows in this window
  vs. `yfinance`'s 164, `SPY`/`QQQ` had complete both-provider coverage while `DIA`/`DOG`/`PSQ`/`SH`
  didn't). **Result: 162 groups resolved, 0 stuck — every single one via `completeness`, not
  `agreement`, and the winner was `ibkr` 162/162 times.** This is the scenario the automatic pass's
  Tier 1 was explicitly designed around (Yahoo premarket gaps), and it's the first real data
  confirming it works exactly as designed: whenever one side had real data and the other was
  incomplete, the complete side won outright, no tolerance check involved.
  `provider_pair_disagreement` didn't move at all this run (`agreement` never fired). 130 of the
  454 rows were single-provider bars, correctly excluded from reconciliation entirely rather than
  force-processed — overwhelmingly `yfinance`'s row missing outright for `DIA`/`DOG`/`PSQ`/`SH`
  (zero such gaps for `SPY`/`QQQ`), with two small exceptions where `DOG` was missing *`ibkr`'s* row
  instead (2026-07-29, 05:00 and 05:07 ET) — so "Yahoo has premarket gaps" held up as a real pattern
  here, but isn't a universal rule worth hard-coding an assumption on.

  *(Correction: the two bullets above originally said 13:30-13:39 ET / 09:00-09:09 ET —
  `dim_time.time_of_day` turned out to be literal UTC, not ET as assumed at the time. Verified
  directly against the real `timestamp` column during the DOG investigation below. Corrected to
  09:30-09:39 ET and 05:00-05:09 ET respectively; the underlying data/analysis was unaffected, only
  the labels were wrong.)*

- **Full 50,318-row dataset, 2026-08-03**: the real run. 1,235 seconds (~20.6 min) — `quant-reconcile`'s
  writes are one round trip per bar (same unbatched profile as issue #23), so this scales roughly
  linearly with bar count; ~25K bars landed almost exactly where a round-trip-count estimate
  predicted. 43,356 of 50,318 rows belonged to two-provider-complete bars (21,678 bars); 6,962 rows
  were single-provider gaps, correctly excluded. Of the 21,678: 461 already resolved from the two
  earlier sample tests (idempotently re-promoted), **20,595 newly resolved, 622 genuinely stuck**
  (2.9%). `fact_market_data_1min`: 21,056 rows. All-time resolution-path breakdown:
  **`completeness` 10,599 (50%), `agreement` 9,517 (45%), `boundary_fix` 940 (4.5%)** — completeness,
  not agreement, is the single largest path across a full week, meaning "one side has real data, the
  other doesn't" is the common case, not the exception. Staging showed 1,022 two-provider bars
  remaining, not 622 — confirmed via `fact_reconciliation` that 400 of those are already-resolved
  bars whose purge is deferred because an adjacent minute is still stuck (lazy purge doing real work
  for the first time at a scale where stuck bars have neighbors worth deferring for), leaving the
  true 622 stuck. `provider_pair_disagreement`'s `ohlc` stddev kept tightening with more real data:
  `0.000180` → `0.0000736` (sample_count 39,576).

- **The 622 stuck bars are sharply concentrated in `DOG`** (499), followed by `PSQ` (66), `SH` (54),
  `SPY` (3), with `QQQ`/`DIA` at 0 — investigated in depth; findings and the resulting design change
  are in `tasks/per_ticker_disagreement.md`. Summary: neither ticker-average volume, per-bar volume
  within `DOG`, nor price level explain the gap — the *true* stuck rate (computed correctly against
  `fact_reconciliation`, not the purge-biased staging leftovers) is `DOG` 25.9% vs. `PSQ`/`SH`
  ~1.8% vs. `SPY`/`QQQ`/`DIA` ~0%, a categorical outlier rather than a smooth gradient. `DOG`'s
  stuck-bar diff *magnitudes* are similar to or smaller than `PSQ`/`SH`'s — it's not that `DOG`'s
  disagreements are bigger, they're just far more frequent, which points at the pooled (all-ticker)
  `provider_pair_disagreement` tolerance being distorted by `QQQ`/`SPY`/`DIA`'s much larger volume
  of tight agreements. Led directly to `tasks/per_ticker_disagreement.md`'s per-ticker stats design
  (brainstorm stage, converged on the training/graduation mechanism, not yet implemented).

## Superseded: volume as an independent field group

The findings above — 61/294 volume groups genuinely stuck, sharply and consistently
ticker-dependent, not explained by seeding lag or missing neighbors — prompted reconsidering
whether volume should be reconciled against `yfinance` at all, rather than continuing to adjust the
tolerance/tier logic to fit what was observed (see [[feedback_infra_fix_vs_tuning]]). Two reasons
volume was never a good fit for this mechanism in the first place, from how it's actually used
downstream: it's a relative, provider-tied confidence signal (e.g. "50% of a normal session's
volume"), not a value needing independent cross-provider corroboration the way price does; and
`yfinance` (the whistleblower) has no pre-market volume data at all, which is exactly the window
this project trades in, so the whistleblower comparison was never meaningful there.

**Design and implementation now live in `tasks/volume_reconciliation.md`.** Everything in this
file describing volume as an independent `dim_field_group` row with its own Tier 1-4 resolution and
`provider_pair_disagreement` tracking is historical — it describes the design as originally built
and live-tested, not the current behavior. Volume now simply rides along with whichever provider
wins the `ohlc` group for that bar.
