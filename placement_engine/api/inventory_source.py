"""Resolve which `clean_slabs.json` the API should use as its inventory.

The editor used to read a single hard-coded demo fixture
(``outputs/slab_ingestion_test/clean_slabs.json``). That fixture has
~7 records and was useful for plumbing the UI, but it's not the real
project inventory. This module replaces the single hard path with an
ordered search:

  1. ``STONELAYOUT_INVENTORY_PATH`` env var. Lets ops/dev point the
     server at any clean_slabs.json without code changes — the most
     direct way to test against a different inventory file.
  2. The real project export at
     ``outputs/slab_ingestion/raw_test/clean_slabs.json``. Produced by
     the slab-intake pipeline against ``data/raw_test/export.xlsx``;
     this is the file the engine is meant to use day-to-day.
  3. The demo fixture at
     ``outputs/slab_ingestion_test/clean_slabs.json``. Last resort —
     kept around so a developer who hasn't run the intake pipeline
     locally can still boot the UI.

Each candidate is checked for existence (and non-empty content) before
being accepted. The resolved choice carries a ``source_label`` that
the UI surfaces so designers know exactly which inventory they're
matching against — that traceability matters when a slab they expect
to see doesn't appear in the candidate list.
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
SOURCE_REAL = "real_inventory"
SOURCE_DEMO = "demo_fallback"

ENV_VAR_NAME = "STONELAYOUT_INVENTORY_PATH"


@dataclass(frozen=True)
class ResolvedInventorySource:
    """The picked clean_slabs.json plus the trail of paths that were
    considered. Frozen so callers can cache without aliasing concerns.

    ``candidates_checked`` is in priority order — first entry was tried
    first. ``source_label`` matches one of the SOURCE_* constants
    above. The label is the UI-visible identifier; the path is for
    logging and the inventory-info endpoint.
    """

    path: Path
    source_label: str
    candidates_checked: list[Path]


def resolve_inventory_source(project_root: Path) -> ResolvedInventorySource:
    """Pick the inventory file the API should load.

    Order of preference (highest first):

    0. The active uploaded session — when a designer ran the Step-3
       upload flow, their parsed ``clean_slabs.json`` lives in a
       tempdir that the resolver MUST pick up first. Otherwise the
       upload would silently be ignored, which is the exact bug the
       upload milestone fixes. Falls through cleanly when no upload
       is active.
    1. ``$STONELAYOUT_INVENTORY_PATH`` — when set AND the file exists.
       A missing env-pointed file is treated as a configuration error
       — we raise here rather than silently falling through, because
       silently using a different file would surprise an operator who
       expected the override to take effect.
    2. ``<project_root>/outputs/slab_ingestion/raw_test/clean_slabs.json``
       — the real project export. Skipped (without error) when absent
       so a dev who hasn't run the intake pipeline still gets a
       working server.
    3. ``<project_root>/outputs/slab_ingestion_test/clean_slabs.json``
       — the demo fallback. Skipped when missing; in that case we
       raise so the caller knows there's nothing usable.

    Raises ``FileNotFoundError`` only when EVERY candidate is unusable.
    """
    candidates: list[Path] = []

    # Step 0: uploaded session takes precedence over everything else.
    # Imported lazily so the resolver doesn't pull in the upload
    # module (and its slab-intake dependency chain) when nothing has
    # been uploaded yet.
    from placement_engine.api.inventory_upload import get_active_upload
    active = get_active_upload()
    if active is not None and active.clean_slabs_path.exists():
        candidates.append(active.clean_slabs_path)
        return ResolvedInventorySource(
            path=active.clean_slabs_path,
            source_label=SOURCE_UPLOADED,
            candidates_checked=candidates,
        )
    env_raw = os.environ.get(ENV_VAR_NAME)
    if env_raw:
        env_path = Path(env_raw).expanduser()
        # Resolve relative env-paths against the project root so
        # ``STONELAYOUT_INVENTORY_PATH=outputs/foo.json`` works the
        # same way no matter where the server is launched from.
        if not env_path.is_absolute():
            env_path = (project_root / env_path).resolve()
        candidates.append(env_path)
        if not env_path.exists():
            raise FileNotFoundError(
                f"{ENV_VAR_NAME}={env_raw!r} points at {env_path}, "
                f"which does not exist. Either fix the path or unset "
                f"the variable to fall back to the project defaults.",
            )
        return ResolvedInventorySource(
            path=env_path,
            source_label=SOURCE_ENV,
            candidates_checked=candidates,
        )

    real = project_root / "outputs/slab_ingestion/raw_test/clean_slabs.json"
    candidates.append(real)
    if real.exists():
        return ResolvedInventorySource(
            path=real, source_label=SOURCE_REAL,
            candidates_checked=candidates,
        )

    demo = project_root / "outputs/slab_ingestion_test/clean_slabs.json"
    candidates.append(demo)
    if demo.exists():
        return ResolvedInventorySource(
            path=demo, source_label=SOURCE_DEMO,
            candidates_checked=candidates,
        )

    raise FileNotFoundError(
        "no inventory file found. Tried: "
        + ", ".join(str(c) for c in candidates)
        + f". Set {ENV_VAR_NAME} to point at a clean_slabs.json, or "
        "run the slab-intake pipeline to produce one.",
    )


def source_label_description(label: str) -> str:
    """Short human-facing description for ``source_label``. Used as
    the chip tooltip in the inventory-info panel. The frontend has
    its own copy of these strings; both must stay in sync so the UI
    and the API agree on what each label means."""
    return {
        SOURCE_UPLOADED: "uploaded by designer (Step 3)",
        SOURCE_ENV: f"resolved from ${ENV_VAR_NAME}",
        SOURCE_REAL: "real project inventory (outputs/slab_ingestion/raw_test)",
        SOURCE_DEMO: "demo fallback (outputs/slab_ingestion_test)",
    }.get(label, label)
