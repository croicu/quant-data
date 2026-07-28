# Ingest Error Classification (expected vs. unexpected)

## Status: Brainstorm (postponed)

## Problem statement

`quant-ingest` currently treats every per-(ticker, date) failure identically for exit-code
purposes: whether the cause is a weekend/market holiday with no data, a genuinely mistyped
ticker, or a Postgres write failure, it's logged as a `warning` and flips the run's exit code to
`1`. That's a problem for the intended use case: hooking `quant-ingest --catch-up`'s nightly run
up to some kind of alerting, where the exit code is what decides whether anyone gets paged. Today
the exit code can't distinguish "nothing to see here, it was a holiday" from "a real problem
needs attention," so it's useless as an alerting signal without a human reading the log every
night regardless.

Concretely surfaced by `--catch-up` crossing a weekend during a real run on CroicuWS1 — every
watchlist ticker logged a failure for both weekend days, all indistinguishable in the log/exit
code from an actual bad ticker or fetch problem.

## Design decisions (converged 2026-07-27)

- **Target end-state rule**: only genuine, actionable errors should produce a non-zero exit code.
  Warnings (including expected misses like weekends/holidays) get logged but must not affect the
  overall success/exit code.
- **Interim step already shipped**: Yahoo-Finance-sourced fetch failures are now tagged with a
  dedicated `yf` log category (`quant_data._internal.shared.providers.yf.CATEGORY_YF`), separate
  from Postgres write failures (still `ingest`) — see `ingest/cli.py`'s `_ingest_one`. This makes
  the two failure sources at least filterable/attributable by source today via
  `settings.logCategories`/`excludedCategories`, without yet solving the actual
  expected-vs-unexpected classification.
- **Exit code intentionally left unchanged for now**: still `1` if any (ticker, date) pair failed
  for any reason, `0` otherwise. Not touched until this design converges further — changing it
  prematurely risks either silently swallowing a real bad-ticker mistake (if "no data available"
  is blanket-treated as benign) or not actually fixing the alerting-noise problem it's meant to
  solve.

## Open questions

- **Where does "this date is expected to have no data" come from?** Weekends are cheaply
  computable with no dependency. Market holidays are not — quant-data's schema deliberately has
  no session/trading-calendar concept today (see issue #9's investigation), and adding one is a
  real design decision: a hardcoded holiday list (US-equities-specific, doesn't generalize),
  a calendar library dependency (e.g. `pandas_market_calendars`), or something else.
- **Relative/self-consistent heuristic, no calendar needed**: if most of `settings.tickers`
  succeeded for a given date and one didn't, that lone failure is probably a real per-ticker
  problem, not a market-wide holiday — worth considering as a cheaper alternative/complement to a
  real calendar.
- **Does classification differ between `--catch-up` and an explicit `--start-date`/`--end-date`
  range?** Leaning toward "no, should be uniform" but not settled.
- **What does "hook up to alerts" concretely require beyond the exit code?** Whether cron's own
  failure-mail-on-nonzero-exit is sufficient, or whether a structured summary (counts by category)
  needs to be emitted/persisted somewhere for a real monitoring check to consume.

## Implementation plan

<!-- Added when advancing to Implementation. -->

## Test results

<!-- Added when advancing to Testing / Ready to Submit. -->
