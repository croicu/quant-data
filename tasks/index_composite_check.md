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
- **Volume anomaly signal shape**: not designed at all yet — what "coarse, day-level divergence"
  concretely means, what threshold, what it would actually be used for once flagged.
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
