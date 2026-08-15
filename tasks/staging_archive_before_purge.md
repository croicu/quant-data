# Staging Archive Before Purge

## Status: Brainstorm

## Problem statement

`purge_staging_bar` deletes a bar's `candidate` (`ibkr`) staging rows once resolved and no longer
needed as a Tier-3 neighbor — but nothing preserves that raw data first. This violates the "No
information loss during the data processing stage" principle in `CLAUDE.md`'s Data Pipeline
Principles section: `fact_market_data_1min`/`fact_reconciliation` are supposed to stay re-creatable
from `staging_market_data_1min` alone, but once a candidate row is purged, that's no longer true for
its bar.

Concrete evidence (2026-08-07, investigating whether `quant-reconcile` could be re-run against a
"fresh" dataset without re-ingesting): of 52,953 resolved `fact_market_data_1min` bars, only 4,101
still had *both* providers' original staging rows intact. 41,913 had only one provider's row
surviving (almost always the `whistleblower`, since `yfinance` rows are permanently purge-exempt —
the `candidate` side was gone). 6,939 had *nothing* left in staging at all. Reconstructing a
"pristine, just-ingested" dataset for testing required restoring `fact_market_data_1min` from a
backup and re-deriving `candidate` staging rows from the winning value — which only recovers the
*winner's* value, never the actual `ibkr`-vs-`yfinance` comparison that originally happened. The
real disagreement evidence is just gone for the vast majority of already-resolved history.

## Design decisions

- **Chosen direction: archive instead of delete (not "stop purging").** Two options were
  considered:
  1. Archive a candidate row to a separate, never-pruned table immediately before
     `purge_staging_bar` deletes it from the working `staging_market_data_1min` table. Keeps the
     working table lean (so `_run_outlier_detection_pass`/`fetch_staging_rows_for_reconciliation`'s
     full-table sweep every run — deliberate, see `tasks/yahoo_data_sanitization.md` — stays fast)
     while making the pipeline genuinely reproducible.
  2. Just stop purging candidate rows at all. Simpler, and given this project's current scale (a
     handful of tickers, ~100K rows after weeks of data) might not bite for a long time — but both
     of the sweep-everything call sites above would grow unboundedly and slow down every future run
     as a direct consequence, not just grow disk usage.
  - Picked (1): keeps the existing full-sweep performance characteristics intact rather than
    trading them away for simplicity.

## Design decisions (2026-08-13 follow-up — expanded scope)

Picked back up after a full session away; expanded beyond the original archive-before-purge scope
to also cover `dim_provider` gaining `'manual'` and `'databento'` entries and `fact_market_data_1min`
becoming traceable back to its archived source record(s), not just re-derivable via
`staging_market_data_1min` while a bar's raw rows still happen to survive.

- **Enforcement stays code-level, not a new DB role.** Considered giving reconcile a narrower role
  than `quant_writer` (no direct `UPDATE`/`DELETE` on staging, only a privileged archive-move
  function it could call) to make "only ingest writes staging values" a real DB-level guarantee —
  rejected: Postgres grants aren't fine-grained enough to express "may delete a row it's archiving,
  may not otherwise touch staging content" as a distinct role, and reconcile already needs (and has,
  via `quant_writer`) the ability to remove staging rows once resolved — that's `purge_staging_bar`
  today. "Staging modified only by ingest" means only ingest ever writes/updates bar *values* into
  staging; reconcile's write access stays scoped to archiving-then-removing rows it's already
  responsible for purging, just formalized as archive-then-delete instead of delete-only.
- **Archive scope: candidate only, as originally planned.** Confirmed against the alternative (move
  *every* staging row, including the whistleblower's, ending `yfinance`'s permanent staging
  retention). Whistleblower behavior is unchanged: still never purged, still lives in staging
  forever, same as today. Consequence: `MarketData.fetch_rejected_whistleblower_bars` needs no
  changes — it already depends on whistleblower rows staying in `staging_market_data_1min`
  permanently, and that invariant holds.
