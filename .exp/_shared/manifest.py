"""Shared results/ibkr_massive_mad/manifest.json read/update -- run params, source table, method
pin, row counts, git sha (tasks/ibkr_massive_mad_calibration.md's Layout section). One file across
every experiment; each experiment script updates only its own `experiments.<name>` entry, never
overwriting another experiment's, so E0-E8 can run independently and in any order.
"""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

MANIFEST_PATH = Path("results/ibkr_massive_mad/manifest.json")

SOURCE_TABLE = "staging_market_data_1min"
SOURCE_JUSTIFICATION = (
    "Task's own preference: staging is what should promote, so reading promoted rows (fact_market_data_1min) "
    "risks circularity. fact_market_data_1min confirmed empty for SPY before 2026-08-03 (quant-reconcile never "
    "reached this range) -- staging is intact and safe to read directly."
)


def current_git_sha() -> str:
    result = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True)
    return result.stdout.strip()


def load_manifest() -> dict:
    if not MANIFEST_PATH.exists():
        return {
            "source_table": SOURCE_TABLE,
            "source_justification": SOURCE_JUSTIFICATION,
            "experiments": {},
        }
    with MANIFEST_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_manifest(manifest: dict, experiment_name: str, entry: dict) -> None:
    manifest["source_table"] = SOURCE_TABLE
    manifest["source_justification"] = SOURCE_JUSTIFICATION
    manifest["git_sha"] = current_git_sha()
    manifest["updated_at_utc"] = datetime.now(timezone.utc).isoformat()
    manifest.setdefault("experiments", {})[experiment_name] = entry

    MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    with MANIFEST_PATH.open("w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, default=str)
        f.write("\n")
