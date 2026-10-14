"""
Where things live.

The prototype may run from a git worktree while the ledgers (untracked CSVs)
live in the main checkout next to the running bots. REPO_ROOT resolves to the
main checkout in that case so the corpus and the ledger tools read the real
files. Everything under REPO_ROOT is opened read-only; the prototype's own
writes stay under DATA_DIR.
"""
from __future__ import annotations

import os
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("AI_PLATFORM_DATA_DIR", PACKAGE_DIR / "data"))
REPORTS_DIR = PACKAGE_DIR / "reports"


def _main_checkout(start: Path) -> Path:
    """If `start` is a git worktree, return the main checkout; else `start`."""
    dot_git = start / ".git"
    if dot_git.is_file():
        text = dot_git.read_text().strip()
        if text.startswith("gitdir:"):
            gitdir = Path(text.split(":", 1)[1].strip())
            # <main>/.git/worktrees/<name>  ->  <main>
            if gitdir.parent.name == "worktrees" and gitdir.parent.parent.name == ".git":
                return gitdir.parent.parent.parent
    return start


def repo_root() -> Path:
    override = os.environ.get("AI_PLATFORM_REPO_ROOT")
    if override:
        return Path(override).resolve()
    return _main_checkout(PACKAGE_DIR.parent)


REPO_ROOT = repo_root()
# The checkout this code sits in (docs are read from here so a branch's doc
# edits are what gets indexed; ledgers come from REPO_ROOT).
CODE_ROOT = PACKAGE_DIR.parent

GATEWAY_DB = DATA_DIR / "gateway.sqlite"
PROPOSAL_QUEUE = DATA_DIR / "proposal_queue.jsonl"
INDEX_DIR = DATA_DIR / "index"


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    INDEX_DIR.mkdir(parents=True, exist_ok=True)
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
