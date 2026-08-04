# --finalize Targeted Promotion

## Status: Brainstorm

## Problem statement

`--finalize` today has exactly one mode: sweep the *entire* `fact_pending_manual_resolution` queue,
force-promoting `settings.reconcile.preferredProvider`'s raw value for every pending (bar, field
group). There's no way to target one specific bar, and no way to choose a winner other than
`preferredProvider` — in particular, no tooled way to let the whistleblower (`yfinance`) win a
specific bar. The only existing path for that is exactly what `tasks/quant_reconcile.md`'s "Manual
correction" section already documents: directly hand-editing `staging_market_data_1min`/
`fact_market_data_1min`, "no dedicated tooling implied."

Concrete motivating case: investigating the 3 pending `SPY` bars (2026-08-03), the person reviewing
them judged `yfinance`'s raw value correct for all three (against `ibkr`'s, which looked
internally-consistent but was actually the outlier — see `tasks/quant_reconcile.md`'s Test Results
for the actual values and the reasoning). Promoting that judgment currently means hand-writing SQL
against three tables, not running a command.

## Design (from initial conversation, not fully converged — see Open questions)

New CLI arguments on `--finalize` for targeted single-bar mode, replacing the bulk sweep for that
invocation: **ticker**, **date/time**, **field group**, and **winner**. When all are supplied,
`--finalize` promotes the specified winner's raw staging value for that one (ticker, date, time,
field_group) instead of sweeping the whole pending queue with `preferredProvider`.

- **`resolution_path = 'manual_override'`, always, regardless of which provider is named as
  winner** — even if the chosen winner happens to be `preferredProvider`, using the targeted flags
  represents a specific, deliberate judgment about *this* bar, not the blanket algorithmic sweep
  `'finalized'` represents. This is what makes the feature actually useful: it's the first *tooled*
  implementation of yfinance's only path to `fact_market_data_1min`, and it correctly feeds
  `fact_reconciliation_participant`'s reputation tracking (which already filters to
  `resolution_path = 'manual_override'` — no change needed there, just calling `record_reconciliation`
  with the right path).
- Reuses the existing promote+lazy-purge machinery once the resolution is recorded — no new
  behavior needed there.

## Open questions

Everything below needs to converge before implementation:

- **Date/time input format.** The session that motivated this feature spent real effort untangling
  that `dim_time.time_of_day` is stored as literal UTC, not ET, which was a genuine source of
  confusion even mid-session. Should the CLI accept ET (human-friendly — "market close" is
  naturally 16:00 ET, not 20:00 UTC — but requires a conversion step that's exactly the kind of
  thing that caused confusion before) or UTC (matches the raw stored value directly, no conversion
  bug surface, but not how a person naturally thinks about "which bar")? Whichever is chosen, the
  CLI's own `--help` text should say explicitly which one, given the history here.
- **Must the targeted bar already be pending?** `--finalize`'s whole scope today is "the pending
  queue" — does targeted mode only operate on bars already in `fact_pending_manual_resolution`
  (consistent scoping, simple mental model), or can it act on any bar regardless of state
  (more flexible, but starts blurring `--finalize`'s role with a general-purpose override tool)?
- **Winner validation**: should the CLI reject a `--winner` that never actually reported a staging
  row for the targeted bar (can't promote a provider's value that doesn't exist), or trust the
  caller? Leaning toward validating and failing clearly, consistent with this repo's general
  preference for explicit errors over silent no-ops.
- **Field group input**: today there's only `'ohlc'`, so this argument does no real work yet, but
  it's still part of `fact_pending_manual_resolution`'s grain — needed for forward-compatibility if
  a second field group is ever added, same reasoning as that table's own design.
- **Exact flag names and whether this is a `--finalize`-mode combination or a distinct
  subcommand/flag entirely** (e.g. `quant-reconcile --finalize --ticker SPY --date ... --group ohlc
  --winner yfinance` vs. something else) — not decided.
- **Dimension lookup**: needs read-only ticker/date/time → `ticker_id`/`date_id`/`time_id`
  resolution (existing dimension-resolution code in `postgres.py` creates rows if missing, which is
  wrong for this — a targeted correction should fail clearly if the bar doesn't exist, not silently
  create dimension rows for it).

## Implementation plan

<!-- Not started -- pending the open questions above. -->

## Test results

<!-- Not started. -->
