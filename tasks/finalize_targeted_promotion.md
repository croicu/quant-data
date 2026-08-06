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

Concrete motivating case: investigating the 3 pending `SPY` bars (2026-08-03/04). **Corrected
finding, superseding an earlier same-session read**: the original pass judged `yfinance`'s raw
value correct for all three, believing `ibkr` was the outlier. Visual inspection of candlestick
charts (`tasks/Conflict - 2026.7.28/29/30.png`) plus the raw `staging_market_data_1min` rows
reversed that — `ibkr`'s OHLC sits smoothly inside the surrounding candle pattern on all three
days, while `yfinance` has exactly one outlier extreme field per bar, and that field isn't fixed
(`L` on 07-28 and 07-30, `H` on 07-29, where `L` actually matches `ibkr` exactly at 725.98). Each
outlier value sits suspiciously close to the *adjacent* day's closing price level — `yfinance`
`H`=740.4873 on 07-29 ≈ 07-28's close (~740.9); `yfinance` `L`=729.2697 on 07-30 ≈ 07-29's close
(~729.5). A further 3-day candlestick comparison (`ibkr` vs. `yfinance` raw staging data,
`tasks/IBKR - 2026.7.28-30.png` / `tasks/Yahoo - 2026.7.28-30.png`) showed this isn't a specific
"adjacent-day" mechanism — `yfinance`'s curve has sporadic unexplained spikes throughout the window
(more than just these 3 bars) with nothing in `ibkr` at the same instant, consistent with ordinary
Yahoo/yfinance feed noise rather than a nameable bug; a few happening to land near an adjacent day's
price is most likely coincidence. See `tasks/yahoo_data_sanitization.md` for the follow-up this
spawned (excluding these bad ticks from reconciliation generally, not just these 3 bars). So the
actual motivating case is the reverse of the original framing: **`ibkr` should win all three**, and
promoting that judgment currently means hand-writing SQL against three tables, not running a
command. (This also means `tasks/quant_reconcile.md`'s Test Results, if it's ever read for these
specific bars, should be cross-checked against this correction rather than trusted at face value for
this case.)

**Independently confirmed against a third-party reference (DataBento, `tasks/*.csv`).** Pulled
paid DataBento 1-minute data for `SPY` over the same 3-day window (raw export has multiple
per-venue records per minute, not one row per bar — combined by hand via min-of-lows/max-of-highs
for a quick check). `yfinance`'s specific outlier value is decisively refuted on all three bars —
DataBento isn't within miles of any of them. `ibkr` matches closely on 07-28 and 07-30; on 07-29,
`ibkr`'s own `L`=725.98 sits ~2.9 below DataBento's combined low of 728.88 — a smaller, unresolved
residual gap, not chased further. **Decision: not adopting DataBento as an ongoing/routine
reference** — it's a paid data source (even though this one pull was inexpensive), and this was a
one-off sanity check for this specific investigation, not a new candidate provider.

## New pending-bar review candidates (2026-08-05)

Reviewing the 127-bar backlog left after croicu/quant-data#28/#29/#31's live verification
(6,939 bars separately unblocked by #31's whistleblower-absence fix are not part of this list —
those resolved automatically, not stuck). Logging candidates for DataBento cross-check here as
they're found, rather than re-investigating from scratch later:

- **`SPY`, 2026-07-27, 09:50/09:51 ET (13:50/13:51 UTC)** — `close` at 09:50 differs `ibkr`
  743.95 vs. `yfinance` 743.919982910156 (~3¢); `open` at 09:51 differs `ibkr` 743.91 vs.
  `yfinance` 743.950012207031 (~4¢). All other fields at both minutes agree to a fraction of a
  cent. Correctly failed Tier 2: SPY's own learned tolerance for `open`/`close` is unusually tight
  (sub-cent, `provider_pair_disagreement` stddev ~3.5e-6/~5.9e-6 relative), so a 3–4¢ gap is a real
  outlier relative to SPY's normal cross-provider precision, not just visually-small noise. Not
  boundary-fix-resolved either, since the two adjacent minutes each have their own (different-field)
  discrepancy, so the 3-bar windowed average doesn't smooth it away. **Ask-DataBento case** — same
  treatment as the original 3-bar `SPY` investigation above, not yet pulled.
- **`SPY`, 2026-07-28, 09:30 ET (13:30 UTC — market open)** — `ibkr` O=739.21 H=739.49 L=738.83
  C=738.83 vs. `yfinance` O=739.190002441406 H=739.47998046875 L=738.830017089844 C=738.830017089844.
  `low`/`close` agree to a fraction of a cent; `high` differs ~1¢ but SPY's `high` tolerance is wide
  (~52¢, stddev 2.35e-4) so it passes comfortably; `open` differs ~2¢ against SPY's tightest field
  tolerance (~0.8¢, stddev 3.5e-6), so `open` alone fails Tier 2. Illustrates the per-field redesign
  doing its job: same absolute cent-gap means very different things depending on which field it
  lands in. **Ask-DataBento case.**

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

## Library API extension (2026-08-04, brainstorm continued)

