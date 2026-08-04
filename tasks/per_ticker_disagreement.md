# Per-Ticker Disagreement Stats

## Status: Brainstorm

## Problem statement

`provider_pair_disagreement` (per `tasks/quant_reconcile.md`) deliberately has no ticker
dimension: "noise is a property of methodology, not of individual tickers." The first full-dataset
live run of `quant-reconcile` (2026-08-03, one week of real data, 6 tickers) shows that assumption
doesn't hold.

622 of 21,678 evaluated bars (2.9%) stayed stuck on `ohlc` — the first real OHLC disagreement seen
at all (every earlier small-sample test resolved 100%). The stuck rate is sharply, categorically
ticker-dependent, not a smooth gradient:

| ticker | true stuck rate |
|---|---|
| `DOG` | **25.9%** |
| `PSQ` | 1.87% |
| `SH` | 1.84% |
| `SPY` | 0.06% |
| `QQQ` / `DIA` | 0% |

Two hypotheses were tested directly against the data and both were disproven:
- **Ticker-average volume**: doesn't rank-order the stuck rates (`PSQ` has *higher* average volume
  than `DOG` yet far fewer stuck bars).
- **Per-bar volume within a ticker**: tested directly within `DOG` (the cleanest test, since it
  controls for ticker) — stuck bars actually have *higher* volume than `agreement`-resolved ones,
  not lower. `boundary_fix`-resolved bars have the highest volume of all. If anything, the
  relationship runs opposite to a naive "thin bar -> noisier bar" story.
- **Price level**: also doesn't explain it — `PSQ` ($27) and `SH` ($33) are similarly cheap to
  `DOG` ($22) but sit together at ~1.8%, an order of magnitude below `DOG`'s 25.9%.

What *does* distinguish the finding: `DOG`'s stuck-bar disagreement *magnitude* (both absolute-$
and relative-%) is actually similar to or smaller than `PSQ`/`SH`'s stuck-bar magnitudes — it's not
that `DOG`'s disagreements are bigger, they're just far more frequent. This points at the pooled
tolerance itself: `provider_pair_disagreement`'s single global `stddev` is measured across all
tickers combined, dominated by `QQQ`/`SPY`/`DIA`'s much larger volume of tight agreements. A ticker
whose normal noise band is genuinely wider than the pooled average gets squeezed by a
threshold calibrated on everyone else's tighter behavior. A per-ticker `stddev` is the standard fix
for this kind of pooled-variance distortion (heteroscedasticity across groups) — this is a
structural/statistical argument, not "adjust the number until DOG passes" (see
`tasks/quant_reconcile.md`'s "Superseded" section and the infra-fix-vs-tuning distinction it
references for why that line matters).

A specific root cause (e.g. an overnight NAV-reset artifact specific to inverse ETFs) was
considered and not confirmed — `DOG`'s stuck bars are spread through the mid-morning/early-afternoon
window, not clustered right at the session open the way a reset-related mismatch would predict, and
a cleaner test of the overnight-adjustment idea was blocked by lazy purge having already discarded
most of the raw two-sided data needed to check it. Per-ticker stats aren't conditioned on finding
that root cause — they're motivated directly by the demonstrated rate disparity.

## Design decisions

- **`provider_pair_disagreement` gains a `ticker_id` column**, extending its primary key to
  `(provider_id, ticker_id, field_group_id)`. Each candidate/ticker/field-group combination gets
  its own independently-measured `Var(candidate - whistleblower)`.
- **No artificial seed, for any ticker, ever.** The illustrative cold-start seed (migration 004's
  original `0.0008`/`0.03` guesses, later updated to a real measured pooled value) goes away
  entirely as a concept, not just as a specific number. A ticker's row starts at
  `sample_count = 0` and only ever contains genuinely measured data for that specific ticker.
- **"In training" — a ticker with fewer than the graduation threshold's worth of samples doesn't
  promote *anything* to `fact_market_data_1min`, no exceptions, including completeness.** Even
  though Tier 1 (completeness) doesn't need a `stddev` at all, promotion is still withheld until
  the ticker graduates, for a simple, consistent mental model: a ticker is either training or not,
  with no partial states to reason about.
- **Graduation threshold is a sample-count cutoff**, same unit Welford already counts in (one
  sample per field per in-band observation) — likely `100`, matching the old pseudo-count's
  magnitude, though this is still open (see below).
