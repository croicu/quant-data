-- 015_relax_materiality_floor_psq_dog.sql
--
-- Manual override for PSQ/DOG's materiality_floor rows: the population-level volume regression
-- from 014 systematically under-predicted both (real-world validation 2026-08-15: PSQ and DOG
-- showed zero backlog reduction at their regression-derived floor, since both sit well above the
-- fitted line -- DOG's own observed average diff was 5.017 bps against a seeded floor of only
-- 2.193 bps; PSQ's was 3.952 bps against 1.299 bps seeded).
--
-- Replaced with each ticker's own P90 (90th percentile) of its currently-pending backlog's
-- per-bar diff distribution -- clears the bulk of typical noise while still leaving genuinely
-- extreme outliers flagged for manual review, rather than relying on a cross-ticker model that
-- doesn't fit them. Deliberate tradeoff, not a precision default: PSQ/DOG matter more for trading
-- execution than for research-grade reconciliation precision, so a looser floor trading some
-- accuracy for a smaller manual queue is the right call for these two specifically.
--
-- IWM deliberately left unchanged (floor_value = 0.0, no floor) -- only 12 pending bars to derive
-- a P90 from (vs. PSQ/DOG's 31/37), too thin a sample to calibrate responsibly right now.
-- Revisit once more data accumulates.
--
-- Apply with: psql -h <host> -U <user> -d quant_data -f migrations/015_relax_materiality_floor_psq_dog.sql

BEGIN;

INSERT INTO materiality_floor (provider_id, ticker_id, field_id, floor_value, floor_type)
SELECT p.provider_id, t.ticker_id, f.field_id, v.floor_bps, 'bps_of_reference'
FROM (VALUES
    ('PSQ', 3.871),
    ('DOG', 4.753)
) AS v(ticker, floor_bps)
JOIN dim_ticker t ON t.ticker = v.ticker
JOIN dim_provider p ON p.name = 'ibkr'
CROSS JOIN dim_field f
ON CONFLICT (provider_id, ticker_id, field_id) DO UPDATE SET
    floor_value = EXCLUDED.floor_value,
    floor_type = EXCLUDED.floor_type,
    updated_at = CURRENT_TIMESTAMP;

INSERT INTO schema_migrations (version) VALUES ('015_relax_materiality_floor_psq_dog');

COMMIT;
