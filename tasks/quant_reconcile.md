# quant-reconcile

## Status: Brainstorm overall (design converged, no open questions remaining) — schema-only slice
done, applied to the real database, verified — issue #24, `status:ready-to-submit`.
`quant-reconcile`'s own CLI/logic (automatic pass, `--finalize`, manual correction) is not started.

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
- **Volume** is its own independent group.
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
  consumes them yet.

No automated tests (no Python code changed). `quant-reconcile`'s own CLI/logic remains open —
tracked as a follow-up issue once this schema lands.
