"""Resolve which slab inventory the API should use.

Portability rewrite
-------------------
The previous version silently fell back to a demo ``clean_slabs.json``
at ``outputs/slab_ingestion_test/`` when the operator hadn't uploaded
anything yet. On a fresh clone that file doesn't exist, so:

    * either the resolver raised ``FileNotFoundError`` and every
      inventory endpoint returned 500, OR
    * the operator's *real* project silently ran against the demo
      fixture — which is the surprise the portability audit
      flagged.

The new resolver never falls back to a filesystem path outside the
operator's explicit choice. Order of preference:

    1. The active uploaded session (Step-3 upload). Highest
       precedence.
    2. ``$AVANDAD_INVENTORY_PATH`` (or the legacy
       ``STONELAYOUT_INVENTORY_PATH``) — an explicit
       operator-configured path. A missing file at this location
       raises, because "override to a nonexistent file" is a
       configuration error we must not swallow.
    3. Empty. The resolver returns a ``ResolvedInventorySource``
       with ``path=None`` and ``source_label="empty"``. Callers
       render an empty inventory (no slabs, no crash) so the
       designer can proceed straight to Step 3 and upload one.

Sample-plan endpoints ship with their own bundled inventory fixture
(see ``placement_engine/api/routes.py``) so a fresh clone can still
demonstrate the wizard without any upload. That fixture is NEVER
used for real-project matching or export.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


# Source labels — string literals so they round-trip cleanly through
# JSON to the frontend. Keep this list aligned with the
# ``InventorySource`` discriminated union on the TypeScript side.
SOURCE_UPLOADED = "uploaded"
SOURCE_ENV = "env_override"
SOURCE_EMPTY = "empty"

# Legacy env var name kept as an alias so operators upgrading from
# V1.0.0 don't get bitten by a rename. The Avandad name wins if both
# are set.
ENV_VAR_NAMES: tuple[str, ...] = (
    "AVANDAD_INVENTORY_PATH",
    "STONELAYOUT_INVENTORY_PATH",
)


@dataclass(frozen=True)
class ResolvedInventorySource:
    """The picked slab inventory + the trail of candidates checked.

    ``path`` is ``None`` when the resolver returned an empty result
    (no upload active, no env override). ``candidates_checked``
    lists every path we looked at, in priority order, so the info
    endpoint can surface it to operators.
    """

    path: Path | None
    source_label: str
    candidates_checked: list[Path]

    @property
    def is_empty(self) -> bool:
        """True when there is no inventory to load."""
        return self.path is None


def resolve_inventory_source(project_root: Path) -> ResolvedInventorySource:
    """Pick the inventory file the API should load.

    See the module docstring for the full precedence rules. Never
    raises for a "no inventory yet" state — callers handle
    ``is_empty`` explicitly. Only raises when the operator set an
    env override to a file that doesn't exist (a configuration
    error we must not swallow).
    """
    candidates: list[Path] = []

    # Step 1: uploaded session takes precedence over everything else.
    # Lazy import so the resolver doesn't drag in the upload module's
    # slab-intake dependency chain when nothing has been uploaded yet.
    from placement_engine.api.inventory_upload import get_active_upload
    active = get_active_upload()
    if active is not None and active.clean_slabs_path.exists():
        candidates.append(active.clean_slabs_path)
        return ResolvedInventorySource(
            path=active.clean_slabs_path,
            source_label=SOURCE_UPLOADED,
            candidates_checked=candidates,
        )

    # Step 2: environment override.
    env_raw: str | None = None
    env_var_used: str | None = None
    for name in ENV_VAR_NAMES:
        val = os.environ.get(name)
        if val:
            env_raw = val
            env_var_used = name
            break
    if env_raw:
        env_path = Path(env_raw).expanduser()
        # Relative paths resolve against the project root so a value
        # like ``examples/demo/clean_slabs.json`` works no matter
        # where the server was launched from.
        if not env_path.is_absolute():
            env_path = (project_root / env_path).resolve()
        candidates.append(env_path)
        if not env_path.exists():
            raise FileNotFoundError(
                f"{env_var_used}={env_raw!r} points at {env_path}, "
                f"which does not exist. Fix the path or unset the "
                f"variable to use the upload flow instead.",
            )
        return ResolvedInventorySource(
            path=env_path,
            source_label=SOURCE_ENV,
            candidates_checked=candidates,
        )

    # Step 3: empty state. No upload, no override — the app renders
    # a "no inventory yet" surface and gates Step 4 until the user
    # runs the Step-3 upload flow.
    return ResolvedInventorySource(
        path=None,
        source_label=SOURCE_EMPTY,
        candidates_checked=candidates,
    )


def source_label_description(label: str) -> str:
    """Short human-facing description for ``source_label``. Used as
    the chip tooltip in the inventory-info panel. The frontend has
    its own copy of these strings; both must stay in sync so the UI
    and the API agree on what each label means."""
    return {
        SOURCE_UPLOADED: "uploaded by designer (Step 3)",
        SOURCE_ENV: (
            "resolved from $AVANDAD_INVENTORY_PATH "
            "(or the legacy $STONELAYOUT_INVENTORY_PATH)"
        ),
        SOURCE_EMPTY: "no inventory uploaded yet",
    }.get(label, label)
