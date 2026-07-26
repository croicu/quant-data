from pathlib import Path

from ingest.cli import main

SETTINGS_PATH = Path(__file__).parent.parent / "data" / "settings.json"


def test_main_runs_clean():
    exit_code = main([], settings_path=SETTINGS_PATH)
    assert exit_code == 0
