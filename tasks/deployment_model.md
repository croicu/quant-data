# Deployment Model: On-Prem / Intranet / Cloud

**Status:** Decided — informational reference, not an active task
**Applies to:** `quant-data` (warehouse + storage), consumed by `quant-scratch`, `quant-viz`, and any future tooling

## Problem Statement

`quant-data` needs a clear, written rationale for where the Postgres warehouse and associated
storage live — on-prem, intranet, or cloud — so that decisions aren't re-litigated per-tool and
so the connection-string abstraction (`settings.json`) is understood as a *design property*, not
a placeholder for a migration that's already been decided.

## Design Decisions

### Current state: on-prem, intranet-accessible

- PostgreSQL warehouse runs locally on an Ubuntu machine.
- Accessed over intranet; machine-level SSH key auth from `~/.ssh`.
- A dedicated ingest process is the sole writer; all CLI/viz tools are read-only consumers.
- This remains the default deployment target. Nothing below changes it today.

### Cloud migration is a connection-string change, not an architecture change

Any client (`quant-scratch` CLIs, `quant-viz` backend, future tools) reads its DB target from
`settings.json` and never hardcodes host/credentials elsewhere. Swapping local Postgres for AWS
RDS, Azure Database for PostgreSQL, or Google Cloud SQL requires changing that one value — no
schema changes, no query changes, no client-code changes. This property is deliberate and should
be preserved by any future contributor: **no client should ever branch its behavior based on
whether the target is local or cloud.**

### Two independent triggers for reconsidering deployment — not one

It's tempting to treat "when do we move to cloud" as a single question. It isn't — there are two
separate, unrelated pressures, and they don't need to resolve together:

**1. Storage volume**
Motivation: local disk becoming insufficient for the amount of market data being stored.

Current assessment: **not a live concern.** At ~50–100 bytes/row for 1-minute OHLCV bars under
the star schema, even years of extended-hours history across a few hundred tickers lands in the
tens of GB, not terabytes. Current available storage (10TB) is not a near-term constraint. This
trigger should be re-evaluated only against *measured* DB size growth once real ingestion volume
exists — not designed around speculatively.

If/when this does become real, the fix is **tiering, not full migration**: hot/recent data stays
in local Postgres; cold/historical data offloads to object storage (Cloudflare R2 or S3-compatible),
queryable via foreign data wrapper or re-ingested on demand for a specific backtest. This is a
partial-data decision, not a "move the whole warehouse to the cloud" decision.

**2. Always-on availability**
Motivation: not wanting the local machine running continuously (e.g., overnight) just to keep the
warehouse reachable.

This is the more plausible near-term trigger, and it's cheap to solve independently of storage
volume: a small always-on Postgres instance (a low-cost VM — Lightsail, DigitalOcean droplet, or
similar — rather than a managed DB service) solves "machine can sleep" without implying anything
about data volume or requiring RDS/Cloud SQL's managed-service overhead. A fuller managed cloud
DB is a separate upgrade (managed backups, scaling) on top of solving availability, not a
prerequisite for it.

### Decision matrix

| Trigger | Current status | If it fires, the fix is... | Implies full cloud migration? |
|---|---|---|---|
| Storage volume | Not a concern (10TB available, actual usage orders of magnitude smaller) | Hot/cold tiering to object storage (R2/S3) | No — partial, data-level only |
| Always-on availability | Not urgent, but the more likely near-term trigger | Small always-on Postgres VM | No — infra-level, schema/queries unchanged |
| Both simultaneously | N/A | Managed cloud DB (RDS/Cloud SQL) | Yes — but only once both actually apply |

## Open Questions

- None currently blocking. Revisit storage-volume assessment once ingestion has run long enough
  to produce real growth-rate data (GB/month), rather than the rough estimate above.

## Next Steps

- No action required. This document exists so `quant-viz` and future tools inherit the same
  assumptions: read `settings.json`, never hardcode a deployment target, and don't assume cloud
  migration implies anything about client code.
- Optional future task: instrument and log Postgres storage size over time (e.g., a periodic
  `pg_database_size` check) to give the storage-volume trigger real data instead of estimates.
