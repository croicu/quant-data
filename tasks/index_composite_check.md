# Index Composite Check

## Status: Brainstorm

## Problem statement

`quant-reconcile`'s design (see `tasks/quant-reconcile.md`) deliberately defers a category of
disagreement it can't currently resolve well: feed composition differences (busted prints, odd
lots, single-ticker glitches) that look like a genuine outlier and can't be told apart from a real
price move using only the two providers being reconciled (`yfinance`, `ibkr`) — there's no third
independent reference to check against.

An index ETF (SPY, QQQ, DIA) offers a way to construct exactly that kind of reference for a single
ticker's OHLC, without needing a third data provider at all: since a cap-weighted index's level is
(approximately) the weighted sum of its constituents' prices by construction, an *expected* OHLC
for the ETF can be computed directly from its constituents' own already-ingested OHLC bars. If a
constituent's bar has a bad print or boundary-alignment artifact, that noise is largely isolated to
that one ticker and mostly cancels out across hundreds of independent constituents — so a
significant divergence between the ETF's actual reported bar and its composite-derived expected
bar is a meaningful anomaly signal, distinct from ordinary two-provider disagreement.

## Design decisions

- **Does not affect the main warehouse schema.** This is an analysis/detection tool that reads
  already-ingested/reconciled OHLC data and computes a derived comparison — it is not a new fact
  table, dimension, or column on `fact_market_data_1min`/`staging_market_data_1min`, and doesn't
  change `quant-ingest`/`quant-reconcile`'s existing behavior. Likely lives as its own tool/script,
  not a modification to the existing schema.
- **Scope: single-ticker anomaly detection, not a general third-provider substitute.** This
  approach only isolates noise specific to *one* constituent's bar; it does not help with a
  provider-wide systematic bias (e.g. a missed split adjustment applied to many tickers at once),
  since a bias baked into the underlying provider data would be inherited by every constituent
  feeding the composite, and by the composite itself. Positioned as filling the specific
  "feed composition differences" gap `quant-reconcile` explicitly deferred, not as a full
  replacement for a genuine independent third data source.
- **Price aggregates meaningfully; volume does not, and is a much weaker signal.** A cap-weighted
  index's price has a real arithmetic relationship to its constituents' prices, which is why the
  composite-OHLC check works at all. An ETF's own share volume (trading activity in the ETF on the
  secondary market) has no equivalent identity with the sum of its constituents' volumes — the two
  are driven by largely unrelated forces (index-level sentiment/hedging flow vs. per-stock
  news/earnings). Volume is being kept in scope per explicit request, but understood upfront as a
  coarse, day/session-level anomaly flag at best (broad volatility regimes, and known scheduled
  events like quarterly rebalances/quad-witching that cause real correlated volume spikes across
  the ETF and its constituents) — not a per-bar validation the way the OHLC composite is.
- **DIA needs separate handling from SPY/QQQ.** The Dow is price-weighted via a periodically
  adjusted "Dow Divisor" (changed for splits/spinoffs, not on a fixed calendar), not cap-weighted
  by share count like SPY (S&P 500) and QQQ (Nasdaq-100) — the composite formula and its
  weight-refresh trigger differ structurally for DIA and can't reuse SPY/QQQ's logic directly.
