"""Load an `ArchitecturalPlan` from JSON.

The loader is intentionally permissive: missing fields fall back to
schema defaults, unknown fields are ignored (so a hand-edited plan
file can carry comments / extra metadata without the loader bailing).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from placement_engine.architectural.schema import (
    DEFAULT_COLUMN_SEAM_PROXIMITY_MM,
    DEFAULT_MIN_COVERAGE_RATIO,
    DEFAULT_MIN_PIECE_HEIGHT_MM,
    DEFAULT_MIN_PIECE_WIDTH_MM,
    DEFAULT_SMALL_PIECE_THRESHOLD_MM,
    MATCHING_NONE,
    SUPPORTED_MATCHING_MODES,
    SUPPORTED_VISIBILITY_LEVELS,
    VISIBILITY_MEDIUM,
    ArchitecturalPlan,
    Column,
    Doorway,
    GuideLine,
    Space,
)


def load_architectural_plan(path: str | Path) -> ArchitecturalPlan:
    """Parse an architectural plan JSON file.

    Raises ``FileNotFoundError`` when the path doesn't exist and
    ``ValueError`` when ``matching_mode`` or a space's visibility
    isn't one of the supported values. Every other field is best-
    effort: missing → default, malformed → skipped with a note.
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"architectural plan not found: {p}")
    data = json.loads(p.read_text(encoding="utf-8"))

    matching_mode = str(data.get("matching_mode", MATCHING_NONE))
    if matching_mode not in SUPPORTED_MATCHING_MODES:
        raise ValueError(
            f"unsupported matching_mode {matching_mode!r}; "
            f"choose one of {SUPPORTED_MATCHING_MODES}"
        )

    return ArchitecturalPlan(
        target_id=str(data.get("target_id", "")),
        matching_mode=matching_mode,
        min_piece_width_mm=float(
            data.get("min_piece_width_mm", DEFAULT_MIN_PIECE_WIDTH_MM)
        ),
        min_piece_height_mm=float(
            data.get("min_piece_height_mm", DEFAULT_MIN_PIECE_HEIGHT_MM)
        ),
        small_piece_threshold_mm=float(
            data.get("small_piece_threshold_mm", DEFAULT_SMALL_PIECE_THRESHOLD_MM)
        ),
        column_seam_proximity_mm=float(
            data.get("column_seam_proximity_mm", DEFAULT_COLUMN_SEAM_PROXIMITY_MM)
        ),
        min_coverage_ratio=float(
            data.get("min_coverage_ratio", DEFAULT_MIN_COVERAGE_RATIO)
        ),
        spaces=[_space_from_dict(s) for s in data.get("spaces", [])],
        doorways=[_doorway_from_dict(d) for d in data.get("doorways", [])],
        columns=[_column_from_dict(c) for c in data.get("columns", [])],
        guide_lines=[
            _guide_line_from_dict(g) for g in data.get("guide_lines", [])
        ],
        notes=[str(n) for n in data.get("notes", [])],
    )


def _space_from_dict(raw: dict[str, Any]) -> Space:
    vis = str(raw.get("visibility", VISIBILITY_MEDIUM))
    if vis not in SUPPORTED_VISIBILITY_LEVELS:
        raise ValueError(
            f"unsupported visibility {vis!r} in space "
            f"{raw.get('space_id', '<unknown>')}; "
            f"choose one of {SUPPORTED_VISIBILITY_LEVELS}"
        )
    return Space(
        space_id=str(raw.get("space_id", "")),
        name=str(raw.get("name", "")),
        polygon=_coerce_polygon(raw.get("polygon", [])),
        visibility=vis,
        notes=[str(n) for n in raw.get("notes", [])],
    )


def _doorway_from_dict(raw: dict[str, Any]) -> Doorway:
    seg_raw = raw.get("segment", [])
    if len(seg_raw) != 2:
        raise ValueError(
            f"doorway {raw.get('doorway_id', '<unknown>')} requires a "
            f"two-point segment; got {seg_raw!r}"
        )
    return Doorway(
        doorway_id=str(raw.get("doorway_id", "")),
        segment=(
            (float(seg_raw[0][0]), float(seg_raw[0][1])),
            (float(seg_raw[1][0]), float(seg_raw[1][1])),
        ),
        is_main_entrance=bool(raw.get("is_main_entrance", False)),
        name=str(raw.get("name", "")),
        width_mm=float(raw.get("width_mm", 0.0)),
        notes=[str(n) for n in raw.get("notes", [])],
    )


def _guide_line_from_dict(raw: dict[str, Any]) -> GuideLine:
    seg_raw = raw.get("segment", [])
    if len(seg_raw) != 2:
        raise ValueError(
            f"guide_line {raw.get('guide_line_id', '<unknown>')} requires a "
            f"two-point segment; got {seg_raw!r}"
        )
    return GuideLine(
        guide_line_id=str(raw.get("guide_line_id", "")),
        segment=(
            (float(seg_raw[0][0]), float(seg_raw[0][1])),
            (float(seg_raw[1][0]), float(seg_raw[1][1])),
        ),
        priority=int(raw.get("priority", 0)),
        name=str(raw.get("name", "")),
        notes=[str(n) for n in raw.get("notes", [])],
    )


def _column_from_dict(raw: dict[str, Any]) -> Column:
    return Column(
        column_id=str(raw.get("column_id", "")),
        polygon=_coerce_polygon(raw.get("polygon", [])),
        name=str(raw.get("name", "")),
        notes=[str(n) for n in raw.get("notes", [])],
    )


def _coerce_polygon(raw: Any) -> list[tuple[float, float]]:
    out: list[tuple[float, float]] = []
    for pt in raw or []:
        if len(pt) < 2:
            continue
        out.append((float(pt[0]), float(pt[1])))
    return out
