-- 014_seed_materiality_floor_defaults.sql
--
-- Seeds materiality_floor (migration 013) with volume-informed default floor values, instead of
-- shipping empty and waiting on pure manual trial-and-error (data_quality_thresholds's original
-- precedent). Derived from a real finding, not a gut prior: per-bar ibkr volume correlates with
-- ibkr/yfinance disagreement (log-log regression over all 215 bars in
-- fact_pending_manual_resolution as of 2026-08-15: slope -0.3844, intercept 4.3248, R^2 = 0.32
-- against ln(diff_bps) ~ ln(volume)). R^2 = 0.32 means volume explains roughly a third of
-- per-bar variance -- a real relationship, not the whole story -- so these are informed starting
-- points, deliberately overridable per (provider, ticker, field) as more data accumulates, not
-- final calibrated values. See CLAUDE.md's Pending Tasks entry on the volume/noise correlation
-- for the full finding and its open tension with an earlier, conflicting investigation.
--
-- floor_type = 'bps_of_reference' throughout (scales with each bar's own price level, per
-- tasks/materiality_floor_tolerance.md's open question -- appropriate for these price fields).
-- Same floor_value applied uniformly across open/high/low/close per ticker: the regression was
-- fit against each bar's worst-of-4-fields diff, so this is deliberately the conservative
-- (loosest defensible) choice, not a per-field-tuned one.
--
-- Scoped to today's 6 actively-ingested tickers (settings.json's watchlist) -- IWM/RWM/AAPL exist
-- in dim_ticker from earlier experimentation but aren't actively ingested, so seeding floors for
-- them would be inert.
--
-- Apply with: psql -h <host> -U <user> -d quant_data -f migrations/014_seed_materiality_floor_defaults.sql

BEGIN;

INSERT INTO materiality_floor (provider_id, ticker_id, field_id, floor_value, floor_type)
SELECT p.provider_id, t.ticker_id, f.field_id, v.floor_bps, 'bps_of_reference'
FROM (VALUES
    ('SPY', 0.533),
    ('SH',  0.976),
    ('QQQ', 0.747),
    ('PSQ', 1.299),
    ('DIA', 1.187),
    ('DOG', 2.193)
) AS v(ticker, floor_bps)
JOIN dim_ticker t ON t.ticker = v.ticker
JOIN dim_provider p ON p.name = 'ibkr'
CROSS JOIN dim_field f;

INSERT INTO schema_migrations (version) VALUES ('014_seed_materiality_floor_defaults');

COMMIT;
