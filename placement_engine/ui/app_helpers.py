"""Shared package-generation orchestration + small display helpers.

`generate_layout_package` is the one callable that runs the full
pipeline — CAD intake → engine → per-strategy hand-off package — and
returns a `PackageResult`. Both `make_package.py` (CLI) and
`streamlit_app.py` (local UI) call it, so there is exactly one
implementation of the workflow.

The remaining helpers (`build_package_zip`, `headline_metrics`,
`split_review_markers`) are thin presentation aids used by the UI.
"""

from __future__ import annotations

import io
import json
import shutil
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

from placement_engine import engine
from placement_engine.cad_intake import build_project_input_dict
from placement_engine.cad_intake.inspection import (
    InspectionReport,
    format_report_markdown,
    inspect_cad_file,
)
from placement_engine.exporters.dxf_exporter import write_dxf
from placement_engine.exporters.markdown_report import write_report
from placement_engine.models import EngineOutput, LayoutOption, ProjectInput
from placement_engine.utils.test_inventory import SlabInventorySpec
from placement_engine.visualization.debug_plot import render_layout


# ---------------------------------------------------------------------------
# Result container
# ---------------------------------------------------------------------------


@dataclass
class PackageResult:
    """Everything a caller needs after a package run.

    `per_strategy_files[strategy]` maps a file kind (`json`, `dxf`,
    `report`, `preview`) to the written `Path`. `preview` is absent
    when `generate_preview=False`.
    """

    output_dir: Path
    project_id: str
    project_type: str
    payload: dict                       # the engine input JSON (as a dict)
    engine_output: EngineOutput
    inspection: InspectionReport
    cad_inspection_path: Path
    generated_input_path: Path
    per_strategy_files: dict[str, dict[str, Path]] = field(default_factory=dict)

    def option(self, strategy: str) -> LayoutOption:
        """The `LayoutOption` for a given strategy name."""
        for opt in self.engine_output.layout_options:
            if opt.strategy == strategy:
                return opt
        raise KeyError(f"strategy {strategy!r} not in engine output")

    @property
    def strategies(self) -> list[str]:
        return [o.strategy for o in self.engine_output.layout_options]


# ---------------------------------------------------------------------------
# Internal: per-strategy file writing
# ---------------------------------------------------------------------------


def _slug(name: str) -> str:
    """Filesystem-safe slug for a strategy subfolder name."""
    return "".join(c if c.isalnum() else "_" for c in name).strip("_").lower()


def _write_strategy_package(
    project: ProjectInput,
    output: EngineOutput,
    option: LayoutOption,
    option_index: int,
    strategy_dir: Path,
    render_preview: bool,
) -> dict[str, Path]:
    """Write one strategy's layout.json / layout.dxf / layout_report.md
    (+ preview.png) into `strategy_dir`. Returns the written paths."""
    strategy_dir.mkdir(parents=True, exist_ok=True)

    single = output.model_copy(update={"layout_options": [option]})
    json_path = strategy_dir / "layout.json"
    json_path.write_text(json.dumps(single.to_json_dict(), indent=2))

    dxf_path = write_dxf(project, option, strategy_dir / "layout.dxf")
    report_path = write_report(
        project, output, option, strategy_dir / "layout_report.md"
    )

    written = {"json": json_path, "dxf": dxf_path, "report": report_path}
    if render_preview:
        preview_path = strategy_dir / "preview.png"
        render_layout(project, output, preview_path, option_index=option_index)
        written["preview"] = preview_path
    return written


# ---------------------------------------------------------------------------
# Orchestration — the single shared entry point
# ---------------------------------------------------------------------------