- **Bootstrapping the first samples is a genuine chicken-and-egg problem, resolved by a
  train/graduated split in how updates happen, not just in whether promotion happens**: Tier 2
  today only updates stats on an actual in-band agreement — but a ticker with no stats yet has no
  tolerance to check against, so it could never produce one. During training, every matched (both
  providers report real, non-incomplete data) bar's raw diff feeds the Welford update
  *unconditionally* — there's no established baseline yet to protect from outlier contamination,
  so there's nothing to gate. Once the ticker crosses the graduation threshold, the existing
  anti-drift rule (`tasks/quant_reconcile.md`: "Only Tier 2 observations update the rolling
  variance") takes over exactly as designed today.
- **New tickers get a real backfill, not a statistical shortcut.** When a ticker is newly added, an
  operator runs `quant-ingest` with `startDate`/`endDate` covering the same historical window
  already present for existing tickers (already-existing capability, no new ingest code) before
  relying on reconciliation for it — so training data is 100% real and ticker-specific, arriving in
  one batch rather than trickling in day-by-day. Explicitly rejected: borrowing a blended/pooled
  starting `stddev` from already-trained tickers — that's a smarter seed, but still a seed, and
  contradicts "no artificial seeds" as a standing principle rather than a one-time migration fix.
- **Existing pooled production data gets discarded, not migrated forward.** The current single
  pooled `ohlc` row (`sample_count` 39,576 as of the full-dataset run) can't be meaningfully split
  back into per-ticker components after the fact — it was never tracked that way. The per-ticker
  migration starts every ticker at zero and re-earns its own history from the next
  `quant-reconcile` run onward.
- **Known, accepted tradeoff — not solved by this task**: training a ticker's tolerance on its own
  history fixes false positives (a ticker's genuinely-normal noise wrongly flagged) but does nothing
  for false negatives (a real data-quality problem specific to that ticker gets baked into "normal"
  and stops being caught). `DOG` is the concrete case: its own 25.9% stuck rate is unexplained, and
  per-ticker training would make its own checker the loosest one in the system. A genuinely
  independent fix exists (`tasks/inverse_pair_cross_check.md` — using `DOG`/`SH`/`PSQ`'s
  already-reliable long counterpart as a third-reference validator) but is explicitly postponed:
  for signal-research purposes the inverse tickers' own data quality isn't currently load-bearing,
  so this task proceeds without that safeguard as a prerequisite, by deliberate choice.

## Open questions

- **Is `100` actually the right graduation threshold?** It matches the old pseudo-count's order of
  magnitude, but that number was itself illustrative, not derived. Worth deciding deliberately
  rather than carrying it forward by default.
- **Threshold unit**: samples (one per field per bar, so ~25 bars fully clears 100 for the 4-field
  `ohlc` group) vs. bars vs. calendar days/sessions observed. A pure sample-count threshold can be
  satisfied by a burst of same-session activity; is that acceptable, or should graduation also
  require some minimum spread of calendar days (guards against one unusual day looking like enough
  history)?
- **Does this fully replace the seeding-lag fixed-point loop, or complement it?** The fixed-point
  loop (tasks/quant_reconcile.md) fixed "how many manual re-runs to converge within a given amount
  of data"; per-ticker training fixes "the starting point is a guess" more fundamentally. Both
  remain useful together (the fixed-point loop still matters for reaching convergence quickly
  within a single run once a ticker has *some* data), but worth confirming neither becomes dead
  code.
- **Migration numbering/sequencing**: this is schema + data + algorithm work together (unlike
  volume removal's schema-only migration) — worth deciding whether to slice it (schema-only
  migration first, algorithm changes as separate follow-up) or ship together, given this repo's
  general preference for narrow, independently-reviewable slices (see the original IBKR/reconcile
  work's schema-then-code-then-verify pattern).
- **Interaction with `fact_pending_manual_resolution`** (`tasks/quant_reconcile.md`, implemented
  separately from this task): once a ticker graduates and its tolerance is properly calibrated,
  fewer of its bars should ever reach the pending-manual state in the first place — worth
  confirming there's no ordering dependency between the two once both exist.

## Implementation plan

<!-- TBD -- pending the open questions above. -->

## Test results

<!-- TBD -->
