"""Generate clean synthetic standardized DXF inputs (+ PNG previews).

These files are **not** customer CAD plans. They are tiny, deterministic
DXFs that follow the engine's input convention exactly:

    AI_PROJECT_BOUNDARY   one closed polyline (the installation surface)
    AI_HOLES_CUTOUTS      zero or more closed polylines (columns, voids)
    AI_IGNORE             (always created; left empty here)

Use them to smoke-test the CAD intake, to verify the engine on
hand-crafted fixtures, or to share with a designer who wants a clean
DXF to compare against in Rhino/AutoCAD.

The `DEMOS` list is importable so tests can re-use the same specs
without depending on committed artifacts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

import ezdxf
import matplotlib

matplotlib.use("Agg")  # headless
import matplotlib.patches as mpatches
import matplotlib.pyplot as plt
from matplotlib.patches import PathPatch
from matplotlib.path import Path as MplPath

from placement_engine.cad_intake.dxf_reader import (
    LAYER_HOLES_CUTOUTS,
    LAYER_IGNORE,
    LAYER_PROJECT_BOUNDARY,
)
from placement_engine.cad_intake.inspection import inspect_dxf
from placement_engine.models import PolygonCoords


# ---------------------------------------------------------------------------
# Demo specifications
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DemoSpec:
    name: str
    title: str
    boundary: PolygonCoords
    holes: Sequence[PolygonCoords] = field(default_factory=tuple)


DEMOS: tuple[DemoSpec, ...] = (
    DemoSpec(
        name="demo_rectangle_floor",
        title="Demo: simple rectangle floor (6 m × 4 m)",
        boundary=[(0, 0), (6000, 0), (6000, 4000), (0, 4000)],
    ),
    DemoSpec(
        name="demo_l_shape_floor",
        title="Demo: L-shaped floor (8 m × 4 m with notch)",
        boundary=[
            (0, 0), (8000, 0), (8000, 4000),
            (4800, 4000), (4800, 2600), (0, 2600),
        ],
    ),
    DemoSpec(
        name="demo_floor_with_column",
        title="Demo: floor with central column (7 m × 4.5 m, 600 × 600 mm hole)",
        boundary=[(0, 0), (7000, 0), (7000, 4500), (0, 4500)],
        holes=[[(3200, 1950), (3800, 1950), (3800, 2550), (3200, 2550)]],
    ),
    DemoSpec(
        name="demo_irregular_apartment_floor",
        title="Demo: irregular apartment floor (L-shape + 2 columns + utility shaft)",
        # Lower wing: x∈[0, 8 000], y∈[0, 3 000]
        # Upper wing: x∈[0, 12 000], y∈[3 000, 8 000]
        boundary=[
            (0, 0), (8000, 0), (8000, 3000),
            (12000, 3000), (12000, 8000), (0, 8000),
        ],
        holes=[
            # 400 × 400 mm column in the lower wing
            [(2000, 2000), (2400, 2000), (2400, 2400), (2000, 2400)],
            # 400 × 400 mm column in the upper wing
            [(6000, 5000), (6400, 5000), (6400, 5400), (6000, 5400)],
            # 1 000 × 1 000 mm utility shaft in the upper-right
            [(9500, 5500), (10500, 5500), (10500, 6500), (9500, 6500)],
        ],
    ),
    DemoSpec(
        name="demo_long_corridor",
        title="Demo: long corridor (18 m × 2 m)",
        boundary=[(0, 0), (18000, 0), (18000, 2000), (0, 2000)],
    ),
)


# ---------------------------------------------------------------------------
# DXF writer
# ---------------------------------------------------------------------------


def write_demo_dxf(spec: DemoSpec, target: str | Path) -> Path:
    """Write `spec` to a standardized DXF and return the path."""
    target_path = Path(target)
    target_path.parent.mkdir(parents=True, exist_ok=True)

    doc = ezdxf.new(dxfversion="R2013", setup=True)
    doc.units = ezdxf.units.MM
    doc.layers.add(name=LAYER_PROJECT_BOUNDARY, color=7)
    doc.layers.add(name=LAYER_HOLES_CUTOUTS, color=1)
    doc.layers.add(name=LAYER_IGNORE, color=8)

    msp = doc.modelspace()
    msp.add_lwpolyline(
        list(spec.boundary),
        close=True,
        dxfattribs={"layer": LAYER_PROJECT_BOUNDARY},
    )
    for hole in spec.holes:
        msp.add_lwpolyline(
            list(hole),
            close=True,
            dxfattribs={"layer": LAYER_HOLES_CUTOUTS},
        )
    doc.saveas(target_path)
    return target_path


# ---------------------------------------------------------------------------
# PNG preview (matplotlib)
# ---------------------------------------------------------------------------


def _polygon_patch(coords: Sequence[tuple[float, float]], **kwargs) -> PathPatch:
    verts = list(coords) + [coords[0]]
    codes = (
        [MplPath.MOVETO]
        + [MplPath.LINETO] * (len(coords) - 1)
        + [MplPath.CLOSEPOLY]
    )
    return PathPatch(MplPath(verts, codes), **kwargs)


def write_demo_preview(spec: DemoSpec, target: str | Path) -> Path:
    """Render a quick PNG of the demo's geometry and return the path."""
    target_path = Path(target)
    target_path.parent.mkdir(parents=True, exist_ok=True)

    fig, ax = plt.subplots(figsize=(11, 7))

    ax.add_patch(_polygon_patch(
        spec.boundary,
        facecolor="#e8eef5",
        edgecolor="black",
        linewidth=2.5,
    ))
    for hole in spec.holes:
        ax.add_patch(_polygon_patch(
            hole,
            facecolor="#f4cccc",
            edgecolor="#d62728",
            hatch="///",
            linewidth=1.0,
        ))

    handles = [
        mpatches.Patch(
            facecolor="#e8eef5", edgecolor="black",
            label=LAYER_PROJECT_BOUNDARY,
        )
    ]
    if spec.holes:
        handles.append(mpatches.Patch(
            facecolor="#f4cccc", edgecolor="#d62728", hatch="///",
            label=LAYER_HOLES_CUTOUTS,
        ))
    ax.legend(handles=handles, loc="upper right", fontsize=9, frameon=False)

    # Annotate the boundary bounding box so the dimensions are obvious.
    xs = [pt[0] for pt in spec.boundary]
    ys = [pt[1] for pt in spec.boundary]
    bbox_w = max(xs) - min(xs)
    bbox_h = max(ys) - min(ys)
    ax.set_title(
        f"{spec.title}\nbbox: {bbox_w:.0f} × {bbox_h:.0f} mm   "
        f"holes: {len(spec.holes)}",
        fontsize=11,
    )
    ax.set_xlabel("x (mm)")
    ax.set_ylabel("y (mm)")
    ax.set_aspect("equal", adjustable="box")
    ax.grid(True, linestyle=":", linewidth=0.5, alpha=0.5)
    ax.autoscale_view()
    fig.tight_layout()
    fig.savefig(target_path, dpi=140, bbox_inches="tight")
    plt.close(fig)
    return target_path


