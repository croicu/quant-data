# Inverse-Pair Cross-Check

## Status: Brainstorm, postponed — deprioritized, not actively worked. Revisit once inverse ETFs
(`DOG`/`SH`/`PSQ`) actually matter for trading, not just signal research (see Problem statement).

## Problem statement

`tasks/per_ticker_disagreement.md` fixes the false-positive problem (a ticker's genuinely-normal
noise wrongly flagged as disagreement) by giving each ticker its own measured tolerance. It does
nothing for the opposite risk: training a ticker's tolerance on its own history means that
tolerance converges to *whatever that ticker's actual behavior is* — including any genuine
data-quality problem already baked into that history. `DOG` specifically is the clearest case: it's
the ticker most in need of scrutiny (25.9% stuck rate, unexplained by volume or price level — see
`tasks/per_ticker_disagreement.md`), and per-ticker training would make its own checker the
loosest one in the system, precisely backwards from what's needed.

A genuinely independent validation signal exists for `DOG`/`SH`/`PSQ` specifically, and it's simpler
than `tasks/index_composite_check.md`'s full weighted-constituent-basket approach: `DOG`/`SH`/`PSQ`
are daily-rebalanced inverse ETFs (short Dow30/S&P 500/Nasdaq-100 respectively) that track
intraday very tightly to -1× their long counterpart (`DIA`/`SPY`/`QQQ`), and those long
counterparts already agree between `ibkr`/`yfinance` essentially perfectly (0-3 stuck bars each in
the full-dataset run, vs. `DOG`'s 499). That makes each long ticker a trusted third reference for
its inverse pair, without needing any constituent composition/weight data at all.

## Design decisions (sketch only — not converged, deliberately not filled in further while
postponed)

- **This is an anomaly flag on `ibkr` specifically, not a tiebreaker between providers.** Earlier
  framing of this idea (checking "which provider matches `-1 × DIA` better, and promoting that
  one") was wrong — corrected during the ad-hoc curiosity check below. `ibkr` is the broker this
  project actually trades through; its quote is the real executable price regardless of how it
  compares to a theoretical inverse-tracking model, and `yfinance` "matching the model better" just
  means its data is smoother/staler, not more real for trading purposes. This is the same
  candidate/whistleblower principle the rest of `quant-reconcile` already follows (`yfinance` never
  replaces `ibkr`'s value, it only ever checks it) — this mechanism should follow it too, not quietly
  reintroduce a "whichever provider looks more correct wins" rule through the back door.
- Concretely: for a disagreeing `DOG` (or `SH`/`PSQ`) minute, compare `ibkr`'s own value against
  what `-1 × DIA`'s (or `SPY`'s/`QQQ`'s) already-agreed move would predict. A large deviation is a
  flag for manual review — evidence something may be wrong with `ibkr`'s own print that minute —
  not a vote to promote `yfinance`'s value instead. `ibkr` still wins by default per
  `preferredProvider`; this only changes whether a specific bar gets surfaced for a human to look
  at before that happens.
- (Curiosity-check finding, 2026-08-03, not build-relevant beyond motivating the above correction:
  on `DOG`'s 499 currently-stuck bars, `yfinance`'s within-bar return was closer to `-1 × DIA`'s
  than `ibkr`'s was, roughly 2:1 among bars where `DIA` moved meaningfully. Real, but doesn't mean
  `yfinance` was "right" — see the framing correction above for why that's the wrong conclusion to
  draw from it.)
- Relationship to `tasks/index_composite_check.md`: same underlying gap (a third independent
  reference beyond the two providers being reconciled), simpler mechanism (one paired ticker's
  price series vs. a full weighted constituent basket) — worth deciding whether this becomes a
  component of that tool, a precursor to it, or stays fully separate once actually picked up.

## Why postponed

Explicit call from the person actually using this data: for signal-research purposes (not yet
live trading), the inverse ETFs matter less than their long counterparts — a signal found in `QQQ`
exists equally in `PSQ`, just sign-flipped, so `PSQ`/`SH`/`DOG`'s own data quality isn't currently
load-bearing. The outlier-masking risk this file describes is a known, accepted tradeoff for now,
not a solved problem — `tasks/per_ticker_disagreement.md` proceeds without this as a prerequisite.
Revisit once inverse-ETF data quality actually matters (e.g. before trading them directly).

## Open questions

Everything is open — this file exists to record the idea and the reasoning for postponing it, not
to converge a design yet.

## Implementation plan

<!-- Not started. -->

## Test results

<!-- Not started. -->
