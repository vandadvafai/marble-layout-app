"""Read-only inventory matching for the editor.

Given a piece's nominal width × height, walk the project's slab
inventory and rank slabs that could cover the piece (direct fit or
90°-rotated fit). Returns the top-K candidates per piece plus an
overall match status.

This is **not** the final assignment layer — it answers a much
narrower question: "is there at least one stock slab that can cover
this designed piece?" The future assignment layer will additionally
enforce one-slab-used-once, optimise waste across the whole job,
and consult image / vein-match constraints. This milestone ships
the read-only preview that designers can use to validate a layout
before any of that exists.

Slab data is read from the project's clean-slabs JSON (the same
fixture used by the existing layout generator). The file format is
the one produced by ``placement_engine.slab_intake`` — a top-level
dict with a ``records`` array, each record carrying ``width_mm``,
``height_mm``, ``slab_id``, ``area_m2``, and optional ``image_path``.

Out of scope:
  * one-slab-one-piece constraints (a slab can match multiple
    pieces in this preview),
  * vein / book-match,
  * cutting plan generation,
  * inventory reservation.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from placement_engine.inventory import load_inventory as _load_typed_inventory
from placement_engine.inventory.model import InventorySlab as _TypedInventorySlab

# Default Top-K returned per piece. Three keeps the panel scannable
# (designers can compare best vs. runner-up) without overwhelming
# the UI; a future milestone can plumb this through the request.
DEFAULT_TOP_K = 3

# A slab is considered an "exact fit" when its longer side wastes
# less than this many square millimetres compared to the piece.
# Three sq-mm = a ~5mm × ~5mm rounding artefact — well below any
# real cutting tolerance.
EXACT_FIT_WASTE_MM2 = 9.0


# ---------------------------------------------------------------------------
# slab loading
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class InventorySlab:
    """One stock slab as the matcher sees it.

    Narrower than ``placement_engine.inventory.model.InventorySlab`` —
    we only need the geometry and a couple of identifying fields here
    so the matching algorithm stays simple and the dataclass can be
    frozen (handy if we add caching later). Built from the typed
    inventory record via ``_from_typed`` below — that conversion is
    the only place real-inventory fields and matcher fields cross.
    """

    slab_id: str
    width_mm: float
    height_mm: float
    area_m2: float
    image_path: str | None = None
    serial_number: str | None = None
    item_code: str | None = None
    # Material / finish from the source spreadsheet. Both are nullable
    # in V1 inventories (the demo export has them as null) but the
    # field is here so a future inventory with material columns
    # populated flows through to the UI without further plumbing.
    material_name: str | None = None
    finish: str | None = None
    ingestion_warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class InventoryLoadResult:
    """Outcome of loading the configured clean_slabs.json.

    Carries the slabs the matcher will use plus the metadata the UI
    surfaces in its inventory header (source path, valid-vs-skipped
    counts). Built once per request inside the route handler — small
    dataclass, not worth caching across requests at this stage.
    """

    slabs: list[InventorySlab]
    source_path: Path
    valid_count: int
    skipped_count: int
    total_records: int


def _from_typed(typed: _TypedInventorySlab) -> InventorySlab:
    """Adapt the typed inventory record to the matcher's narrow view.

    The typed loader already validates dimensions and resolves image
    paths against the JSON's directory, so by the time we get here a
    slab is guaranteed to have positive width / height. We map only
    the fields the matcher and the UI consume — extra fields like
    ``processed_image_path`` stay on the typed model for downstream
    layers that care about them.
    """
    image_path = (
        str(typed.image_path) if typed.image_available and typed.image_path
        else None
    )
    # Fall back to the geometric area when neither catalog field is
    # set — matches the old behaviour but reads more obviously.
    area_m2 = (
        typed.area_m2 if typed.area_m2 is not None
        else typed.calculated_area_m2 if typed.calculated_area_m2 is not None
        else (typed.width_mm * typed.height_mm) / 1_000_000.0
    )
    return InventorySlab(
        slab_id=typed.slab_id,
        width_mm=typed.width_mm,
        height_mm=typed.height_mm,
        area_m2=float(area_m2),
        image_path=image_path,
        serial_number=typed.serial_number,
        item_code=typed.item_code,
        # `material_name` / `finish` aren't on the typed model yet —
        # the slab-intake pipeline drops them. Left here for forward
        # compatibility; both are ``None`` in V1.
        material_name=None,
        finish=None,
        ingestion_warnings=tuple(typed.ingestion_warnings),
    )


def load_inventory_slabs(path: str | Path) -> InventoryLoadResult:
    """Load a project's clean_slabs.json into a matcher-ready result.

    Delegates the JSON parsing + dimension validation + image-path
    resolution to ``placement_engine.inventory.load_inventory`` so we
    get the same skipped-record tracking the rest of the engine
    relies on. The conversion to the matcher's narrow ``InventorySlab``
    happens once per call here.

    Raises ``FileNotFoundError`` when the path doesn't exist (same
    behaviour as the typed loader).
    """
    p = Path(path)
    typed = _load_typed_inventory(p)
    slabs = [_from_typed(s) for s in typed.slabs]
    return InventoryLoadResult(
        slabs=slabs,
        source_path=typed.source_json,
        valid_count=len(slabs),
        skipped_count=len(typed.skipped_records),
        total_records=len(slabs) + len(typed.skipped_records),
    )


# ---------------------------------------------------------------------------
# matching
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SlabCandidate:
    """One slab that can cover a given piece.

    Dimension fields (mm):
      * ``width_mm`` / ``height_mm`` — the slab's ORIGINAL size as
        recorded in the inventory (the Excel `width_mm/height_mm`
        columns). These do NOT swap when ``rotation_needed`` is
        true — the slab itself doesn't get smaller, the cutter just
        orients it differently.
      * ``cut_width_mm`` / ``cut_height_mm`` (0.1.49) — the size of
        the FINAL piece that's cut OUT of the slab. Today that's
        just the piece's nominal w × h (the matcher confirmed the
        slab covers it); a future milestone may differ when the
        polygon clip is more elaborate. Exposed as its own pair so
        the frontend's Properties panel can show "piece size",
        "original slab size", and "final cut size" without
        ambiguity.

    ``rotation_needed`` is True when the slab covers the piece only
    after a 90° rotation. ``waste_mm2`` and ``waste_fraction`` are
    computed against the slab's full area — they describe the
    designer-visible material that won't go into this piece.
    ``material_name`` and ``item_code`` flow through from the
    inventory record so the UI can surface them — both are
    null-tolerant since not every inventory carries them.
    """
    slab_id: str
    width_mm: float
    height_mm: float
    cut_width_mm: float
    cut_height_mm: float
    waste_mm2: float
    waste_fraction: float
    rotation_needed: bool
    image_path: str | None = None
    serial_number: str | None = None
    item_code: str | None = None
    material_name: str | None = None
    finish: str | None = None


# Match-status enum. Kept as string literals for direct JSON-friendly
# serialisation; the UI matches against these names.
STATUS_EXACT_FIT = "exact_fit"
STATUS_MATCHED = "matched"
STATUS_MULTIPLE_OPTIONS = "multiple_options"
STATUS_NO_MATCH = "no_match"


def match_piece(
    piece_width_mm: float, piece_height_mm: float,
    slabs: Iterable[InventorySlab],
    *,
    allow_rotation: bool = True,
    top_k: int = DEFAULT_TOP_K,
) -> tuple[str, list[SlabCandidate]]:
    """Find slabs that can cover ``piece_width_mm × piece_height_mm``.

    Returns a ``(status, candidates)`` tuple. ``candidates`` is
    sorted by least waste first and capped at ``top_k`` entries.
    ``status`` is one of the four STATUS_* constants.

    Direct fit: slab.w >= piece.w AND slab.h >= piece.h.
    Rotated fit (only when ``allow_rotation``): same comparison with
    the slab's axes swapped.

    Square slabs (w == h) are evaluated once even if rotation is
    allowed — the rotated fit would be geometrically identical.
    """
    if piece_width_mm <= 0 or piece_height_mm <= 0:
        return STATUS_NO_MATCH, []

    piece_area_mm2 = piece_width_mm * piece_height_mm

    # Each match becomes a candidate. We collect both the direct +
    # rotated fits as separate candidates when they differ, then
    # dedupe to the best-waste per slab so the UI doesn't show the
    # same slab twice.
    best_per_slab: dict[str, SlabCandidate] = {}
    for slab in slabs:
        for rotation_needed in (False, True):
            if not allow_rotation and rotation_needed:
                continue
            if rotation_needed and slab.width_mm == slab.height_mm:
                # Square — rotated == direct, skip the duplicate.
                continue
            slab_w = slab.height_mm if rotation_needed else slab.width_mm
            slab_h = slab.width_mm if rotation_needed else slab.height_mm
            if slab_w < piece_width_mm or slab_h < piece_height_mm:
                continue
            slab_area_mm2 = slab.width_mm * slab.height_mm
            waste_mm2 = slab_area_mm2 - piece_area_mm2
            waste_fraction = waste_mm2 / slab_area_mm2 if slab_area_mm2 else 0.0
            # Final cut = the piece dimensions. The matcher already
            # verified slab_w/h ≥ piece_w/h (with rotation applied),
            # so we never publish a cut larger than the slab in either
            # axis. Recorded explicitly here so the UI doesn't have to
            # re-derive it and so a future fancier clip (e.g. for L-
            # shaped pieces) only changes this number.
            cut_width_mm = piece_width_mm
            cut_height_mm = piece_height_mm
            cand = SlabCandidate(
                slab_id=slab.slab_id,
                width_mm=slab.width_mm,
                height_mm=slab.height_mm,
                cut_width_mm=cut_width_mm,
                cut_height_mm=cut_height_mm,
                waste_mm2=waste_mm2,
                waste_fraction=waste_fraction,
                rotation_needed=rotation_needed,
                image_path=slab.image_path,
                serial_number=slab.serial_number,
                item_code=slab.item_code,
                material_name=slab.material_name,
                finish=slab.finish,
            )
            prev = best_per_slab.get(slab.slab_id)
            if prev is None or cand.waste_mm2 < prev.waste_mm2:
                best_per_slab[slab.slab_id] = cand

    candidates = sorted(
        best_per_slab.values(),
        key=lambda c: (c.waste_mm2, c.slab_id),
    )[:top_k]

    if not candidates:
        return STATUS_NO_MATCH, []
    if candidates[0].waste_mm2 <= EXACT_FIT_WASTE_MM2:
        return STATUS_EXACT_FIT, candidates
    if len(candidates) >= 2:
        return STATUS_MULTIPLE_OPTIONS, candidates
    return STATUS_MATCHED, candidates


# ---------------------------------------------------------------------------
# JSON serialisation
# ---------------------------------------------------------------------------


def candidate_to_dict(c: SlabCandidate) -> dict[str, Any]:
    return {
        "slab_id": c.slab_id,
        "serial_number": c.serial_number,
        "item_code": c.item_code,
        "material_name": c.material_name,
        "finish": c.finish,
        "width_mm": c.width_mm,
        "height_mm": c.height_mm,
        "cut_width_mm": c.cut_width_mm,
        "cut_height_mm": c.cut_height_mm,
        "waste_mm2": round(c.waste_mm2, 3),
        "waste_fraction": round(c.waste_fraction, 4),
        "rotation_needed": c.rotation_needed,
        "image_path": c.image_path,
    }


def status_label(status: str) -> str:
    """Designer-facing label for a status string. Mirrored on the
    frontend; centralised here so the two stay in sync."""
    return {
        STATUS_EXACT_FIT: "exact fit",
        STATUS_MATCHED: "matched",
        STATUS_MULTIPLE_OPTIONS: "multiple options",
        STATUS_NO_MATCH: "no match",
    }.get(status, status)