# ---------------------------------------------------------------------------
# Orchestrator (also runs the inspection on each generated file)
# ---------------------------------------------------------------------------


DEFAULT_DXF_DIR = Path("examples/cad_inputs/demo")
DEFAULT_PREVIEW_DIR = Path("outputs/demo_cad_previews")


def generate_all(
    dxf_dir: str | Path = DEFAULT_DXF_DIR,
    preview_dir: str | Path = DEFAULT_PREVIEW_DIR,
    *,
    verbose: bool = True,
) -> list[dict]:
    """Generate every demo + its preview; inspect the DXF afterwards.

    Returns a list of result dicts: `{name, dxf, png, errors, warnings}`.
    """
    dxf_dir = Path(dxf_dir)
    preview_dir = Path(preview_dir)
    dxf_dir.mkdir(parents=True, exist_ok=True)
    preview_dir.mkdir(parents=True, exist_ok=True)

    results: list[dict] = []
    for spec in DEMOS:
        dxf_path = write_demo_dxf(spec, dxf_dir / f"{spec.name}.dxf")
        png_path = write_demo_preview(spec, preview_dir / f"{spec.name}.png")
        report = inspect_dxf(dxf_path)
        results.append({
            "name": spec.name,
            "dxf": dxf_path,
            "png": png_path,
            "errors": list(report.errors),
            "warnings": list(report.warnings),
            "boundary_area_mm2": report.boundary_area_mm2,
            "hole_count": report.hole_polyline_count,
        })
        if verbose:
            status = "✅" if not report.errors else "❌"
            extra = (
                f"  errors: {report.errors}" if report.errors
                else f"  boundary_area={report.boundary_area_mm2:.0f} mm², "
                     f"holes={report.hole_polyline_count}"
            )
            print(f"  {status}  {spec.name}{extra}")
    return results


def main() -> int:
    print(f"Writing {len(DEMOS)} demo DXFs → {DEFAULT_DXF_DIR}")
    print(f"Writing {len(DEMOS)} previews   → {DEFAULT_PREVIEW_DIR}")
    print()
    results = generate_all()
    failed = [r for r in results if r["errors"]]
    print()
    if failed:
        print(f"FAILED: {len(failed)} demo(s) did not pass inspection.")
        return 1
    print(f"All {len(results)} demos passed inspection.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
