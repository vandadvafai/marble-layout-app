"""Inspect a standardized DXF and report what the intake will see.

Useful when a conversion fails or when the designer wants to confirm
the DXF is clean *before* running the engine. Produces both a
structured dict (for tests) and a Markdown report (for humans).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from shapely.geometry import Polygon as ShPolygon

from placement_engine.cad_intake.dxf_reader import (
    CADIntakeError,
    LAYER_HOLES_CUTOUTS,
    LAYER_PROJECT_BOUNDARY,
    entities_on_layer,
    known_layers,
    layer_summary,
    read_dxf,
)
from placement_engine.cad_intake.geometry_extractor import (
    extract_closed_polylines,
)


@dataclass
class InspectionReport:
    """Structured snapshot of what's in a standardized DXF."""

    path: str
    layers: dict[str, dict[str, int]] = field(default_factory=dict)
    boundary_polyline_count: int = 0
    hole_polyline_count: int = 0
    boundary_area_mm2: float | None = None
    boundary_bbox: tuple[float, float, float, float] | None = None
    hole_areas_mm2: list[float] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "layers": self.layers,
            "boundary_polyline_count": self.boundary_polyline_count,
            "hole_polyline_count": self.hole_polyline_count,
            "boundary_area_mm2": self.boundary_area_mm2,
            "boundary_bbox": list(self.boundary_bbox) if self.boundary_bbox else None,
            "hole_areas_mm2": list(self.hole_areas_mm2),
            "warnings": list(self.warnings),
            "errors": list(self.errors),
        }


def inspect_dxf(cad_path: str | Path) -> InspectionReport:
    """Read the DXF and assemble an `InspectionReport`.

    Never raises for content reasons — every problem is captured in
    `report.errors` or `report.warnings`. (Genuinely unreadable files
    still propagate `CADIntakeError`.)
    """
    cad_path = Path(cad_path)
    report = InspectionReport(path=str(cad_path))

    doc = read_dxf(cad_path)
    report.layers = layer_summary(doc)

    # Warn about layers we don't recognise so the designer notices typos.
    seen_layers = set(report.layers)
    recognised = set(known_layers()) | {"0", "Defpoints"}
    extras = sorted(seen_layers - recognised)
    if extras:
        report.warnings.append(
            f"Found layers the intake ignores: {', '.join(extras)}"
        )

    # Boundary
    boundary_entities = entities_on_layer(doc, LAYER_PROJECT_BOUNDARY)
    if not boundary_entities:
        report.errors.append(
            f"Layer {LAYER_PROJECT_BOUNDARY!r} is empty or missing. "
            "Draw exactly one closed polyline on this layer."
        )
    else:
        try:
            boundaries = extract_closed_polylines(
                boundary_entities, LAYER_PROJECT_BOUNDARY
            )
        except CADIntakeError as exc:
            report.errors.append(str(exc))
            boundaries = []
        report.boundary_polyline_count = len(boundaries)
        if len(boundaries) != 1:
            report.errors.append(
                f"Expected exactly one closed polyline on "
                f"{LAYER_PROJECT_BOUNDARY!r}, found "
                f"{len(boundaries)}."
            )
        for b in boundaries[:1]:
            poly = ShPolygon(b)
            if poly.is_valid and poly.area > 0:
                report.boundary_area_mm2 = float(poly.area)
                report.boundary_bbox = tuple(float(v) for v in poly.bounds)  # type: ignore[assignment]
            else:
                report.errors.append(
                    f"Boundary polyline is invalid or has zero area."
                )

    # Holes
    hole_entities = entities_on_layer(doc, LAYER_HOLES_CUTOUTS)
    holes: list = []
    try:
        holes = extract_closed_polylines(hole_entities, LAYER_HOLES_CUTOUTS)
    except CADIntakeError as exc:
        report.errors.append(str(exc))
    report.hole_polyline_count = len(holes)
    for h in holes:
        poly = ShPolygon(h)
        report.hole_areas_mm2.append(float(poly.area))

    return report


def format_report_markdown(report: InspectionReport) -> str:
    """Render an `InspectionReport` as a designer-readable Markdown doc."""
    lines: list[str] = []
    lines += [f"# CAD Intake Inspection — `{report.path}`", ""]

    lines += ["## Layers found", ""]
    if report.layers:
        lines += ["| Layer | Entity counts |", "|---|---|"]
        for layer in sorted(report.layers):
            counts = ", ".join(
                f"{k}={v}" for k, v in sorted(report.layers[layer].items())
            )
            lines.append(f"| `{layer}` | {counts} |")
    else:
        lines.append("_No modelspace entities found._")
    lines.append("")

    lines += [
        "## Project boundary",
        "",
        f"- closed polylines on `{LAYER_PROJECT_BOUNDARY}`: "
        f"**{report.boundary_polyline_count}**",
    ]
    if report.boundary_area_mm2 is not None:
        lines.append(f"- area: **{report.boundary_area_mm2:.0f} mm²**")
    if report.boundary_bbox is not None:
        xmin, ymin, xmax, ymax = report.boundary_bbox
        lines.append(
            f"- bounding box: x = {xmin:.0f}–{xmax:.0f} mm, "
            f"y = {ymin:.0f}–{ymax:.0f} mm"
        )
    lines.append("")

    lines += [
        "## Holes / cutouts",
        "",
        f"- closed polylines on `{LAYER_HOLES_CUTOUTS}`: "
        f"**{report.hole_polyline_count}**",
    ]
    for i, area in enumerate(report.hole_areas_mm2, start=1):
        lines.append(f"- hole #{i} area: {area:.0f} mm²")
    lines.append("")

    lines += ["## Warnings", ""]
    if report.warnings:
        for w in report.warnings:
            lines.append(f"- {w}")
    else:
        lines.append("_None._")
    lines.append("")

    lines += ["## Errors", ""]
    if report.errors:
        for e in report.errors:
            lines.append(f"- **{e}**")
    else:
        lines.append("_None._")
    lines.append("")

    return "\n".join(lines)
