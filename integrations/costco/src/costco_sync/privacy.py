"""Keep Costco secrets off world-readable files."""

from __future__ import annotations

import os
from pathlib import Path


def tighten_tree(root: Path) -> None:
    if not root.exists():
        return
    for path in [root, *root.rglob("*")]:
        if path.is_symlink():
            continue
        path.chmod(0o700 if path.is_dir() else 0o600)


def tighten_private_files() -> None:
    home = Path.home()
    tighten_tree(home / ".costco-mcp")
    tighten_tree(home / ".costco-sync")


def private_umask() -> None:
    os.umask(0o077)