Originally scoped as a `quant-reconcile --finalize` CLI-only flag (see "Design" above). Once
`MarketData.fetch_pending_resolution_bars` (quant-data#26/#27) shipped, the same capability came up
again from the *consumer* side: should `quant-scratch` be able to trigger a targeted resolution
programmatically, not just have a person run the CLI? Conclusions reached so far, not yet
implemented:

- **Mechanism: a narrow, credentialed public class, not a network service.** The read side
  (`MarketData`) is read-only by DB privilege (`quant_reader`, no write grant at all) — a write
  capability can't just be added to it. The alternative considered was a real network
  service/RPC layer (so `quant_writer` credentials never reach `quant-scratch`'s machine at all),
  but the actual goal here is catching *honest mistakes* (hand-writing SQL against three tables),
  not isolating an untrusted/adversarial caller — there's no multi-tenant or hostile-actor threat
  model to design against. So a new class (working name `MarketDataWriter`) constructed with
  `quant_writer` host/user/password — same shape `create_postgres_provider` already uses for
  `quant_reader` — with a narrow method surface (`resolve_pending_bar(...)`, not raw
  `write_bars`/SQL access) is enough: it guides a caller toward the validated path instead of
  hand-rolled writes, without requiring a whole new hosted-service component this repo doesn't
  have today.
- **`resolve_pending_bar`'s input shape**: identify the target via `ticker`, `timestamp`,
  `field_group`, and a winner provider name — exactly the fields `PendingResolutionBar` already
  exposes (`bar.ticker`, `bar.timestamp`, `field_group`, `provider`), so a client can call
  `fetch_pending_resolution_bars`, pick whichever entry it wants to win (including the
  whistleblower's, which is this whole task's original motivating case), and pass those four
  values straight through. Deliberately **not** passing the candidate's `OHLCV` value itself back
  to the server — `resolve_pending_bar` re-fetches and validates against the *current*
  `staging_market_data_1min` row for that (ticker, timestamp, field_group, provider) rather than
  trusting a client-echoed copy, since it could be stale between the `fetch` and the `resolve`
  call. No new fields needed on `PendingResolutionBar` for this.
- **Naming, deferred**: `MarketData` should become `MarketDataReader` once `MarketDataWriter`
  actually exists, so the two are named by capability rather than one being the unmarked default.
  Not renamed yet — bundle the rename with actually introducing `MarketDataWriter`/
  `resolve_pending_bar` as one coordinated breaking change, rather than renaming today with no
  functional payoff and breaking a second time later.
- **Breaking changes are fine here, deliberately**: the repo owner is comfortable breaking
  `MarketData`'s import name outright when this lands — no deprecated alias needed — since
  `quant-data` and `quant-scratch` are both under their control and can be synchronized directly.
  Explicit preference for breaking *now*, while `quant-scratch` is still the only consumer, over
  accumulating back-compat shims that get harder to unwind once more components depend on
  `quant-data` (see `CLAUDE.md`'s "Future: multiple consumers" section on the registry that doesn't
  exist yet either, for the same reason).
- Still needs to converge with the CLI-flag open questions below — both `quant-reconcile
  --finalize`'s targeted mode and `MarketDataWriter.resolve_pending_bar` should share one
  underlying core implementation, not duplicate the promote+validate logic.
- **Optional narrower DB role, `quant_resolver`**: instead of (or alongside) `MarketDataWriter`
  holding full `quant_writer` credentials, a dedicated role scoped to only what a targeted resolve
  actually needs — least-privilege on top of the "honest mistakes, not hard security" mechanism
  already chosen, still without needing a hosted network-service boundary. Based on what
  `promote_bar_to_fact`/`record_reconciliation`/`clear_pending_manual_resolution`/
  `purge_staging_bar` (`postgres.py`) actually touch, the grant list is bigger than just the two
  fact tables in the queue's own name:
  - `fact_market_data_1min` — INSERT/UPDATE (the promotion itself)
  - `fact_pending_manual_resolution` — DELETE (clearing the resolved key)
  - `staging_market_data_1min` — SELECT at minimum, to validate the winner's current value before
    promoting (the "don't trust the client's echoed `OHLCV`" design above depends on this read);
    DELETE only if `resolve_pending_bar` also purges (see the open fork below)
  - `fact_reconciliation` / `fact_reconciliation_participant` — INSERT, or the reputation-tracking
    benefit this task's own "Design" section calls out silently stops working
  - `dim_ticker`/`dim_date`/`dim_time`/`dim_field_group`/`dim_provider` — SELECT only (not
    `quant_writer`'s upsert-on-missing behavior), so a targeted resolve fails clearly on an unknown
    bar instead of silently creating dimension rows — same reasoning as the existing "Dimension
    lookup" open question below.
  - **Open fork**: should `resolve_pending_bar` also purge `staging_market_data_1min` (matching
    `quant-reconcile`'s existing lazy-purge behavior, needing DELETE there too), or leave purging to
    the next regular `quant-reconcile` run (a resolved bar's now-redundant staging rows are harmless
    until then, and it shrinks `quant_resolver` to read-only on that table)?

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
