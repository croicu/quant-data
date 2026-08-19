# Quote-bar enrichment ingest (WAP, trade count, IBKR bid/ask)

## Status: Superseded

**Superseded 2026-08-18 by `tasks/ingestion_layer_spec.md`** — that doc converged on the landing-
zone design (`(provider, method, ticker, period)` grain on `provider_source_archive`) this task's
open questions were asking about. This file's problem statement (below) still stands as the
motivating case/evidence; its three open questions are answered in `ingestion_layer_spec.md`
instead of here. Kept on disk for history rather than deleted, since croicu/quant-data#60 (the
tracking issue) is still open and this file is what it originally pointed at.

## Problem statement

`fact_market_data_1min` (and `staging_market_data_1min`/`provider_source_archive` beneath it)
currently only captures OHLCV. Both `ibkr` and `massive` (formerly Polygon.io) return additional
per-minute fields alongside the OHLCV bars this repo already ingests, currently discarded:

- **IBKR**: `TRADES` bars already returned by the existing ingest call include `WAP` (time-weighted
  average price) and trade-count fields that go uncaptured. A separate `reqHistoricalData` call
  with `whatToShow="BID_ASK"` returns time-averaged bid (`.open`) / time-averaged ask (`.close`)
  per minute — confirmed live, full account/key access, no subscription gap.
- **Massive**: the same aggregates endpoint already used for OHLCV also returns `vw` (WAP-equivalent)
  and `n` (trade count) on every bar, at no extra call cost. Bid/ask/NBBO is confirmed **not
  available at any price point on the free Basic tier** (`/v3/quotes` 403s "not entitled") — a hard
  capability gap, not a config issue.
- Confirmed live: IBKR's `TRADES` and `BID_ASK` calls can return **different bar counts for the same
  window** (16 vs 15 in one test) — any ingest of both needs to tolerate partial per-minute coverage
  rather than assuming a 1:1 match between the two calls.

`quant-scratch` already prototyped this end-to-end (croicu/quant-scratch#26 tracking,
croicu/quant-scratch#27 merged) purely to validate the data is real/accessible before this repo
scopes its own ingest/schema — not meant to dictate quant-data's design. Prototype shape, for
reference: a `QuoteBar` type (`timestamp`, `wap`, `trade_count`, `avg_bid`, `avg_ask`, all
`Optional`) kept separate from the OHLCV bar type; `IBKRIntraDay.fetch_quote_bars` does a second
`TRADES` call plus a `BID_ASK` call left-joined on timestamps; `MassiveIntraDay.fetch_quote_bars`
reads `vw`/`n` out of the *same* HTTP response already used for OHLCV via a small per-call cache
(deliberately no second request, since Massive free tier is hard-limited to 5 calls/minute).

Tracking issue already opened by the repo owner: croicu/quant-data#60 (`status:brainstorm`,
`cross-repo`, cc'd from croicu/quant-scratch#26/#27). Related: croicu/quant-data#44 (Massive as a
second OHLCV candidate — this issue is the follow-on enrichment-field question once that lands, not
a duplicate).

## Design decisions

<!-- Update as the discussion converges. -->

## Open questions

Carried directly from issue #60's Ask — none resolved by the quant-scratch prototype, all left for
this repo to decide:

1. **Where these fields live in the schema** — new nullable columns on `fact_market_data_1min`, or
   a separate supplementary table? Only `ibkr`/`massive` populate anything at all, and only `ibkr`
   populates bid/ask — a sparsity/nullability question as much as a normalization one.
2. **Does `quant-reconcile`'s arbitration logic need to account for these fields**, or are they
   informational/provider-attributed data with no cross-provider conflict concept (unlike OHLCV,
   which is what actually gets reconciled today)?
3. **Ingest/backfill cadence and pacing impact** — IBKR's `BID_ASK` call roughly doubles per-day
   IBKR call volume beyond the existing `TRADES` ingest; Massive's `vw`/`n` capture is free (same
   call), but confirm that holds for whatever ingest shape this repo actually uses (batch backfill
   vs. incremental — relevant to `src/ingest/` post-#56 split).

## Implementation plan

<!-- Added when advancing to Implementation. -->

## Test results

<!-- Added when advancing to Testing / Ready to Submit. -->