def generate_layout_package(
    cad_path: str | Path,
    *,
    project_id: str,
    output_dir: str | Path,
    project_type: str = "floor",
    strategies: Sequence[str] = ("balanced", "lowest_waste"),
    include_test_slabs: bool = True,
    test_slab_count: str | int = "auto",
    test_slab_width: float = 3200.0,
    test_slab_height: float = 1800.0,
    test_slab_thickness: float = 20.0,
    slab_buffer_factor: float = 1.25,
    generate_preview: bool = True,
    clean_output: bool = True,
    keep_intermediate: bool = False,
    conversion_backend: str = "auto",
    oda_path: str | Path | None = None,
) -> PackageResult:
    """Run the full CAD → layout package pipeline and return a `PackageResult`.

    Raises on bad input rather than swallowing it — `CADConversionError`
    / `CADIntakeError` for bad CAD geometry, `FileNotFoundError` for a
    missing file, `ValueError` when no slab inventory is configured,
    `pydantic.ValidationError` if the assembled input is invalid. The
    caller (CLI or UI) is responsible for presenting those.
    """
    cad_path = Path(cad_path)
    output_dir = Path(output_dir)

    if not cad_path.is_file():
        raise FileNotFoundError(f"CAD file not found: {cad_path}")
    if not include_test_slabs:
        raise ValueError(
            "No slab inventory was provided. Enable the synthetic test "
            "inventory, or supply a real slab inventory once database "
            "integration is available."
        )

    slab_spec = SlabInventorySpec(
        count=test_slab_count,
        width=test_slab_width,
        height=test_slab_height,
        thickness=test_slab_thickness,
        buffer_factor=slab_buffer_factor,
    )

    # 1. CAD intake (+ DWG conversion if needed) → engine input dict.
    payload = build_project_input_dict(
        cad_path,
        project_id=project_id,
        project_type=project_type,
        test_slab_spec=slab_spec,
        options_requested=list(strategies),
        conversion_backend=conversion_backend,
        oda_path=oda_path,
    )

    # 2. Prepare the output folder.
    if clean_output and output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # 3. CAD inspection report (never raises for content reasons).
    inspection = inspect_cad_file(
        cad_path, conversion_backend=conversion_backend, oda_path=oda_path
    )
    cad_inspection_path = output_dir / "cad_inspection.md"
    cad_inspection_path.write_text(format_report_markdown(inspection))

    # 4. Persist the generated engine input.
    generated_input_path = output_dir / "generated_engine_input.json"
    generated_input_path.write_text(json.dumps(payload, indent=2))

    # 5. Validate + run the placement engine.
    project = ProjectInput.model_validate(payload)
    engine_output = engine.run(project)

    if keep_intermediate:
        internal = output_dir / "internal"
        internal.mkdir(parents=True, exist_ok=True)
        (internal / "full_engine_output.json").write_text(
            json.dumps(engine_output.to_json_dict(), indent=2)
        )

    # 6. Per-strategy hand-off packages.
    per_strategy_files: dict[str, dict[str, Path]] = {}
    for index, option in enumerate(engine_output.layout_options):
        per_strategy_files[option.strategy] = _write_strategy_package(
            project=project,
            output=engine_output,
            option=option,
            option_index=index,
            strategy_dir=output_dir / _slug(option.strategy),
            render_preview=generate_preview,
        )

    return PackageResult(
        output_dir=output_dir,
        project_id=project_id,
        project_type=project_type,
        payload=payload,
        engine_output=engine_output,
        inspection=inspection,
        cad_inspection_path=cad_inspection_path,
        generated_input_path=generated_input_path,
        per_strategy_files=per_strategy_files,
    )


# ---------------------------------------------------------------------------
# Presentation helpers (used by the Streamlit UI)
# ---------------------------------------------------------------------------


def build_package_zip(
    root: Path, zip_path: Path | None = None
) -> bytes:
    """Zip the contents of `root` and return the archive bytes.

    Skips any `*.zip` already in `root` (so re-zipping is idempotent)
    and `__pycache__` directories. If `zip_path` is given, the archive
    is also written there.
    """
    root = Path(root)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            if path.suffix == ".zip":
                continue
            if "__pycache__" in path.parts:
                continue
            zf.write(path, path.relative_to(root))
    data = buffer.getvalue()
    if zip_path is not None:
        Path(zip_path).write_bytes(data)
    return data


# The 8 metrics the UI shows as headline numbers for each strategy.
_HEADLINE_FIELDS = (
    "layout_status",
    "inventory_status",
    "coverage_percentage",
    "waste_percentage",
    "slabs_used",
    "piece_count",
    "seam_count",
    "total_seam_length",
)


def headline_metrics(option: LayoutOption) -> dict[str, object]:
    """Extract the headline metric values the UI displays for a strategy."""
    m = option.metrics
    return {field: getattr(m, field) for field in _HEADLINE_FIELDS}


# Marker types that are informational rather than blocking — shown in a
# collapsed "Technical notes" section rather than as a primary warning.
_TECHNICAL_MARKER_TYPES = {"empty_slab_placement_skipped"}


def split_review_markers(option: LayoutOption) -> tuple[list, list]:
    """Partition an option's review markers into (primary, technical).

    Primary markers (incomplete_coverage, insufficient_inventory,
    piece_risk, …) are designer-facing warnings. Technical markers
    (empty_slab_placement_skipped) are routine engine bookkeeping and
    are tucked into a collapsed section in the UI.
    """
    primary = [m for m in option.review_markers
               if m.type not in _TECHNICAL_MARKER_TYPES]
    technical = [m for m in option.review_markers
                 if m.type in _TECHNICAL_MARKER_TYPES]
    return primary, technical
