#!/usr/bin/env python3
"""Sync ./local to the croicuws1-only remote, keeping it out of GitHub history.

Temporarily un-ignores local/, commits it, force-pushes that one commit to
croicuws1's main, then resets local main back to its pre-sync state so
local/ stays untracked/ignored on the branch that goes to origin/GitHub.
"""

import subprocess
import sys
from pathlib import Path

REMOTE = "croicuws1"
IGNORE_COMMENT = "# Artifacts that go to the secondary (non-GitHub) git remote only."
IGNORE_LINE = "/local/"


def run(root, args, check=True, capture=False):
    result = subprocess.run(["git", *args], cwd=root, capture_output=capture, text=True)
    if check and result.returncode != 0:
        sys.exit(f"git {' '.join(args)} failed:\n{result.stderr or result.stdout}")
    return result


def repo_root() -> Path:
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True
    )
    if result.returncode != 0:
        sys.exit("Not inside a git repository.")
    return Path(result.stdout.strip())


def local_has_content(local_dir: Path) -> bool:
    if not local_dir.is_dir():
        return False
    for _ in local_dir.iterdir():
        return True
    return False


def remove_ignore_block(gitignore: Path) -> None:
    lines = gitignore.read_text().splitlines()
    end = lines.index(IGNORE_LINE) + 1
    start = end - 1
    if start > 0 and lines[start - 1] == IGNORE_COMMENT:
        start -= 1
    if start > 0 and lines[start - 1] == "":
        start -= 1
    del lines[start:end]
    gitignore.write_text("\n".join(lines) + "\n")


def undo_local_commit(root: Path) -> None:
    run(root, ["reset", "--mixed", "HEAD~1"])
    run(root, ["checkout", "--", ".gitignore"])


def main() -> None:
    root = repo_root()
    local_dir = root / "local"
    gitignore = root / ".gitignore"

    if not local_has_content(local_dir):
        print("local/ is empty -- nothing to sync.")
        return

    status = run(root, ["status", "--porcelain"], capture=True).stdout
    if status.strip():
        sys.exit("Working tree isn't clean. Commit or stash your changes first.")

    branch = run(root, ["rev-parse", "--abbrev-ref", "HEAD"], capture=True).stdout.strip()
    print(f"Syncing local/ to {REMOTE}/{branch} ...")

    run(root, ["fetch", REMOTE])

    if IGNORE_LINE not in gitignore.read_text().splitlines():
        sys.exit(f"Expected '{IGNORE_LINE}' in .gitignore but didn't find it -- aborting.")

    remove_ignore_block(gitignore)
    run(root, ["add", "local", ".gitignore"])

    if run(root, ["diff", "--cached", "--quiet"], check=False).returncode == 0:
        print("Nothing new to sync -- restoring .gitignore.")
        run(root, ["checkout", "--", ".gitignore"])
        return

    run(root, ["commit", "-m", "Track ./local for croicuws1 sync"])

    push = run(
        root, ["push", "--force-with-lease", REMOTE, f"HEAD:{branch}"], check=False, capture=True
    )
    if push.returncode != 0:
        undo_local_commit(root)
        sys.exit(f"Push to {REMOTE} failed -- rolled back local commit.\n{push.stderr}")

    print(f"Pushed to {REMOTE}/{branch}.")

    undo_local_commit(root)

    status = run(root, ["status", "--porcelain"], capture=True).stdout
    if status.strip():
        print("Warning: working tree isn't clean after reset -- check `git status`.")
    else:
        print("Local main reset back to its pre-sync state; local/ is untracked again.")


if __name__ == "__main__":
    main()
