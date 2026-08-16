# Massive Provider Integration

## Status: Brainstorm

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

- **The Tier 2/3 agreement loop itself needs no structural change for a second candidate, but
  Tier 1's interaction with an invalid whistleblower does — see "Two-candidate adjudication gaps"
  below, this is not just a design preference, it's a real bug/regression.** Confirmed by reading
  `src/reconcile/algorithm.py`: `_resolve_agreement` already loops over *every*
  `_candidates(bars)` independently, checking each against the whistleblower
  (`_agrees_within_tolerance`) rather than assuming exactly one — `_pick_preferred` already
  handles the case where more than one candidate agrees, falling back to
  `settings.reconcile.preferred_provider` as a tiebreak, then `agreeing[0]` (arbitrary DB row
  order) if that name doesn't match anyone in the agreeing list. No candidate-vs-candidate direct
  comparison exists anywhere, only ever candidate-vs-whistleblower — by design, and that part is
  fine. What's *not* fine: `_resolve_completeness`'s `len(valid) == 1` check silently changes
  behavior once a second `ACCEPTED` candidate exists, because it no longer intercepts an invalid
  whistleblower before Tiers 2/3 see it. Full detail below.
- **Two-candidate adjudication gaps — found by tracing exactly how `ibkr` vs. `massive`
  disagreement gets resolved today, not by inspection alone.** Candidates are always
  `data_quality = ACCEPTED` in this codebase (`ibkr.py`: "no synthetic/NaN placeholder rows... a
  zero-volume bar is a real fact"; `massive`'s prototype behaves the same way) — only the
  whistleblower can be `INCOMPLETE` (never reported, or the confirmed-absent synthetic
  placeholder from issue #31) or `REJECTED` (`_run_outlier_detection_pass` runs *exclusively* over
  whistleblower rows, per `fetch_whistleblower_accepted_staging_rows`). With exactly one candidate
  (today's reality), Tier 1 (`_resolve_completeness`) always catches an invalid whistleblower
  first — `len(valid) == 1` (just the one candidate) — before Tier 2/3 ever run. **Both gaps below
  are dormant today for exactly that reason, and would first activate the moment a second real
  candidate exists:**
  1. **Coverage regression (confirmed-absent whistleblower + two agreeing candidates → stuck,
     where one candidate alone would auto-resolve).** With two `ACCEPTED` candidates, Tier 1 sees
     `len(valid) == 2` and doesn't fire. Tier 2 then compares each candidate against the
     synthetic all-zero placeholder (`_synthetic_absent_whistleblower_bar`) — `|candidate − 0|`
     blows every real tolerance, so it "fails closed" as that function's docstring assumes, but
     that docstring's assumption ("Tier 1 catches it first") is exactly what breaks with two
     candidates. Tier 3 correctly no-ops (no window exists for a synthetic bar). Net effect: even
     if `ibkr` and `massive` fully agree with each other, the bar lands in
     `fact_pending_manual_resolution` with zero automatic path — worse coverage than today's
     single-candidate behavior, not just an unhandled edge case.
  2. **Correctness bug (whistleblower present but outlier-`REJECTED` + two `ACCEPTED` candidates →
     Tier 2 judges against a known-bad value).** Same Tier-1-doesn't-fire situation, but here
     Tier 2 does *not* fail closed: `_find_whistleblower` returns the `REJECTED` row regardless of
     its `data_quality` (role match only), and `_agrees_within_tolerance` has no data-quality
     check at all — it compares real candidate O/H/L/C values against the whistleblower's real,
     already-known-outlier values. A candidate can win or lose Tier 2 by coincidental proximity to
     a value the outlier-detection pass (issue #32) already decided was wrong. This is a genuine,
     previously-unreachable correctness bug, not a hypothetical.
  3. **Silent tiebreak when both candidates agree with the whistleblower but disagree with each
     other**: resolved purely by `settings.reconcile.preferred_provider` (Design decision below),
     with no signal about which candidate was actually closer to the whistleblower.
  4. **When both candidates disagree with the whistleblower outside tolerance**: yfinance is the
     only adjudicator that exists — Tier 2 fails for both, Tier 3 (windowed 3-bar average, same
     per-candidate-vs-whistleblower shape) is tried next, and if that also fails for both the bar
     goes to Tier 4/pending, where `--finalize` just outright trusts `preferred_provider`'s raw
     value with no tolerance check — never an actual "which one is right" adjudication between the
     two candidates.

  **This needs a decision before Massive goes live, not just documentation of the gap**: options
  include (a) fix `_resolve_completeness`/Tier 1 to only compare against `ACCEPTED` whistleblower
  rows and explicitly recognize "N candidates agree with each other, whistleblower
  absent/invalid" as its own resolvable case, (b) filter out non-`ACCEPTED` whistleblower rows
  before they ever reach `_resolve_agreement`/`_resolve_boundary_fix`, or (c) accept the
  regression/bug as acceptable given expected frequency and let both cases fall through to the
  pending queue for manual review. Not decided — needs its own pass through
  `tests/unit/test_reconcile_algorithm.py` (or wherever the existing tier tests live) to write a
  regression test for the two-candidate + rejected-whistleblower case specifically, since that's
  the one that produces a wrong answer rather than just a missed one.
- **No cap on `dim_provider` rows with `role = 'candidate'`** — confirmed in
  `migrations/003_add_dim_provider_and_staging.sql`: no unique constraint or CHECK restricting
  candidate count. Adding `massive` is a plain seed insert, same shape as `ibkr`/`yfinance`'s
  original seeding.
- **Provider naming: `massive`, not `polygon`.** Read the actual merged prototype
  (`croicu/quant-scratch#24`, `src/shared/providers/massive.py`) rather than just the issue text —
  it already committed to `PROVIDER_NAME = "massive"` and class name `MassiveIntraDay`. Matches
  this repo's own naming lean from the earlier open-questions pass and avoids repeating issue
  #14's `yf`->`yfinance` mid-project rename. Settled, not still open.
- **No SDK dependency — plain `requests` against Massive's REST API.** The prototype doesn't use
  `polygon-api-client` or any Massive-specific package at all: a single `requests.get` against
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
- **Credentials — settled shape, mirrors the prototype exactly.** New `MassiveSettings(api_key:
  str)` dataclass (no default — "no usable local default", same reasoning the prototype's own
  comment gives for `DatabentoSettings.api_key`), parsed from a `settings.massive.apiKey` block
  (`TaskError` if the key is present as an object but missing `apiKey`), following
  `IbkrSettings`'s existing dataclass-plus-parsing pattern in `settings.py`. Lives in
  `settings.local.json` (gitignored), never committed. `docs/DATABASE.md`/`docs/SETUP.md` need a
  new credential-setup section.
- **Rate limit — real numbers now available, still needs a decision.** The prototype's own
  comment: "Massive's free Basic tier documents 5 API calls/minute" — confirmed still true
  post-rebrand (the earlier open question about whether the limit changed is answered: it hasn't,
  as of the prototype's 2026-08-15 live verification). But the prototype also found the documented
  limit "isn't strictly enforced in practice" (`day-chart`'s soft warning past 5 days instead of a
  hard cap) and handles it with **retry-on-429** (3 attempts, 15s apart — a guess at safe spacing,
  "60s/5 calls = 12s minimum, padded slightly," not a measured value) rather than a pre-emptive
  rate limiter. quant-data's own `RateLimitSettings`/`_default_ibkr_rate_limit` mechanism is
  pre-emptive (caps outgoing calls before hitting the ceiling), a different strategy from the
  prototype's react-and-retry approach — still open: does `providers/massive.py` adopt
  quant-data's existing pre-emptive `RateLimitSettings` pattern (e.g. a
  `_default_massive_rate_limit` of 5/60s, always-applied like IBKR's), reuse the prototype's
  retry-on-429 approach instead, or do both (pre-emptive limiting as the primary defense, retry as
  a fallback for the cases quant-scratch already found the documented limit doesn't strictly hold)?
- **`reconcile.preferred_provider` default**: currently `"ibkr"` (`DEFAULT_PREFERRED_PROVIDER`).
  Per the Design decisions above, no code change is needed for the tiebreak logic itself to work
  with two candidates — but does `ibkr` staying preferred (tiebreak winner when both candidates
  agree with the whistleblower) match intent, or should this be revisited given the close-price
  agreement data above suggests both are reliable? Leaning toward "leave as `ibkr`, no reason
  given yet to change it" but flagging as a decision rather than an oversight.
- **Historical backfill scope**: does Massive ingestion start real-time-only (new bars going
  forward) or does it also backfill historical dates the way `ibkr` did at its own integration —
  and if backfilled, over what date range? Affects how large an initial mixture
  `fact_market_data_1min` sees on day one, and interacts with `materiality_floor`/
  `data_quality_thresholds`/`provider_pair_disagreement`'s "starts at zero, needs real data to
  calibrate" precedent (`tasks/pipeline_accuracy_hardening.md`) — a large sudden backfill could
  produce a temporary spike in `fact_pending_manual_resolution` before those tables accumulate
  enough `massive` history to calibrate tolerances.
- **`settings.providers` default list and rollout**: `DEFAULT_PROVIDERS = ["yfinance"]` in code;
  the real watchlist config (`settings.local.json`) presumably already adds `ibkr`. Does `massive`
  get added to every ticker in the existing watchlist immediately, or rolled out on a subset first
  (e.g. just `SPY`, mirroring how the quant-scratch#23 validation itself was scoped) before trusting
  it across the full watchlist?
- **Mechanical follow-through, not yet a design question** — once the remaining open questions
  above converge, the shape of the actual change is: `providers/massive.py` (new
  `IntraDayProvider` implementation, adapted from the confirmed-working prototype shape —
  constructor-injectable `request_fn`/`sleep_fn` for offline testability per rule 7, builds
  `OHLCV` with `data_quality=DataQuality.ACCEPTED` since Massive — like IBKR — only returns real
  data, no synthetic/NaN placeholder rows to detect), `pyproject.toml` (add `requests` as a direct
  dependency), `settings.py` (new `MassiveSettings` + parsing, following `IbkrSettings`'s exact
  pattern), `ingest/cli.py`'s `_build_provider`/`_default_providers`/rate-limit dispatch (new
  `"massive"` branch alongside the existing `"ibkr"` one), a migration seeding `dim_provider (name,
  role) VALUES ('massive', 'candidate')`, and offline unit tests per rule 5 (mock `request_fn`, no
  real network access in `tests/unit` — matching `tests/unit/test_massive_provider.py`'s existing
  approach on the quant-scratch side).

## Implementation plan

<!-- Added when advancing to Implementation. -->

## Test results

<!-- Added when advancing to Testing / Ready to Submit. -->