- **`fact_market_data_1min` → archive reference: extend `fact_reconciliation_participant`, not a new
  junction table.** It's already exactly the right grain — one row per provider that participated in
  resolving a (bar, field group), win or lose. Gains a nullable `archive_id` FK: populated for a
  participant once its staging row is archived, `NULL` for a participant whose row was never
  archived (the whistleblower, per the point above, and any provider that hasn't been purged yet).
  This reuses the existing reputation-tracking table rather than adding a parallel structure.
- **`dim_provider` gains `'manual'` and `'databento'`, both role `'advisor'` — neither ever writes
  to `staging_market_data_1min`.** New `dim_provider.role` value `'advisor'` (alongside existing
  `'candidate'`/`'whistleblower'`): "can suggest a value but doesn't have authoring rights" — unlike
  `'candidate'`, an advisor provider can never autonomously win a bar through the automatic Tier 1-3
  pass; it can only become a bar's value through an explicit human action. Resolves the blocking
  question in `tasks/databento_stuck_bar_verification.md` ("where would a DataBento value even
  live") in favor of the narrower, already-favored framing there: an opt-in, one-off verification
  signal, not a routine third staging-writing provider.
- **Two finalize APIs, distinguished by which one is called — not by comparing values.** Resolves
  the manual-vs-whistleblower-reputation question below by making attribution an act, not a
  value-equality check:
  1. **Accept candidate/whistleblower** — human selects one of the two already-staged raw values.
     `winning_provider_id` stays that provider's own id (`ibkr` or `yfinance`), exactly as
     `manual_override` behaves today. No new archive entry beyond the candidate's own normal
     archival (per "archive scope" above) — if the whistleblower's value is the one picked, it's
     still never archived, per its permanent-staging-retention behavior being unchanged.
  2. **Accept value** — human supplies an explicit value directly. `winning_provider_id` is always
     `'manual'`, unconditionally — **even if that value is numerically identical to the candidate's
     or whistleblower's own reported number.** Attribution is determined by which API was invoked,
     not by comparing the resulting value against what's already on staging. Always creates a new
     archive row (bypassing staging entirely) plus a matching `fact_reconciliation_participant` row
     (`provider_id` = `'manual'`, `won = TRUE`, `archive_id` populated).
  This fully preserves the existing whistleblower-reputation signal ("how often a person actually
  reached for its value specifically") — that signal only comes from API 1, and is untouched by API
  2's existence.
- **`'databento'` has zero footprint in the archive or `fact_reconciliation_participant` — it is
  purely an out-of-band reference a human consults before calling one of the two APIs above.**
  Neither API names `databento` as the thing being accepted; a human looks at what it suggests, then
  calls "accept candidate/whistleblower" or "accept value" based on their own judgment. The
  `dim_provider` row exists for identity/documentation purposes (naming a known source), consistent
  with `dim_provider` already being "not hardcoded to exactly two rows" — not because anything in the
  schema references it.

## Open questions — all resolved (2026-08-13)

- **Archive table schema: loosely mirror `staging_market_data_1min`.** Not a strict 1:1 copy —
  optimized for "trivial to extend later" (new columns expected over time) over "leanest possible
  audit log." Plus the surrogate `archive_id` PK `fact_reconciliation_participant.archive_id`
  references — the natural key (`provider_id`, `ticker_id`, `date_id`, `time_id`) alone can't be a
  foreign-key target from a table that also needs to reference `'manual'` rows with no corresponding
  staging row ever having existed.
- **Where archiving happens: inline in `purge_staging_bar`** (archive-then-delete as one unit) for
  the candidate-purge path. For the "accept value" API, either routing the manual entry through
  staging first (reusing the same archive-then-delete path candidates already use) or writing
  directly to the archive table are both acceptable — bars aren't correlated, so individual vs.
  batched writes are equivalent either way. Left as an implementation-time call: pick whichever
  turns out simpler to code once the archive-write plumbing exists, not a decision to pre-commit to
  now.
- **Read access: `quant_reader` gets a grant on the archive table too**, same as every other table
  so far — deliberately not introducing a more granular account/role split just for this one table.
- **Retention: perpetual.** Never pruned, explicitly, not just "presumably."

**Decided (2026-08-13): cold-storage export is out of scope.** Considered moving archived rows out
of Postgres entirely onto cheaper storage — would have forced `fact_reconciliation_participant
.archive_id` to be an unenforced reference instead of a real FK (Postgres can't enforce a constraint
against a row that's been moved out of the database), plus denormalizing the archive table's own
dimension FKs so an exported row stays self-describing. Rejected: CroicuWS1 has 10TB available,
comfortably enough to hold the archive table indefinitely at this project's scale — `archive_id`
stays a normal enforced FK. A same-instance tablespace move (cheaper/slower disk, same logical
table) remains available later with zero schema impact if it's ever needed, since that doesn't
involve breaking any FK.

## Implementation plan

<!-- Added when advancing to Implementation. -->

## Test results

<!-- Added when advancing to Testing / Ready to Submit. -->