- **Empirical grounding (from `quant-reconcile`'s first live test, 2026-08-03)**: a small sample
  (6 tickers × 5 sessions × the 10:00-10:09 ET window, IBKR vs. Yahoo, full details in
  `tasks/quant-reconcile.md`'s test results) gives this task its first real data instead of
  speculation:
  - **OHLC resolved 100% (294/294 bars); volume didn't (61/294 genuinely stuck after stats
    converged).** Confirms this file's existing assumption above ("price aggregates meaningfully;
    volume does not") in the most direct way possible — in this sample, price is exactly the part
    that already agrees, and volume is exactly the part that doesn't. A composite-price check
    would have nothing to catch here; the actual gap is on the side this file already flagged as
    a weak fit for that mechanism.
  - **Volume disagreement is sharply ticker-dependent**: stuck rate (fraction of bars where
    IBKR/Yahoo volume differs beyond measured tolerance) was QQQ 43%, DIA 43%, SPY 20%, DOG 15%,
    PSQ 2%, SH 0%. Not remotely uniform across tickers.
  - **Every long/inverse pair shows the same direction**: the long ETF disagrees more than its
    inverse counterpart — SPY (20%) > SH (0%), QQQ (43%) > PSQ (2%), DIA (43%) > DOG (15%),
    consistently. This weighs *against* "thin/low volume amplifies the relative diff" as the main
    driver — the inverse ETFs are the thinner-traded names here, and they're the ones agreeing
    almost perfectly. Points more toward something specific to heavily-traded, multi-venue names
    (unconfirmed hypothesis: consolidated-tape vs. primary-exchange volume attribution differing
    more where there's more off-exchange/dark-pool activity to attribute) than to a generic
    small-number-of-shares artifact.
  - **Useful coincidence**: SPY/QQQ/DIA — the only three tickers this task's composite-check
    mechanism can actually apply to (SH/PSQ/DOG have no constituent basket to reconstruct from) —
    are also the three showing the worst volume disagreement. That's not the same as this
    mechanism being able to *explain* it, though (next bullet).
  - **Caveat, so this doesn't get overclaimed later**: none of the above means the composite-check
    tool will explain this specific pattern once built. Its mechanism (index price ≈ weighted sum
    of constituent prices) has no volume equivalent, and this file already scopes its volume
    signal as coarse/day-level, not per-bar — it's not positioned to say *which specific minute's*
    IBKR-vs-Yahoo volume diff is real disagreement vs. artifact. The QQQ/DIA pattern above still
    needs its own investigation; this task is grounding for the "volume anomaly signal shape"
    open question below, not a solution to today's puzzle.
- **Index composition and weights are their own ongoing maintenance burden, not a one-time setup.**
  Two different update cadences apply:
  - **Constituent membership** (which stocks are in the index at all) changes infrequently —
    scheduled quarterly reviews (S&P 500, Nasdaq-100: third Friday of March/June/September/
    December), typically only a handful of additions/removals per quarter, announced just 5-7
    business days ahead, plus occasional off-cycle changes (M&A, bankruptcy, eligibility loss).
    Worth noting Nasdaq's own methodology changed in 2026 to a rank-based quarterly review that can
    now swap constituents every quarter, not just at the prior annual reconstitution.
  - **Weights** (share counts / market-cap drift) change far more often — cap-weighted index
    weights shift with ordinary daily price movement, and share-count updates themselves may have
    their own update cadence separate from the quarterly membership review (worth confirming
    against S&P DJI's current methodology, since Nasdaq recently folded several previously-separate
    intra-quarter share-count update paths into its scheduled quarterly events).
  - Needs its own ingest/refresh mechanism for composition + weights — this is real, recurring
    scope, not a static reference table populated once.

## Open questions

Everything below is unresolved — flagged for the follow-up conversation this task is meant to
continue in (planned to sit alongside/within `quant-reconcile`'s manual-reconciliation-improvements
context):

- **Where does index composition/weight data come from?** No source identified yet — needs an
  actual data provider/feed for constituent lists and weights, refreshed on the two different
  cadences above.
- **Tolerance for the composite-vs-actual comparison**: an ETF has real, if small, tracking error
  and premium/discount to NAV relative to its theoretical composite value — a comparison here needs
  its own tolerance, not an exact-match expectation, and not necessarily the same
  tolerance/statistics machinery `quant-reconcile` uses for cross-provider comparison.
  - Design decisions here should be **derived from the actual data captured**, this task is created
   to have an dedicated space to reason about that decision.
- **Volume anomaly signal shape**: still not designed, but no longer starting from nothing — see
  the empirical grounding above. Real open sub-questions it raises: is the QQQ/DIA-vs-SH/PSQ/DOG
  gap a per-ticker constant (something a per-ticker tolerance could absorb) or itself something
  that needs explaining before any threshold is trustworthy? Is the long/inverse pattern
  (consistent across all three pairs) reproducible outside a 10-minute regular-hours sample,
  or specific to that window? Needs a larger, less artificially-truncated sample before designing
  a real threshold — the 2026-08-03 sample was deliberately small and had its own edge artifacts
  (see `tasks/quant-reconcile.md`'s test results for the boundary-truncation caveat).
- **Relationship to `quant-reconcile`'s deferred outlier-analyzer tool**: this may end up as a
  component of that tool rather than a fully separate one — not decided, worth revisiting once
  `quant-reconcile` itself is further along.
- **Which output does this actually produce?** A flag/report for manual review (consistent with
  `quant-reconcile`'s "staging is the analysis window, no separate dry-run tooling" philosophy), a
  new reconciliation `resolution_path`/tier, or something else entirely — not decided.

## Implementation plan

<!-- TBD -- blocked on the open questions above converging first. -->

## Test results

<!-- TBD -->
