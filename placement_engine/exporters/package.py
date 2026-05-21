"""Bundle a layout option into a hand-off package.

Per layout option the package contains:
  - layout_<strategy>.json      — the raw engine output trimmed to this option
  - layout_<strategy>.dxf       — clean editable geometry for Rhino/AutoCAD
  - layout_<strategy>_report.md — verbose Markdown report
  - layout_<strategy>_preview.png (optional) — matplotlib debug plot

When a single layout option is exported the strategy suffix is still
used so the filenames are unambiguous if the package is opened
alongside another option from the same project.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Sequence

from placement_engine.exporters.dxf_exporter import write_dxf
from placement_engine.exporters.markdown_report import write_report
from placement_engine.models import EngineOutput, LayoutOption, ProjectInput


def _slug(name: str) -> str:
    """Filesystem-safe lower-case slug; deterministic."""
    return "".join(c if c.isalnum() else "_" for c in name).strip("_").lower()


def _trim_output_to_one_option(
    output: EngineOutput, option: LayoutOption
) -> EngineOutput:
    """Return a copy of `output` containing only the given layout option.

    Useful for the per-option JSON file in the package: the designer
    sees exactly what produced the DXF and report sitting next to it,
    without having to scroll past unrelated options.
    """
    return output.model_copy(update={"layout_options": [option]})


def write_package(
    project_input: ProjectInput,
    output: EngineOutput,
    target_dir: str | Path,
    options: Sequence[LayoutOption] | None = None,
    render_preview: bool = True,
) -> dict[str, list[Path]]:
    """Write a per-option DXF + report + JSON bundle into `target_dir`.

    `options` defaults to *every* option in the engine output. Pass a
    subset to export only specific strategies. Returns a dict mapping
    strategy name to the list of files written for that option.
    """
    target_path = Path(target_dir)
    target_path.mkdir(parents=True, exist_ok=True)

    chosen = list(options) if options is not None else list(output.layout_options)
    if not chosen:
        raise ValueError("no layout options to export")

    written: dict[str, list[Path]] = {}
    for option in chosen:
        slug = _slug(option.strategy)
        json_path = target_path / f"layout_{slug}.json"
        dxf_path = target_path / f"layout_{slug}.dxf"
        report_path = target_path / f"layout_{slug}_report.md"

        # Per-option JSON: just this option, so the trio (json, dxf, md)
        # is internally consistent.
        single = _trim_output_to_one_option(output, option)
        json_path.write_text(json.dumps(single.to_json_dict(), indent=2))

        write_dxf(project_input, option, dxf_path)
        write_report(project_input, output, option, report_path)

        files = [json_path, dxf_path, report_path]

        if render_preview:
            # Lazy import keeps matplotlib out of the dependency path
            # for users that don't need the preview.
            from placement_engine.visualization.debug_plot import render_layout
            preview_path = target_path / f"layout_{slug}_preview.png"
            option_index = output.layout_options.index(option)
            render_layout(project_input, output, preview_path, option_index=option_index)
            files.append(preview_path)

        written[option.strategy] = files
    return written
