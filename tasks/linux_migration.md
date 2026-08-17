# Linux Migration

## Problem statement

`quant-data` currently runs from a Windows development machine, connecting to
Postgres over an SSH tunnel (PuTTY/plink, port-forwarded). This adds friction
(tunnel setup, `open_quant_data.py` automation) and requires IBKR connectivity
to be brokered across machines. Moving the project to run natively on the
Linux box that hosts Postgres removes the tunnel entirely and allows IB
Gateway to run headless, local to the database.

The Linux box will run headless (no monitor/GUI use). Development will
continue via VS Code Remote-SSH from the Windows machine, with Claude Code
running server-side on the Linux box for the migration itself and going
forward.

## Procedure

Executed by Claude Code from the current Windows project, operating over SSH
against the Linux box.

1. **Clone the repo on Linux**
   - `git clone` `quant-data` fresh onto the Linux box. Single repo — no
     separate `quant-reconcile` project (reconciliation code lives at
     `src/reconcile` inside `quant-data`).

2. **Bootstrap Claude Code project memory path on Linux**
   - Run Claude Code once inside the freshly-cloned Linux repo so the
     project-path-keyed memory directory (`~/.claude/projects/<project>/memory/`)
     is created before anything is copied in. Skipping this means the later
     unzip lands next to an orphaned folder instead of the one Claude Code
     actually reads from.

3. **Transfer Claude Code memory**
   - On Windows: zip the project's memory directory
     (`~/.claude/projects/<project>/memory/` under the Windows user profile).
   - Copy the zip to the Linux box (scp/equivalent).
   - Unzip into the Linux memory path established in step 2.
   - Verify with `/memory` that Claude Code is actually loading the
     transferred content, not starting fresh.

4. **Install and configure IB Gateway headless on Linux**
   - Install IB Gateway (Linux installer) + IBC (IBController) + Xvfb.
   - Configure for the paper trading account. 2FA is not enabled on the
     paper account, so unattended login should not require a manual/TOTP
     step — only the daily forced restart applies (platform-wide, not
     account-tier-specific).
   - Run as a **systemd service**, not a manually-launched process, so it
     survives reboots and doesn't depend on the SSH/VS Code session staying
     open.
   - Confirm the daily restart window doesn't collide with the ingestion
     schedule.

5. **Point Postgres access at localhost**
   - Update whatever connection config currently assumes the SSH-tunneled
     port to connect to localhost instead, now that the app runs on the same
     box as the database.

6. **Supply fresh credentials on Linux**
   - `.env`/credentials (IBKR paper login, DB credentials) do not transfer
     via `git clone` (repo is public, rightly excludes them). These need to
     be supplied fresh on the Linux side.

7. **Decide fate of `open_quant_data.py`**
   - This script assumes the Windows + SSH tunnel setup and is dead weight
     once Postgres access is local. Either retire it, or keep it documented
     as a fallback for working from Windows without the Linux box available.

8. **Connect from VS Code Remote-SSH**
   - Once setup is complete, open a new VS Code instance, connect via
     Remote-SSH to the Linux box, open the cloned repo. Claude Code run from
     this session executes server-side on Linux — no further path/OS
     juggling required.

## Interaction with related work

- Does not touch reconciliation logic, tolerance model, or any pipeline
  design work — purely infrastructure/environment migration.
- Unblocks (but does not require) moving off Excel/Power Query toward
  Grafana or Streamlit, since the tunnel-dependent visualization path is
  going away regardless.

## Open questions

- Retire `open_quant_data.py` now, or keep as a documented Windows fallback?
- Exact IBKR daily restart window on Linux, and whether it needs to be
  changed from default to avoid colliding with ingestion runs.
