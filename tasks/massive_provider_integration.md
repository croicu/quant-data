# Massive Provider Integration

## Status: Brainstorm — largely converged, blocked on one newly-found code bug

## Problem statement

`fact_market_data_1min` has only ever had one real `candidate`-role provider (`ibkr`) reconciled
against the `yfinance` whistleblower — `provider_pair_disagreement`/reconciliation's
`BarConflict.candidates` list-shaped design anticipated multiple candidates from the start, but
nothing has ever actually exercised that path with real data.

[GitHub issue #44](https://github.com/croicu/quant-data/issues/44) proposes adding Massive
(formerly Polygon.io — `polygon.io` now 301-redirects to `massive.com`, confirmed genuine rebrand)
as a second `candidate`. Already validated as an extended-hours intraday source in
[croicu/quant-scratch#23](https://github.com/croicu/quant-scratch/issues/23) /
[PR #24](https://github.com/croicu/quant-scratch/pull/24) (bounded IBKR-comparison, not yet
production): free Basic tier genuinely covers 1-minute bars across the full 4:00-20:00 ET
extended-hours session, no premium gate. A 5-trading-day SPY comparison there found:

- Bar-count differences are pure representation, not disagreement — IBKR pads every minute with a
  zero-volume bar when nothing traded, Massive omits those minutes entirely; every "missing"
  Massive timestamp matched an IBKR zero-volume bar with no exceptions.
- Close prices agree closely: 18/4,277 shared bars differ by more than $0.01.
- Volume disagrees systematically and grows with session thinness (pre-market 1.08x, regular
  1.24x, after-market 2.46x, Massive consistently higher) — but volume isn't independently
  reconciled (`tasks/volume_reconciliation.md`'s "rides along with whichever provider wins ohlc"),
  so this is a dashboard/trust-signal concern, not a reconciliation-correctness one.
- The 16:00 ET regular-close/after-market-open boundary bar stands out on its own: IBKR reports
  2.6x-16x *more* volume than Massive there, consistently across all 5 days — likely a
  closing-auction-print attribution difference. Same boundary that's already shown up in this
  repo's own reconciliation history (rejected-whistleblower and pending-resolution disputed-bar
  work both clustered there too, per issue #32's closed work).

Known, accepted consequence: once integrated, `fact_market_data_1min` becomes a genuine mixture of
`ibkr`- and `massive`-sourced bars per minute, not a clean single-source series — expected, not a
defect, per the issue.

## Design decisions

### Provider mechanics (settled)

- **Provider naming: `massive`, not `polygon`.** Read the actual merged prototype
  (`croicu/quant-scratch#24`, `src/shared/providers/massive.py`) rather than just the issue text —
  it already committed to `PROVIDER_NAME = "massive"` and class name `MassiveIntraDay`. Avoids
  repeating issue #14's `yf`->`yfinance` mid-project rename.
- **No SDK dependency — plain `requests` against Massive's REST API.** The prototype doesn't use
  `polygon-api-client` or any Massive-specific package: a single `requests.get` against
  `{BASE_URL}/v2/aggs/ticker/{ticker}/range/1/minute/{date}/{date}` (`BASE_URL =
  "https://api.massive.com"`, same `/v2/aggs/...` path shape the old `polygon.io` API used before
  the rebrand), params `adjusted=true, sort=asc, limit=50000, apiKey=<key>`. Response shape:
  `payload["results"]` is a list of `{"t": epoch_ms, "o", "h", "l", "c", "v"}` — `t` needs
  `datetime.fromtimestamp(t / 1000, tz=timezone.utc)`, `v` comes back as a float in practice (the
  prototype casts `int(v)`, noting observed fractional/odd-lot values). **`requests` needs adding
  as an explicit direct dependency in `pyproject.toml`** — quant-data doesn't declare it today
  (only `psycopg[binary]`, `yfinance`, `sshtunnel`, `ib_async`); it may already be present
  transitively via `yfinance`, but importing it directly in `providers/massive.py` means it should
  be declared directly too, not relied on as someone else's transitive pin.
- **Credentials — mirrors the prototype exactly.** New `MassiveSettings(api_key: str)` dataclass
  (no default — "no usable local default", same reasoning the prototype's own comment gives for
  `DatabentoSettings.api_key`), parsed from a `settings.massive.apiKey` block (`TaskError` if the
  key is present as an object but missing `apiKey`), following `IbkrSettings`'s existing
  dataclass-plus-parsing pattern in `settings.py`. Lives in `settings.local.json` (gitignored),
  never committed. `docs/DATABASE.md`/`docs/SETUP.md` need a new credential-setup section.
- **Rate limit — pre-emptive primary, retry-on-429 fallback.** The prototype's own comment
  confirms Massive's free Basic tier still documents 5 calls/minute post-rebrand, but also found
  that limit "isn't strictly enforced in practice" and handles it reactively (retry on HTTP 429, 3
  attempts, 15s apart — an admitted guess: "60s/5 calls = 12s minimum, padded slightly"). Adopt
  quant-data's existing pre-emptive `RateLimitSettings` pattern with a `_default_massive_rate_limit`
  of 5/60s, always-applied like IBKR's, *and* keep the prototype's retry-on-429 as a fallback for
  the cases the documented limit doesn't strictly hold. The 15s spacing is a guess and belongs
  behind a configurable limiter in settings, not hardcoded in the provider.
- **No cap on `dim_provider` rows with `role = 'candidate'`** — confirmed in
  `migrations/003_add_dim_provider_and_staging.sql`: no unique constraint or CHECK restricting
  candidate count. Adding `massive` is a plain seed insert, same shape as `ibkr`/`yfinance`'s
  original seeding.

### Reconciliation adjudication when candidates disagree (settled design, NOT yet implemented)

Candidates are always `data_quality = ACCEPTED` in this codebase (`ibkr.py`: "no synthetic/NaN
placeholder rows... a zero-volume bar is a real fact"; `massive`'s prototype behaves the same way)
— only the whistleblower can be `INCOMPLETE` (never reported, or the confirmed-absent synthetic
placeholder from issue #31) or `REJECTED` (`_run_outlier_detection_pass` runs *exclusively* over
whistleblower rows, per `fetch_whistleblower_accepted_staging_rows`). With exactly one candidate
(today's reality), Tier 1 (`_resolve_completeness`) always catches an invalid whistleblower first —
`len(valid) == 1` (just the one candidate) — before Tiers 2/3 ever run. **Two gaps are dormant
today for exactly that reason, and would first activate the moment a second real candidate
exists:**

1. **Coverage regression**: confirmed-absent whistleblower + two agreeing `ACCEPTED` candidates →
   Tier 1 sees `len(valid) == 2`, doesn't fire; Tier 2 compares each candidate against the
   synthetic all-zero placeholder and "fails closed" (correctly, but only because the diff blows
   every tolerance by accident, not by design); Tier 3 correctly no-ops (no window for a synthetic
   bar). Net effect: even if `ibkr` and `massive` fully agree, the bar lands in
   `fact_pending_manual_resolution` — worse coverage than today's single-candidate behavior.
2. **Correctness bug**: whistleblower present but outlier-`REJECTED` + two `ACCEPTED` candidates →
   same Tier-1-doesn't-fire situation, but Tier 2 does *not* fail closed here: `_find_whistleblower`
   returns the `REJECTED` row regardless of `data_quality` (role match only), and
   `_agrees_within_tolerance` has no data-quality check — it compares real candidate values against
   the whistleblower's real, already-known-bad outlier values. A candidate can win or lose Tier 2
   by coincidental proximity to a value issue #32's outlier detection already rejected.

**Fix — whistleblower validity gate, a single rule that generalizes today's behavior rather than
adding a new policy:**

> A non-`ACCEPTED` whistleblower row never reaches `_agrees_within_tolerance` or the Tier 3 window.
> When no `ACCEPTED` whistleblower exists for a bar/field group, the bar resolves to
> `settings.reconcile.preferred_provider` with a distinct resolution reason.

Checked against single-candidate reality: whistleblower `INCOMPLETE`/`REJECTED` today already
means `len(valid) == 1` → the sole candidate promotes — that already *is* "take the preferred
provider when no adjudicator exists," just degenerate with one candidate. Extending it to N
candidates preserves the semantics exactly and closes both gaps above at once. Implementation
shape: filter to `data_quality == ACCEPTED` inside `_find_whistleblower` (or immediately
downstream) so both `_resolve_agreement` and `_resolve_boundary_fix` inherit the guarantee, rather
than patching each tier separately — `_resolve_completeness`'s `len(valid) == 1` check stops being
the thing that accidentally protects Tiers 2/3.

**Rejected alternative: recognizing "N candidates agree with each other" as its own resolvable
case.** This would smuggle in candidate-vs-candidate comparison, which is absent from this design
*by design* (see below). Two candidates agreeing is not evidence of correctness — the
three-cornered-hat problem doesn't dissolve because both sides of a two-way comparison happen to be
candidates. Falling through to `preferred_provider` keeps that a stated assumption rather than
laundering mutual agreement into a derived accuracy claim.

**No candidate-vs-candidate direct comparison exists anywhere in the algorithm, and this
integration doesn't add one** — confirmed by reading `src/reconcile/algorithm.py`:
`_resolve_agreement` loops over every `_candidates(bars)` independently against the whistleblower;
`_pick_preferred` breaks ties among multiple whistleblower-agreeing candidates via
`settings.reconcile.preferred_provider`, falling back to `agreeing[0]` (arbitrary DB row order) if
that name matches no one in the agreeing list. Two remaining consequences of this, unchanged by the
fix above:
- **Silent tiebreak** when both candidates agree with the whistleblower but disagree with each
  other: resolved purely by `preferred_provider`, with no signal about which was actually closer.
- **When both candidates disagree with the whistleblower outside tolerance**: yfinance is the only
  adjudicator that exists. Tier 2 fails for both, Tier 3 (windowed 3-bar average, same shape) is
  tried next, and if that also fails the bar goes to Tier 4/pending, where `--finalize` outright
  trusts `preferred_provider`'s raw value with no tolerance check — never an actual "which one is
  right" adjudication.

**Audit trail must distinguish adjudicated from unadjudicated resolutions.**
`fact_reconciliation` needs a reason code separating "won Tier 2 within tolerance" from "took
`preferred_provider`, no valid whistleblower existed." In `fact_reconciliation_participant`, the
non-winning candidate in the unadjudicated case did not *lose* — it was never compared. Reusing the
Tier 2 `won = false` shape here would corrupt the reputation trail with losses that carry no
information.

**Unadjudicated resolutions must not feed the Welford variance.** Confirmed in `cli.py`: today,
`provider_pair_disagreement`'s running stats are only updated `if resolution.resolution_path ==
RESOLUTION_AGREEMENT` — i.e. only genuine Tier 2 in-band agreements already feed the variance
estimate. A bar resolved via the new unadjudicated fallback is not an agreement and must stay
excluded from that update on the same existing basis, not a new carve-out.

### ⚠ Blocking bug found verifying the design against actual code — graduation is per-ticker, not per-candidate

The design above assumed `massive` accumulates its own disagreement history and "graduates" into
Tier 2/3 competition once it clears the matched-bar threshold, the same way `ibkr` did originally.
**That assumption doesn't hold in the current code.** From `reconcile/cli.py:467-488`:

```python
graduated_ticker_ids: set[int] = set()
for stats_provider_id, stats_ticker_id, stats_field_id in stats_by_key:
    graduated_ticker_ids.add(stats_ticker_id)
...
for ticker_id, ticker_bar_keys in bar_keys_by_ticker.items():
    if ticker_id in graduated_ticker_ids:
        continue
    # ... the entire graduation-batch calibration block (which computes and saves
    # provider_pair_disagreement rows) lives inside this loop
```

`graduated_ticker_ids` is built from *any* existing `provider_pair_disagreement` row for that
ticker, discarding which candidate it belongs to. `SPY` already has `ibkr` stats, so `SPY`'s
ticker_id is already in that set — the graduation-batch block **never runs for `SPY` again, for
any candidate, ever.** Downstream, `tolerances.get(massive_provider_id)` stays `None` forever
(nothing populates it outside that skipped block or the Tier-2 incremental per-winner update, which
itself requires `massive` to already have tolerances in order to win Tier 2 in the first place —
circular). Net effect: **`massive` would be structurally locked out of ever competing in Tier 2/3
on `SPY`** (or any already-graduated ticker) — not "after a graduation window," but never, until
this is fixed. It could still occasionally win via Tier 1 completeness if `ibkr` is invalid for a
given bar, but that's incidental, not the intended path.

**This blocks the rollout plan below as written** — `SPY` is exactly the ticker the rollout plan
picks first, and it's exactly the ticker this bug disables `massive` on. Needs its own fix
(graduation tracked per `(candidate_provider_id, ticker_id)` rather than per ticker alone, or the
batch-calibration block re-triggered for any candidate still missing stats even on an
already-graduated ticker) as in-scope, required work for this integration — not a follow-up.

### Rollout and backfill (design intent settled; sizing depends on the graduation-gate fix above)

- **`reconcile.preferred_provider` stays `ibkr`.** The close-price agreement data showing both
  candidates reliable is an argument *against* changing it, not for — there's no evidence favoring
  either, and changing on no evidence would be the same derived-accuracy-claim error the
  whistleblower-validity-gate fix deliberately avoids. Under that fix, this setting now also
  governs the no-adjudicator fallback case — it carries more load than before, which is a reason to
  leave it stable, not to revisit it.
- **Rollout — `SPY` first.** Pre-graduation `massive` ingestion is harmless to reconciliation
  correctness (it only fills staging), so the argument for staging the rollout isn't safety but
  bounded follow-through: `provider_pair_disagreement` is per-ticker, so onboarding the full
  watchlist at once starts every ticker's graduation clock simultaneously and produces one large
  simultaneous revision backlog when they clear (see "Graduation is an operational event" below).
  Scope initial rollout to `SPY`, mirroring quant-scratch#23's own scope — **but this is blocked on
  the graduation-gate bug above, since `SPY` is already graduated for `ibkr`.**
- **Backfill — sized to graduation, corrected.** The earlier pass through this used
  `GRADUATION_THRESHOLD_MATCHED_BARS = 4,000`; the actual constant in `src/reconcile/algorithm.py`
  is **1,400**. At ~960 extended-hours minutes/trading day, that's roughly **1.5-2 trading days**
  to clear the gate (not 4-5) — take margin for bars that fail to match. Throughput is a separate,
  looser constraint: at 5 calls/minute with one call per ticker-day, even a 500-day backfill is
  ~100 minutes for a single ticker, tolerable for `SPY` alone. Note the asymmetry a deep backfill
  creates regardless of window size: backfilled history graduates the pair immediately but lands
  its entire span in the revision backlog at once, whereas forward-only accumulation graduates
  slower with a proportionally smaller backlog.
- **Graduation is an operational event, not just a threshold crossing.** When `massive` graduates
  on a ticker, two things change at once: `massive` starts competing on new bars, and the
  pre-graduation staging accumulation becomes evidence about bars already promoted to
  `fact_market_data_1min` under single-candidate adjudication. Handling that second part is
  deferred to a follow-up task (not yet created — working name `tasks/retroactive_revision.md`) —
  it isn't Massive-specific, since any future candidate graduating (or retuning per-ticker outlier
  thresholds) triggers the same question. What this task owns is only the consequence: **the size
  of that revision backlog is the pre-graduation window times the number of tickers**, which is
  the primary constraint on rollout shape above.

### Still open

- **16:00 ET boundary bar.** The closing-auction attribution difference (IBKR 2.6x-16x more volume
  than Massive) lands on the same boundary already implicated in the rejected-whistleblower and
  disputed-bar work from issue #32. Volume isn't independently reconciled, so this doesn't affect
  reconciliation correctness today — but it's a third independent observation of that boundary,
  worth carrying into whatever eventually addresses it rather than noting separately a third time.
  Not blocking.

### Regression tests required before go-live

1. Two `ACCEPTED` candidates + `REJECTED` whistleblower → must not compare against the rejected
   values; resolves to `preferred_provider` with the unadjudicated reason code. This is the case
   that produces a *wrong* answer rather than a missed one.
2. Two `ACCEPTED` candidates + confirmed-absent synthetic whistleblower → resolves automatically,
   does not land in `fact_pending_manual_resolution`.
3. Both above → asserts no `provider_pair_disagreement` update occurs.
4. Single candidate + invalid whistleblower → behavior identical to pre-change baseline (guards
   the "strict generalization" claim).
5. **New**: a second candidate added to a ticker that's already graduated for the first candidate
   must still reach its own graduation batch and be able to win Tier 2 — guards against the
   graduation-gate bug found above regressing silently if "fixed" only in the narrow case tested by
   hand.

### Mechanical follow-through, not yet a design question

Once the graduation-gate bug's fix approach is chosen, the shape of the actual change is:
`providers/massive.py` (new `IntraDayProvider` implementation, adapted from the confirmed-working
prototype shape — constructor-injectable `request_fn`/`sleep_fn` for offline testability per rule
7, builds `OHLCV` with `data_quality=DataQuality.ACCEPTED`), `pyproject.toml` (add `requests` as a
direct dependency), `settings.py` (new `MassiveSettings` + parsing, following `IbkrSettings`'s
exact pattern), `ingest/cli.py`'s `_build_provider`/`_default_providers`/rate-limit dispatch (new
`"massive"` branch), a migration seeding `dim_provider (name, role) VALUES ('massive',
'candidate')`, `src/reconcile/algorithm.py`'s whistleblower-validity-gate fix plus a new
resolution-reason code and the graduation-per-candidate fix, and offline unit tests per rule 5
(mock `request_fn`/DB access, no real network/DB in `tests/unit`).

## Implementation plan

<!-- Added when advancing to Implementation. -->

## Test results

<!-- Added when advancing to Testing / Ready to Submit. -->
