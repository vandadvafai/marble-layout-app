"""One consistent project data directory for uploads, processed
files and exports.

Portability contract
--------------------
On a fresh clone of the repo, nothing under this directory exists.
It is created lazily the first time an upload lands, so a new
laptop can:

    git clone <repo>
    pip install -r requirements.txt
    npm install && npm run dev            # frontend
    python scripts/run_api_server.py      # backend

and start uploading real projects without any manual bootstrap.

The root is configurable via ``AVANDAD_DATA_DIR`` — set that when
running under a container / CI to keep artefacts outside the
checkout. When unset it defaults to ``<project_root>/data/`` (kept
out of git via ``.gitignore``).

Layout:

    <APP_DATA_DIR>/
        uploads/       raw Excel + slab photos as the user sent them
        processed/     ``clean_slabs.json`` + processed slab images
        exports/       generated PNG / DXF / ZIP downloads

Directories are created on demand via ``ensure_dirs()`` — callers
don't need to check first. Every path returned is absolute.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


ENV_VAR_NAME = "AVANDAD_DATA_DIR"
DEFAULT_SUBDIR = "data"


@dataclass(frozen=True)
class AppPaths:
    """Snapshot of the configured directories.

    All four fields are absolute ``Path`` objects. ``root`` is the
    directory whose children are ``uploads/``, ``processed/`` and
    ``exports/``. Frozen so callers can pass this around freely.
    """

    root: Path
    uploads: Path
    processed: Path
    exports: Path


def _project_root() -> Path:
    """Repo root — three levels above ``app_paths.py``."""
    return Path(__file__).resolve().parent.parent.parent


def resolve_app_paths(project_root: Path | None = None) -> AppPaths:
    """Return the configured ``AppPaths`` for the running process.

    ``project_root`` is only used to anchor the default when
    ``AVANDAD_DATA_DIR`` is unset; callers in production should let
    the resolver figure it out. Tests can pass an isolated
    ``project_root`` (or set the env var to a temp dir) to prove
    the app works on a machine that has never seen the repo before.
    """
    root_env = os.environ.get(ENV_VAR_NAME)
    if root_env:
        root = Path(root_env).expanduser()
        if not root.is_absolute():
            root = (Path.cwd() / root).resolve()
    else:
        base = project_root or _project_root()
        root = (base / DEFAULT_SUBDIR).resolve()
    return AppPaths(
        root=root,
        uploads=root / "uploads",
        processed=root / "processed",
        exports=root / "exports",
    )


def ensure_dirs(paths: AppPaths) -> None:
    """Create every configured directory if it isn't already there.

    Idempotent. Safe to call on every request — ``mkdir(parents=True,
    exist_ok=True)`` is cheap enough.
    """
    for p in (paths.root, paths.uploads, paths.processed, paths.exports):
        p.mkdir(parents=True, exist_ok=True)
