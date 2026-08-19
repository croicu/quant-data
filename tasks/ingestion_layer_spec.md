# Ingestion Layer Spec — Multi-Provider 1-Minute Feeds

Status: design converged on grain and structure; variable availability confirmed, the IBKR serialization question resolved, the `provider_source_archive` schema change done as a coalesced migration, and (2026-08-19) `ingest` itself now actually fetches/archives three IBKR methods (`TRADES` + `BID_ASK` + `MIDPOINT`) by default, live-verified end to end — see §2/§3/§4/§6/§7/§8. **[PR #62](https://github.com/croicu/quant-data/pull/62) open** (branch `ibkr-multi-method-archive`) — this is prep work toward croicu/quant-data#60, not a full close of it. This is the ingestion (landing zone) layer only — parsing/extraction into `fact_market_data_1min` and downstream reconciliation are separate, later concerns, now split into their own tracking issue: [croicu/quant-data#61](https://github.com/croicu/quant-data/issues/61). Supersedes `tasks/quote_bar_ingest.md` as the working design doc for croicu/quant-data#60 — that file's problem statement still stands but its open questions are answered here.

---

## 1. Scope and grain

- **Base ingestion grain is 1-minute**, across all three providers (IBKR, Massive, yfinance). This is a firm decision, not provisional.
- **Coarser bars (5-min, 15-min, daily, etc.) are never separately ingested.** They are query-time or materialized-view aggregations computed from `fact_market_data_1min`. No new `dim_time` grain, no parallel fact table, no duplicated reconciliation logic per interval.
- **Finer-than-1-minute (tick-level) is out of scope as a feed.** It is a deliberate, justified exception — an ad hoc analyst tool (see §5), used only when a specific hypothesis can't be tested at 1-min resolution. It is never a default and is not part of the ingestion pipeline.
- Rationale for the asymmetry: going coarser than 1-min is free (aggregation on data already held). Going finer than 1-min carries real cost — pacing limits on IBKR historical ticks, Massive tier constraints, and generally the kind of premium/expense tradeoff that only makes sense if the strategy is genuinely microstructure-dependent (which it currently isn't).

---

## 2. Ingestion table design

### 2.1 Landing zone philosophy

The ingestion table is a **faithful, lossless, replayable image of what each provider's API actually returned** — not a normalized or pre-parsed structure. No schema decisions about which fields matter are made at this layer; that's the job of a later parsing/extraction step. This means:

- Re-parsing later to pull a field not originally extracted should be possible **without re-fetching from the provider**, as long as the stored blob retained it.
- The shape of the blob is provider-specific by design — no forced common envelope across providers.

### 2.2 Grain: `(provider, method, ticker, period)`

**Decision: `method` is a first-class key component of the ingestion table's natural key, not incidental metadata.**

- Natural key: `(provider, method, ticker, period)` — this is `provider_source_archive`'s
  `(ticker, provider, method, trading_date)`, live in `migrations/quant_ingest/001_init_provider_source_archive.sql`
  as of 2026-08-18 (coalesced into the init migration, not a separate ALTER — see §6).
- `blob`: raw payload as returned by the provider for that call (`payload` column)
- `fetched_at`: ingestion timestamp

This resolves a structural asymmetry between providers:

| Provider | Blob shape | `method` value(s) |
|---|---|---|
| **Massive** | Raw JSON, unparsed. Aggregates endpoint response is a fairly fixed shape regardless of parameters. | Single fixed value: `'aggregates'` |
| **yfinance** | Confirmed single-endpoint (`ingestion_variable_inventory.md` §3). | Single fixed value: `'history'` |
| **IBKR** | Serialized Python record from `reqHistoricalData` / `ib_async`. Shape depends entirely on which `whatToShow` was requested (TRADES, BID_ASK, MIDPOINT, ADJUSTED_LAST, etc.) — and later potentially `reqHistoricalTicks`. | **Genuinely varies**: `'TRADES'`, `'BID_ASK'`, `'MIDPOINT'`, ... (IBKR's own `whatToShow` literals) — same broker connection, multiple independent feeds |

Because IBKR's blob is not self-describing (a stored blob is ambiguous on replay without knowing which call produced it), `method` had to be promoted from "a column that happens to matter for IBKR" to "part of what identifies the row."

### 2.3 Why this grain over alternatives

Two alternatives were considered and rejected:

- **Envelope consolidation** (bundle TRADES + BID_ASK + MIDPOINT into one composite blob per provider per minute, to get uniform "one row per provider per minute"): rejected. This would make the ingestion layer perform a transformation (aggregation) before landing, breaking the "dumb, lossless, replay" property. It also introduces partial-failure design questions (what happens if BID_ASK fails but TRADES succeeds? does the whole minute wait?) that the chosen design avoids entirely.
- **`(provider, ticker, period)` with `method` as ordinary metadata**: rejected because it implied a false uniformity — "one record per provider per minute" isn't actually true for IBKR, which has multiple independent feeds per minute. Making `method` part of the key makes the table's grain honest: **one record per provider-method per minute**.

### 2.4 Consequences of this design

- Every row is a literal, untransformed image of exactly one API response. No envelope assembly logic to build or maintain.
- Massive and yfinance rows have a constant/single `method` value; only IBKR varies. Schema stays uniform across providers even though only one provider exercises the variability.
- Adding a new IBKR feed later (e.g. ADJUSTED_LAST, or eventually ticks if that ever became a stored feed rather than ad hoc) is purely additive — new `method` value, same table, no schema migration.
- This anticipates the parked `dim_feed` / feed-coverage generalization (see project-level open items) — `(provider, method)` at ingestion is the raw precursor to what `dim_feed` will formalize at the fact layer. **Note for whoever picks up the `dim_feed` task later: the ingestion layer already encodes this distinction via `method`; the fact-layer task should build on it rather than re-deriving it from scratch.**

---

## 3. Serialization completeness (IBKR-specific risk)

**Resolved 2026-08-18, checked directly against code:** current IBKR ingestion does **not** serialize the full raw `BarData` object. [`ibkr.py:109-118`](../src/quant_data/_internal/shared/providers/ibkr.py#L109-L118) narrows each bar down to `timestamp`/`open`/`high`/`low`/`close`/`volume` before building the archive payload — `ib_async`'s `BarData.average` (WAP) and `.barCount` (trade count) are read off the object but discarded before serialization, never reaching `provider_source_archive`.

So the "reparse without re-fetch" property does **not** currently hold for IBKR:
- Massive: reparsing for a new field later is free — confirmed the archive already stores the full raw JSON response (`PayloadKind.RAW_API_RESPONSE`, [`massive.py:114`](../src/quant_data/_internal/shared/providers/massive.py#L114)).
- IBKR: reparsing WAP/trade-count for any day already archived under the current narrowing would require a genuine re-fetch — pacing-limited, unlike Massive's free reparse. Every historical IBKR archive row to date is affected, not just future ones.

**Action item carried into implementation:** widening `ibkr.py`'s serialization to retain `average`/`barCount` (and whatever else the method-keyed grain in §2 ends up landing) needs to happen before the archive's lossless-replay property is actually true for IBKR — this is now folded into the quote-bar ingest work (croicu/quant-data#60) rather than tracked as a separate fix.

---

## 4. Variable inventory (reference)

Full field-by-field inventory of what each provider's API can return at 1-min grain — organized by call/method for IBKR, by endpoint for Massive/yfinance, with importance ratings — is in the companion document: `ingestion_variable_inventory.md`.

This inventory describes **what's available inside the blob for the extraction layer to later pull out** — it does not itself constrain the ingestion table's shape (per §2.1, ingestion is schema-agnostic).

Open items on that document — **all resolved 2026-08-18** (see `ingestion_variable_inventory.md`'s own "Open items — resolved" section for full detail):

1. **Which fields are actually within current data access / plan tier** — resolved. IBKR `TRADES`/`BID_ASK`/`BID`/`ASK`/`MIDPOINT` all confirmed live against the real Gateway; Massive confirmed on the free Stocks Basic tier.
2. **Whether Massive's tier exposes any bid/ask or NBBO product at all** — resolved: no. Requires upgrading to Stocks Advanced/Business at minimum, confirmed against Massive's own docs.
3. **Whether yfinance intraday bars populate Dividends/Splits/adjusted-close columns in practice, or are always null/no-op at 1m** — resolved: present but inert (constant `0.0`/identical to unadjusted `Close`) in a normal window with no corporate action; not yet tested against a window containing a real ex-div/split date.
4. ~~Whether IBKR `conId` belongs in the variable inventory~~ — resolved: out of scope. `conId` is dimensional (`dim_ticker` concern), not an ingestion/bar variable — kept in the inventory doc for now since no separate reference-data doc exists yet.

---

## 5. Explicitly out of scope for this spec

- **Tick-level data as a stored feed.** Scoped separately as an ad hoc, on-demand analyst tool (`reqHistoricalTicks` / `reqTickByTickData`), triggered manually when investigating a specific hypothesis (e.g. suspected time-of-day correlation between two intervals). Not wired to reconciliation staging, not a scheduled pull, no permanent fact table implied. If it ever needs one, that's a distinct future task.
- **Parsing/extraction logic** that reads landed blobs and populates `fact_market_data_1min` (or a future BID_ASK / tick fact table). This spec covers only the landing zone.
- **Reconciliation logic** (Tiers 1–4, graduation gate, variance/tolerance) — unaffected by anything in this document; ingestion grain and blob format are upstream of and independent from reconciliation design.
- **`dim_feed` formalization** — remains a deferred future task; this spec only notes that ingestion's `method` key anticipates it.

---

## 6. Summary of firm decisions vs. open items

**Decided:**
- 1-minute is the ingestion grain; coarser bars are always derived, never separately ingested.
- Tick data is an ad hoc tool, not a feed.
- Ingestion table natural key is `(provider, method, ticker, period)`; `method` is a first-class key component.
- No envelope consolidation across IBKR call types — one row per actual API response.
- `conId` and similar reference/dimensional data are out of scope for this spec.

**Resolved 2026-08-18:**
- Data access / plan tier per provider — confirmed via `ingestion_variable_inventory.md`'s live checks (IBKR TRADES/BID_ASK/BID/ASK/MIDPOINT all accessible; Massive on free Stocks Basic tier).
- Massive exposes no bid/ask/NBBO on Stocks Basic — confirmed against Massive's own docs; a paid-tier upgrade decision if ever needed, not a gap that closes on its own.
- Current IBKR ingestion serializes a pre-narrowed subset, not full `BarData` objects — confirmed directly in code ([ibkr.py:109-118](../src/quant_data/_internal/shared/providers/ibkr.py#L109-L118)); WAP/trade-count are read off the object and discarded before archiving. Fix folded into croicu/quant-data#60's implementation.
- yfinance blob shape — confirmed single-endpoint/single-`method` like Massive; `Dividends`/`Stock Splits`/adjusted-close columns present but inert (`0.0`/no-op) at 1m in a window with no corporate action.

**Also resolved 2026-08-18:**
- **`provider_source_archive` schema change** — done. `method TEXT NOT NULL` added to both `provider_source_archive` and `archive_coverage`'s natural keys directly in `migrations/quant_ingest/001_init_provider_source_archive.sql` (coalesced in place, not a separate `002_add_method...` ALTER script), per explicit repo-owner direction: no production archive data exists yet worth an incremental migration path, so quant_ingest gets reset clean rather than migrated in place. Preserving old rows through a future *data* migration (not schema) is deliberately left open as a someday-maybe, not designed now. Single-valued providers get literal `method` values (`'aggregates'` Massive, `'history'` yfinance); IBKR uses its own `whatToShow` literals (`'TRADES'`, `'BID_ASK'`, ...). Not yet applied to any real database — still needs a `DROP DATABASE quant_ingest` + recreate + re-apply, which per this file's migration-confirmation rule needs the repo owner's explicit go-ahead before running.

**Also resolved 2026-08-19 — Python side now threads `method` through end to end:**
- New `quant_data._internal.contracts.PRIMARY_METHOD_BY_PROVIDER` (`'yfinance': 'history'`,
  `'ibkr': 'TRADES'`, `'massive': 'aggregates'`) — single source of truth, since `method` is
  needed on both the write side (each `IntraDayProvider`) and the read side (`stage`, which has
  no provider objects, only provider name strings from `settings.providers`).
- Each provider class gained a `METHOD` class attribute set from that constant, mirroring
  `FETCH_VERSION`'s existing precedent; `ibkr.py`'s `whatToShow="TRADES"` literal now reads
  `whatToShow=self.METHOD` instead, removing the duplicate literal.
- `ProviderSourceArchiveWriter.record_fetch`/`_record_coverage` and
  `ProviderSourceArchiveReader.fetch_latest_bars` all gained a required `method` parameter;
  `ingest/cli.py` passes `provider.METHOD`, `stage/cli.py` looks up
  `PRIMARY_METHOD_BY_PROVIDER[provider_name]` (skipping with a warning if a provider has no known
  method, same per-provider fault tolerance as the rest of `_stage_one`).
- All affected mocks/tests updated (`tests/mocks/provider_source_archive.py`,
  `tests/mocks/yfinance.py`, `test_provider_source_archive.py`, `test_ingest_cli.py`,
  `test_stage_cli.py`) plus `docs/ARCHITECTURE.md`/`docs/SCHEMA.md`. `ruff`/`pytest` both green
  (286 passed; the one failure is `test_ibkr.py`'s live-Gateway integration test, unrelated —
  Gateway wasn't running).
- Deliberately **not** done in this pass: the actual WAP/trade-count/bid-ask capture itself (IBKR's
  `average`/`barCount` widening from §3, or any `BID_ASK`/`MIDPOINT` fetch call) — this pass only
  made the schema/plumbing honest for a single method per provider; teaching providers to fetch
  additional methods is separate, larger work still ahead of #60.

**Resolved 2026-08-19 — `ingest` now fetches multiple IBKR methods for real, live-verified:**
- `IntraDayProvider.fetch_bars(ticker, target_date, method: str | None = None)` — `method=None`
  means the provider's own primary method (`DEFAULT_METHODS[0]`). Deliberately **one call = one
  method**, not a single call returning several results: `ingest/cli.py`'s `_ingest_one` loops
  over a provider's methods itself and calls `fetch_bars` once per method, so rate limiting (which
  sits between the orchestration loop and the provider, never inside one) is acquired once per
  real API request rather than once per logical fetch.
- `PRIMARY_METHOD_BY_PROVIDER` replaced by two constants in `contracts.py`:
  `DEFAULT_METHODS_BY_PROVIDER` (`{"yfinance": ["history"], "ibkr": ["TRADES", "BID_ASK"],
  "massive": ["aggregates"]}`, the single source of truth) and `PRIMARY_METHOD_BY_PROVIDER`
  (derived from it, first entry per provider — still what `stage` alone consumes for OHLCV
  staging; unaffected by `ingest` archiving additional methods, which just sit in
  `provider_source_archive` unconsumed until a method-aware parser exists).
- `ProviderFetchResult` gained a `method: str` field (self-describing return value). Each provider
  class gained `DEFAULT_METHODS: list[str]` (replacing the old scalar `METHOD`).
- `IBKRIntraDay.DEFAULT_METHODS = ["TRADES", "BID_ASK"]` — the two methods actually confirmed live
  this session; `MIDPOINT`/`ADJUSTED_LAST` deliberately excluded from the default set (real,
  accessible feeds per the variable inventory, but no concrete reason yet to ingest them). Two
  serializers now dispatch by method (`_SERIALIZERS` dict): `_serialize_trades_bar` (unchanged
  OHLCV fields) and `_serialize_bid_ask_bar` (`avg_bid`/`avg_ask`/`high`/`low` — honest field
  names, since a `BID_ASK` bar's `.open`/`.close` are time-averaged bid/ask, not a real trade
  open/close; `.volume`/`.average`/`.barCount` come back `-1` and are omitted rather than
  archived as meaningless placeholders). An unrecognized method raises `AppError`.
- New `settings.ibkr.methods: list[str] | None` (`IbkrSettings`) — only IBKR carries this, since
  it's the only provider genuinely multi-valued; `None` (unset, the default) means "ingest all of
  `DEFAULT_METHODS`," confirmed as the actual behavior, not just the documented intent.
- **Live-verified against CroicuWS2's real IB Gateway, both code paths**: a plain `quant-ingest`
  run with no `settings.ibkr.methods` override archived both `TRADES` (960 bars, real OHLCV) and
  `BID_ASK` (960 bars, real `avg_bid`/`avg_ask` — e.g. `avg_bid=777.68`/`avg_ask=777.73`) for SPY
  2026-08-17 in one invocation; a second run with `settings.ibkr.methods: ["TRADES"]` against SPY
  2026-08-18 archived only `TRADES`, confirming the override actually restricts fetching rather
  than just filtering after the fact. `ruff`/`pytest` both green (303 passed, including 8 new
  tests for the multi-method loop, the per-method serializers, and `settings.ibkr.methods`
  parsing/validation).
- `docs/ARCHITECTURE.md`/`docs/PROTOCOL.md` updated for the new `fetch_bars` signature,
  `DEFAULT_METHODS_BY_PROVIDER`, IBKR's per-method serializers, and `settings.ibkr.methods`.

**Resolved 2026-08-19 — `MIDPOINT` added to the default set:**
- `IBKRIntraDay.DEFAULT_METHODS` is now `["TRADES", "BID_ASK", "MIDPOINT"]` — repo owner's
  explicit call, on a collect-now-decide-later basis (data is cheap to drop later via
  `provider_source_archive`'s existing `DELETE` grant; not having it collected forecloses any
  analysis entirely). Unlike `BID_ASK`, `MIDPOINT` is a genuine OHLC series of the bid/ask
  midpoint price (real `.open`/`.high`/`.low`/`.close`, not a flat per-bar average), so it's
  **not** reconstructable from `BID_ASK` alone — the concrete use case flagged: comparing
  `MIDPOINT`'s OHLC against `TRADES`' own OHLC can distinguish a trade-print-driven price move
  from a quote-repricing move with no trade behind it. New `_serialize_midpoint_bar` (real OHLC
  field names, `.volume`/`.average`/`.barCount` omitted same as `BID_ASK`) registered in
  `_SERIALIZERS`. `ADJUSTED_LAST` remains deliberately excluded — no concrete reason raised for it.
  Live-verified against CroicuWS2's real Gateway: SPY 2026-08-13 archived all three methods in one
  `quant-ingest` invocation, `MIDPOINT`'s payload confirmed real OHLC shape (`open=773.39`,
  `high=773.39`, `low=773.27`, `close=773.35`, no `volume`/`avg_bid`/`avg_ask` keys).
  `ruff`/`pytest` green (304 passed). Docs updated for the three-method default (`docs/
  ARCHITECTURE.md`/`docs/PROTOCOL.md`) — note `ingest`'s default IBKR call volume is now roughly
  **triple** pre-#60, not double, within `settings.ibkr.rateLimit`'s existing ceiling.

**Open:**
- Whether `dim_feed` formalization (parked, §2.4) should be scoped alongside this migration or stay deferred.
- yfinance's untested case: a window that actually contains a real ex-div/split date, to confirm Dividends/Splits/adjusted-close behavior beyond "inert in the normal case."
- **`stage`/`fact_market_data_1min` still don't consume `BID_ASK`/`MIDPOINT` at all** — those
  archived rows sit in `provider_source_archive` unconsumed. Split out into its own tracking issue,
  [croicu/quant-data#61](https://github.com/croicu/quant-data/issues/61) (`status:brainstorm`,
  2026-08-19) — schema placement (new columns on `fact_market_data_1min` vs. a separate
  supplementary table), whether `quant-reconcile` needs to arbitrate these fields at all, how
  `stage`'s parser dispatch generalizes to a second method per provider, and Massive's `vw`/`n`
  (a much smaller lift, no new archive rows needed) are all open there.
- `ADJUSTED_LAST` support (confirmed live-accessible, not in `DEFAULT_METHODS` or `_SERIALIZERS`
  yet) — add when there's a concrete reason, not speculatively.
