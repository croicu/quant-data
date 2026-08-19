# 1-Minute Bar Variable Inventory — IBKR / Massive / yfinance

Purpose: catalog everything each provider's API can return at 1-minute granularity, independent of current ingestion status. This is a possibility inventory, not a "what we pull today" list. Alexandru to annotate each row with actual data access / plan-tier availability.

Importance scale: **Core** (needed for reconciliation as designed), **High** (materially useful, planned/parked feature), **Medium** (situationally useful), **Low** (marginal or redundant given other fields).

---

## 1. IBKR

### 1.1 TRADES (current core feed — `reqHistoricalData`, whatToShow='TRADES') — **access confirmed**

| Field | Description | Importance | Why |
|---|---|---|---|
| open, high, low, close | Trade-price OHLC within bar | Core | The fact table's reason to exist |
| volume | Total traded volume in bar | Core | Feeds completeness/sanity checks, used in outlier detection design |
| WAP | Volume-weighted average trade price | High | Already flagged as worth pulling; cross-check against close, input to microstructure analysis |
| count | Number of trades in bar | High | Already flagged; liquidity proxy, denominator for tick-level audits |

### 1.2 BID / ASK (separate `whatToShow` calls) — **access confirmed**

| Field | Description | Importance | Why |
|---|---|---|---|
| open/high/low/close of BID | OHLC of bid price over bar | Medium | Only useful if paired with ASK; on its own doesn't give spread |
| open/high/low/close of ASK | OHLC of ask price over bar | Medium | Same as above |

### 1.3 BID_ASK (single combined call — preferred over separate BID/ASK) — **access confirmed**

| Field | Description | Importance | Why |
|---|---|---|---|
| time-avg bid, time-avg ask | Averaged bid/ask over bar | High | This is the actual path to true spread; one call instead of two, avoids double pacing cost |

**Empirically confirmed** (`ib_async` 2.1.0, `AAPL`, live Gateway, 2026-08-18 ~17:25 ET, 30-min lookback ending "now"): `BID_ASK`, separate `BID`/`ASK`, and `MIDPOINT` (§1.4) all returned real bars (15–16 bars each) on this account — no subscription gap. `volume`/`average`/`barCount` on these bar types come back as `-1` (not applicable to a quote-type bar), which is expected, not an error.

Note for future live tests against this feed: `reqHistoricalData`'s `endDateTime` must not be in the future (e.g. a fixed after-market-close time like 20:00 ET when it's currently earlier than that) — IBKR returns "HMDS query returned no data" for a window that hasn't happened yet, on *any* `whatToShow`, not just BID_ASK. Use `datetime.now(timezone.utc)` as `endDateTime` for ad hoc checks instead.

### 1.4 MIDPOINT — **access confirmed**

| Field | Description | Importance | Why |
|---|---|---|---|
| OHLC of midpoint | Midpoint of bid/ask, no volume | Medium | Alternative reference price independent of trade prints; useful if trade-price and quote-price ever need to be distinguished analytically |

### 1.5 ADJUSTED_LAST

| Field | Description | Importance | Why |
|---|---|---|---|
| Split/dividend-adjusted close | Adjusted close series | Medium | Only matters if corporate actions occur within the analysis window; irrelevant for short lookback, relevant for long history / backtests |

### 1.6 Tick-level (`reqHistoricalTicks` / `reqTickByTickData` — separate API family, not a bar call)

| Field | Description | Importance | Why |
|---|---|---|---|
| price, size, timestamp | Individual trade prints | Low (as bulk feed) / High (as ad hoc tool) | Not a warehouse feed — scoped as an ad hoc analyst zoom-in tool, not part of `fact_market_data_1min` pipeline |
| exchange | Venue of individual trade | Low | Same — only relevant in tick-audit context |
| trade condition flags | e.g. odd lot, unreportable, past limit, RTH indicator | Medium (in tick-audit context) | Explains why a tick was or wasn't eligible to move the bar; useful for microstructure investigation, not for bar-level reconciliation |

### 1.7 Reference/contract data (not bar-level, but tied to the feed)

| Field | Description | Importance | Why |
|---|---|---|---|
| conId | IBKR's internal contract identifier | Medium | Useful for `dim_ticker` disambiguation (symbol changes, multiple listings) but not a per-bar field |

**Note:** `conId` is dimensional/reference data, not a bar-level variable — it identifies *which instrument*, not a per-bar observation. Kept in this doc (rather than split into a separate reference-data inventory) because it's tied to the same feed and there's no dedicated reference-data doc yet; revisit if/when `dim_ticker` disambiguation work is actually scoped.

---

## 2. Massive (Polygon.io) — Aggregates endpoint (stocks, minute granularity) — **access confirmed: Stocks Basic (free) tier**

