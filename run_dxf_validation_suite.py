"""Validate the full standardized-DXF pipeline across a folder of DXFs.

For every `*.dxf` in `--cad-dir` the suite runs:

    DXF → CAD inspection
        → engine input JSON (+ auto-sized test slab inventory)
        → placement engine
        → per-strategy DXF + report + preview output

and collects one summary row per (DXF, strategy). The combined result
is written to `<out-dir>/validation_summary.md`.

Usage:
    python3 run_dxf_validation_suite.py \\
        --cad-dir examples/cad_inputs/demo \\
        --out-dir outputs/dxf_validation_runs \\
        --include-test-slabs \\
        --test-slab-count auto \\
        --strategies balanced lowest_waste

Pass/fail (per DXF × strategy): a case PASSES when the input JSON
validates, the engine runs without error, the output files are
written, `layout_status == "complete"`, `inventory_status ==
"sufficient"`, and `coverage_percentage >= 99.9`. Anything else FAILS,
with the reason recorded in the summary's notes column.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

from placement_engine import engine
from placement_engine.cad_intake import build_project_input_dict
from placement_engine.cad_intake.dxf_reader import CADIntakeError
from placement_engine.cad_intake.inspection import (
    format_report_markdown,
    inspect_dxf,
)
from placement_engine.exporters.dxf_exporter import write_dxf
from placement_engine.exporters.markdown_report import write_report
from placement_engine.models import EngineOutput, LayoutOption, ProjectInput
from placement_engine.utils.test_inventory import SlabInventorySpec
from placement_engine.visualization.debug_plot import render_layout

# Coverage at or above this counts as "complete" for the pass rule.
COVERAGE_PASS_THRESHOLD = 99.9


@dataclass
class CaseResult:
    """One summary row: a single (DXF, strategy) outcome."""

    cad_file: str
    project_id: str
    strategy: str
    project_usable_area: float = 0.0
    installed_area: float = 0.0
    uncovered_area: float = 0.0
    coverage_percentage: float = 0.0
    layout_status: str = ""
    inventory_status: str = ""
    slabs_generated: int = 0
    slabs_used: int = 0
    waste_percentage: float = 0.0
    piece_count: int = 0
    seam_count: int = 0
    total_seam_length: float = 0.0
    review_marker_count: int = 0
    risk_flag_count: int = 0
    output_package_path: str = ""
    passed: bool = False
    notes: str = ""


@dataclass
class SuiteConfig:
    cad_dir: Path
    out_dir: Path
    strategies: list[str]
    slab_spec: SlabInventorySpec
    render_preview: bool = True


# ---------------------------------------------------------------------------
# Per-DXF processing
# ---------------------------------------------------------------------------


def _evaluate_pass(option: LayoutOption) -> tuple[bool, str]:
    """Apply the pass/fail rule to one layout option."""
    m = option.metrics
    reasons: list[str] = []
    if m.layout_status != "complete":
        reasons.append(f"layout_status={m.layout_status}")
    if m.inventory_status != "sufficient":
        reasons.append(f"inventory_status={m.inventory_status}")
    if m.coverage_percentage < COVERAGE_PASS_THRESHOLD:
        reasons.append(f"coverage={m.coverage_percentage:.2f}%")
    if reasons:
        return False, "FAIL: " + ", ".join(reasons)
    return True, "complete coverage, sufficient inventory"


def _process_one_dxf(cad_file: Path, cfg: SuiteConfig) -> list[CaseResult]:
    """Run the full pipeline for one DXF; return one row per strategy."""
    case_name = cad_file.stem
    case_dir = cfg.out_dir / case_name
    case_dir.mkdir(parents=True, exist_ok=True)
    project_id = f"cad_{case_name}"

    # 1. Inspection — always written, even when the file is broken.
    try:
        report = inspect_dxf(cad_file)
        (case_dir / "cad_inspection.md").write_text(
            format_report_markdown(report)
        )
    except CADIntakeError as exc:
        return [CaseResult(
            cad_file=cad_file.name, project_id=project_id, strategy="-",
            notes=f"FAIL: inspection error — {exc}",
        )]

    # 2. Convert DXF → engine input JSON (with auto-sized slab inventory).
    try:
        payload = build_project_input_dict(
            cad_file,
            project_id=project_id,
            test_slab_spec=cfg.slab_spec,
            options_requested=cfg.strategies,
        )
    except CADIntakeError as exc:
        return [CaseResult(
            cad_file=cad_file.name, project_id=project_id, strategy="-",
            notes=f"FAIL: intake error — {exc}",
        )]
    (case_dir / "input_generated.json").write_text(
        json.dumps(payload, indent=2)
    )
    slabs_generated = len(payload["slabs"])

    # 3. Validate the input + run the placement engine.
    try:
        project: ProjectInput = ProjectInput.model_validate(payload)
        output: EngineOutput = engine.run(project)
    except Exception as exc:  # noqa: BLE001 — suite must not crash on one bad case
        return [CaseResult(
            cad_file=cad_file.name, project_id=project_id, strategy="-",
            slabs_generated=slabs_generated,
            notes=f"FAIL: engine error — {type(exc).__name__}: {exc}",
        )]

    # 4. Per-strategy output package.
    rows: list[CaseResult] = []
    for index, option in enumerate(output.layout_options):
        strat_dir = case_dir / option.strategy
        strat_dir.mkdir(parents=True, exist_ok=True)

        # layout.json — trimmed to just this option.
        single = output.model_copy(update={"layout_options": [option]})
        (strat_dir / "layout.json").write_text(
            json.dumps(single.to_json_dict(), indent=2)
        )
        dxf_path = write_dxf(project, option, strat_dir / "layout.dxf")
        report_path = write_report(
            project, output, option, strat_dir / "layout_report.md"
        )
        if cfg.render_preview:
            render_layout(project, output, strat_dir / "preview.png",
                          option_index=index)

        m = option.metrics
        passed, note = _evaluate_pass(option)
        if not dxf_path.exists() or not report_path.exists():
            passed, note = False, "FAIL: output files missing"

        rows.append(CaseResult(
            cad_file=cad_file.name,
            project_id=project_id,
            strategy=option.strategy,
            project_usable_area=m.project_usable_area,
            installed_area=m.installed_area,
            uncovered_area=m.uncovered_area,
            coverage_percentage=m.coverage_percentage,
            layout_status=m.layout_status,
            inventory_status=m.inventory_status,
            slabs_generated=slabs_generated,
            slabs_used=m.slabs_used,
            waste_percentage=m.waste_percentage,
            piece_count=m.piece_count,
            seam_count=m.seam_count,
            total_seam_length=m.total_seam_length,
            review_marker_count=len(option.review_markers),
            risk_flag_count=sum(len(p.risk_flags) for p in option.placed_pieces),
            output_package_path=str(strat_dir),
            passed=passed,
            notes=note,
        ))
    return rows


# ---------------------------------------------------------------------------
# Summary report
# ---------------------------------------------------------------------------


def _write_summary(rows: list[CaseResult], out_dir: Path) -> Path:
    """Write `validation_summary.md` and return its path."""
    lines: list[str] = ["# DXF Validation Suite — Summary", ""]

    total = len(rows)
    passed = sum(1 for r in rows if r.passed)
    lines += [
        f"- cases run: **{total}** (one per DXF × strategy)",
        f"- passed: **{passed}**",
        f"- failed: **{total - passed}**",
        "",
    ]

    # Cross-strategy observation: where balanced fails but lowest_waste passes.
    by_file: dict[str, dict[str, CaseResult]] = {}
    for r in rows:
        by_file.setdefault(r.cad_file, {})[r.strategy] = r
    notable: list[str] = []
    for cad_file, strat_map in sorted(by_file.items()):
        bal = strat_map.get("balanced")
        low = strat_map.get("lowest_waste")
        if bal and low:
            if not bal.passed and low.passed:
                notable.append(
                    f"- `{cad_file}`: **balanced FAILS, lowest_waste PASSES** — "
                    f"balanced {bal.coverage_percentage:.1f}% vs "
                    f"lowest_waste {low.coverage_percentage:.1f}% coverage."
                )
            elif not bal.passed and not low.passed:
                notable.append(
                    f"- `{cad_file}`: **both strategies FAIL** — core issue, "
                    f"balanced {bal.coverage_percentage:.1f}%, "
                    f"lowest_waste {low.coverage_percentage:.1f}%."
                )
    if notable:
        lines += ["## Cross-strategy notes", ""] + notable + [""]

    # Compact pass/fail table.
    lines += [
        "## Results",
        "",
        "| CAD file | strategy | coverage % | layout_status | "
        "inventory_status | slabs gen/used | waste % | pieces | seams | "
        "pass |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        lines.append(
            f"| `{r.cad_file}` | {r.strategy} | "
            f"{r.coverage_percentage:.2f} | {r.layout_status or '-'} | "
            f"{r.inventory_status or '-'} | "
            f"{r.slabs_generated}/{r.slabs_used} | "
            f"{r.waste_percentage:.2f} | {r.piece_count} | {r.seam_count} | "
            f"{'✅ PASS' if r.passed else '❌ FAIL'} |"
        )
    lines.append("")

    # Per-case detail (all the columns the milestone spec lists).
    lines += ["## Per-case detail", ""]
    for r in rows:
        lines += [
            f"### `{r.cad_file}` — {r.strategy}",
            "",
            f"- project_id: `{r.project_id}`",
            f"- project_usable_area: {r.project_usable_area:.0f} mm²",
            f"- installed_area: {r.installed_area:.0f} mm²",
            f"- uncovered_area: {r.uncovered_area:.0f} mm²",
            f"- coverage_percentage: {r.coverage_percentage:.2f} %",
            f"- layout_status: `{r.layout_status or '-'}`",
            f"- inventory_status: `{r.inventory_status or '-'}`",
            f"- slabs_generated: {r.slabs_generated}",
            f"- slabs_used: {r.slabs_used}",
            f"- waste_percentage: {r.waste_percentage:.2f} %",
            f"- piece_count: {r.piece_count}",
            f"- seam_count: {r.seam_count}",
            f"- total_seam_length: {r.total_seam_length:.0f} mm",
            f"- review_marker_count: {r.review_marker_count}",
            f"- risk_flag_count: {r.risk_flag_count}",
            f"- output_package_path: `{r.output_package_path or '-'}`",
            f"- **status: {'PASS' if r.passed else 'FAIL'}**",
            f"- notes: {r.notes}",
            "",
        ]

    lines += [
        "## Notes",
        "",
        "- Slab inventories are **synthetic** test material sized by an "
        "area-based estimate, not the real company slab database.",
        "- The area estimate (`buffer_factor` default 1.25) does not "
        "account for whole-slab waste on thin rows. The row-based "
        "`balanced` strategy can therefore fall short of 100 % even "
        "with a nominally 'sufficient' inventory — see the cross-"
        "strategy notes above.",
        "",
    ]

    summary_path = out_dir / "validation_summary.md"
    summary_path.write_text("\n".join(lines))
    return summary_path


# ---------------------------------------------------------------------------
# Public entry point + CLI
# ---------------------------------------------------------------------------


def run_suite(cfg: SuiteConfig) -> tuple[list[CaseResult], Path]:
    """Run the suite and return (rows, summary_path)."""
    cfg.out_dir.mkdir(parents=True, exist_ok=True)
    cad_files = sorted(cfg.cad_dir.glob("*.dxf"))
    if not cad_files:
        raise SystemExit(f"No .dxf files found in {cfg.cad_dir}")

    all_rows: list[CaseResult] = []
    for cad_file in cad_files:
        all_rows.extend(_process_one_dxf(cad_file, cfg))

    summary_path = _write_summary(all_rows, cfg.out_dir)
    return all_rows, summary_path


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the full standardized-DXF pipeline over a folder."
    )
    parser.add_argument("--cad-dir", required=True, type=Path,
                        help="Folder of standardized .dxf files.")
    parser.add_argument("--out-dir", required=True, type=Path,
                        help="Folder for validation outputs.")
    parser.add_argument("--include-test-slabs", action="store_true",
                        default=True,
                        help="Attach a synthetic test slab inventory "
                             "(on by default for the suite).")
    parser.add_argument("--test-slab-count", default="auto",
                        help="'auto' (area-based) or an integer.")
    parser.add_argument("--test-slab-width", type=float, default=3200.0)
    parser.add_argument("--test-slab-height", type=float, default=1800.0)
    parser.add_argument("--test-slab-thickness", type=float, default=20.0)
    parser.add_argument("--slab-buffer-factor", type=float, default=1.25)
    parser.add_argument("--strategies", nargs="+",
                        default=["balanced", "lowest_waste"],
                        help="Strategies to run (default: balanced lowest_waste).")
    parser.add_argument("--no-preview", dest="preview",
                        action="store_false", default=True,
                        help="Skip preview PNG generation.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    count: int | str
    if args.test_slab_count == "auto":
        count = "auto"
    else:
        try:
            count = int(args.test_slab_count)
        except ValueError:
            print("--test-slab-count must be 'auto' or an integer",
                  file=sys.stderr)
            return 2

    cfg = SuiteConfig(
        cad_dir=args.cad_dir,
        out_dir=args.out_dir,
        strategies=list(args.strategies),
        slab_spec=SlabInventorySpec(
            count=count,
            width=args.test_slab_width,
            height=args.test_slab_height,
            thickness=args.test_slab_thickness,
            buffer_factor=args.slab_buffer_factor,
        ),
        render_preview=args.preview,
    )

    rows, summary_path = run_suite(cfg)
    passed = sum(1 for r in rows if r.passed)
    print(f"Ran {len(rows)} case(s); {passed} passed, {len(rows) - passed} failed.")
    print(f"Summary: {summary_path}")
    # Exit non-zero if any case failed, so the suite composes with CI.
    return 0 if passed == len(rows) else 1


if __name__ == "__main__":
    sys.exit(main())
