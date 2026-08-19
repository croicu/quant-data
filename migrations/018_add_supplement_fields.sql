-- 018_add_supplement_fields.sql
--
-- Adds 8 nullable "supplement" columns to staging_market_data_1min and fact_market_data_1min,
-- split into two independently-promoted groups -- see croicu/quant-data#61 for the full design.
--
-- Trade group (wap, trade_count): computed from the same trade prints as OHLC/volume, so it rides
-- along with whichever provider wins that bar's OHLC reconciliation vote, same precedent already
-- set for volume (tasks/volume_reconciliation.md) -- quant-reconcile copies these two columns from
-- the OHLC winner's own staging row, leaving them null if that provider didn't report them ("no
-- data over bad data", an explicit, accepted tradeoff). Populated from Massive's `vw`/`n` today;
-- IBKR's `average`/`barCount` would be a second source later (out of scope for #61) -- the day
-- that's fetched, this pair needs to graduate from "gate by OHLC winner" to a real Tier 1-4
-- comparison of its own.
--
-- Quote group (avg_bid, avg_ask, midpoint_open/high/low/close): a different feed (the NBBO quote
-- book, not the trade tape) with no shared failure mode with the trade group, so it is NOT gated
-- on the OHLC winner -- quant-reconcile copies it from whichever candidate's staging row has it
-- present. Populated from IBKR's BID_ASK/MIDPOINT methods; no second quote source exists today, so
-- there's nothing to arbitrate yet (an honest "wins by default, uncontested" placeholder, not a
-- validated comparison) -- same graduation story as the trade group whenever one appears.
--
-- Both groups are written exclusively by quant-reconcile, preserving the standing
-- single-writer-to-fact invariant -- `stage` only ever writes staging_market_data_1min, never
-- fact_market_data_1min, even for the ungated quote group.
--
-- IBKR's BID_ASK archive also carries `high`/`low` (unconfirmed semantics -- widest quotes seen in
-- the bar) -- deliberately left archived-but-unconsumed here, same treatment as IBKR's deferred
-- `average`/`barCount`.
--
-- Apply with: psql -h <host> -U <user> -d quant_data -f migrations/018_add_supplement_fields.sql

BEGIN;

ALTER TABLE staging_market_data_1min
    ADD COLUMN wap NUMERIC,
    ADD COLUMN trade_count INT CHECK (trade_count IS NULL OR trade_count >= 0),
    ADD COLUMN avg_bid NUMERIC,
    ADD COLUMN avg_ask NUMERIC,
    ADD COLUMN midpoint_open NUMERIC,
    ADD COLUMN midpoint_high NUMERIC,
    ADD COLUMN midpoint_low NUMERIC,
    ADD COLUMN midpoint_close NUMERIC;

ALTER TABLE fact_market_data_1min
    ADD COLUMN wap NUMERIC,
    ADD COLUMN trade_count INT CHECK (trade_count IS NULL OR trade_count >= 0),
    ADD COLUMN avg_bid NUMERIC,
    ADD COLUMN avg_ask NUMERIC,
    ADD COLUMN midpoint_open NUMERIC,
    ADD COLUMN midpoint_high NUMERIC,
    ADD COLUMN midpoint_low NUMERIC,
    ADD COLUMN midpoint_close NUMERIC;

COMMENT ON COLUMN fact_market_data_1min.wap IS 'Volume-weighted average price for the bar -- trade group, winner-gated (copied from whichever provider won this bar''s OHLC vote). NULL if the winner didn''t report it. See croicu/quant-data#61.';
COMMENT ON COLUMN fact_market_data_1min.trade_count IS 'Number of trades in the bar -- trade group, winner-gated. See croicu/quant-data#61.';
COMMENT ON COLUMN fact_market_data_1min.avg_bid IS 'Time-averaged bid price for the bar (IBKR BID_ASK) -- quote group, not winner-gated: copied from whichever provider reported it, independent of the OHLC vote. See croicu/quant-data#61.';
COMMENT ON COLUMN fact_market_data_1min.avg_ask IS 'Time-averaged ask price for the bar (IBKR BID_ASK) -- quote group, not winner-gated. See croicu/quant-data#61.';
COMMENT ON COLUMN fact_market_data_1min.midpoint_open IS 'Open of the bid/ask midpoint price series for the bar (IBKR MIDPOINT) -- quote group, not winner-gated. See croicu/quant-data#61.';

INSERT INTO schema_migrations (version) VALUES ('018_add_supplement_fields');

COMMIT;