| Field | Description | Importance | Why |
|---|---|---|---|
| o, h, l, c | OHLC (trade-derived, condition-filtered) | Core | Direct analog to IBKR TRADES; this is what makes Massive usable as a candidate/validator at all |
| v | Volume | Core | Same role as IBKR volume |
| vw | Volume-weighted average price for the bar | High | Direct analog to IBKR WAP |
| n | Number of trades in bar | High | Direct analog to IBKR count |
| t | Bar start timestamp (Unix ms) | Core | Required for bar alignment / boundary-misalignment detection (Tier 3) |
| adjusted (flag/param) | Whether results are split-adjusted | Medium | Same relevance as IBKR ADJUSTED_LAST — matters for long history, not short lookback |

**Confirmed not available from Massive on the free Stocks Basic tier:** bid/ask or NBBO quote-level data. Massive's quotes endpoint (`/v3/quotes`) exists as a separate product but requires "Stocks Advanced" or "Stocks Business" plan at minimum — Stocks Basic has no access to it at all, confirmed against Massive's own docs (`massive.com/docs/rest/stocks/trades-quotes/quotes`). If bid/ask parity with IBKR's BID_ASK feed is ever needed from Massive, that's a paid-tier upgrade decision, not a free-tier gap that closes on its own.

---

## 3. yfinance — `history()` / `download()` at 1m interval

| Field | Description | Importance | Why |
|---|---|---|---|
| Open, High, Low, Close | Trade-price OHLC | Core | Whistleblower's basis for comparison against IBKR |
| Volume | Bar volume | Core | Same role as other providers |
| Dividends, Stock Splits | Corporate action events | Low (at 1-min grain) | **Empirically confirmed** (`yfinance` 0.2.65, `AAPL`, 5-day 1m window, 1,950 bars, 2026-08-18): columns are present but every value is `0.0` across the full window — no NaN/absent behavior, just a constant no-op at 1m grain. Window was too short to contain a real ex-div date, so this confirms "present but inert in the normal case," not "guaranteed zero even across an actual corporate action" |
| Adjusted Close (via `auto_adjust`) | Split/dividend-adjusted close | Low (intraday) | **Empirically confirmed** (same run): `auto_adjust=True` vs `auto_adjust=False` produced identical `Close` values (max abs diff `0.0`); `auto_adjust=False` additionally exposes a separate `Adj Close` column. No divergence observed in a window with no corporate action — consistent with the doc's original caution, not proof of behavior when an actual split/dividend falls inside the window |

**Confirmed not available from yfinance:** WAP/VWAP (not a native field — would have to be independently computed and wouldn't be a real observation from Yahoo), trade count, bid/ask, tick-level data. yfinance's 1m interval is also constrained to a 7-day trailing retrieval window regardless of field selection — an operational constraint on the whistleblower feed's backfill depth, not a field-level issue.

---

## 4. Fields with no clean multi-provider equivalent (flagging, not recommending)

| Concept | IBKR | Massive | yfinance |
|---|---|---|---|
| Trade-weighted avg price | WAP (TRADES) | vw (aggregates) | Not available |
| Trade count | count (TRADES) | n (aggregates) | Not available |
| Bid/ask spread | BID_ASK (separate call) | Not confirmed on current tier | Not available |
| Adjusted close | ADJUSTED_LAST (separate call) | adjusted param on aggregates | auto_adjust (daily-series behavior, intraday support unverified) |
| Tick-level trades | reqHistoricalTicks / reqTickByTickData | Presumably has an equivalent trades endpoint (not reviewed here) | Not available |

This asymmetry is itself useful: WAP/count/vw/n are the strongest three-way-comparable fields beyond raw OHLCV since IBKR and Massive both expose them natively — yfinance is the outlier, consistent with its "whistleblower, not equal source" role already established in the architecture.

---

## Open items — resolved 2026-08-18

1. **Data access / plan tier.** IBKR TRADES, BID_ASK, BID, ASK, MIDPOINT: all confirmed live against the real Gateway (§1.1–1.4). Massive: confirmed on Stocks Basic (free) tier.
2. **Massive bid/ask/NBBO.** Confirmed unavailable on Stocks Basic — requires upgrading to Stocks Advanced or Stocks Business at minimum (see §2 note, verified against Massive's own docs).
3. **yfinance Dividends/Splits/adjusted-close at 1m.** Empirically confirmed present-but-inert in a normal window (no corporate action) — see §3 notes. Not yet tested against a window containing a real ex-div/split date.
4. **conId scope.** Kept in this doc (§1.7) with an explicit note that it's dimensional/reference data, not a per-bar variable — no separate reference-data doc exists yet to move it to.
