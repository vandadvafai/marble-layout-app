"""Local Streamlit UI for the marble layout engine — internal MVP.

Run with:

    streamlit run streamlit_app.py

This is a thin front end over `generate_layout_package` — the same
orchestration the `make_package.py` CLI uses. It accepts a
standardized DXF, runs both strategies with a synthetic slab
inventory, and lets the designer preview and download the results.

Not for deployment. No authentication. DXF input only.
"""

from __future__ import annotations

import re
from pathlib import Path

import streamlit as st

from placement_engine.cad_conversion import CADConversionError
from placement_engine.cad_intake.dxf_reader import CADIntakeError
from placement_engine.ui.app_helpers import (
    build_package_zip,
    generate_layout_package,
    headline_metrics,
    split_review_markers,
)

# All UI runs write here; only the latest run is kept.
UI_RUN_DIR = Path("outputs/ui_runs/latest")
PROJECT_TYPES = ["floor", "wall", "fireplace", "countertop"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sanitise_project_id(filename: str) -> str:
    """Derive a safe default project id from an uploaded filename."""
    stem = Path(filename).stem
    cleaned = re.sub(r"[^A-Za-z0-9_]+", "_", stem).strip("_").lower()
    return cleaned or "ui_project"


def _friendly_error(exc: Exception) -> str:
    """Map a pipeline exception to a designer-facing message."""
    text = str(exc)
    if isinstance(exc, CADIntakeError):
        if "AI_PROJECT_BOUNDARY" in text:
            return (
                "Input DXF is not ready for the engine: AI_PROJECT_BOUNDARY "
                "was not found or is not a single closed polyline. Please "
                "correct the drawing in Rhino/AutoCAD and try again."
            )
        if "Unsupported entity" in text:
            return (
                "Unsupported geometry exists on an engine layer. Please "
                "convert it to closed polylines before uploading.\n\n"
                f"Detail: {text}"
            )
        if "hole" in text.lower():
            return (
                "One or more cutouts are invalid or outside the project "
                "boundary. Please review AI_HOLES_CUTOUTS.\n\n"
                f"Detail: {text}"
            )
        return f"CAD intake error: {text}"
    if isinstance(exc, CADConversionError):
        return f"CAD conversion error: {text}"
    return f"Unexpected processing error: {text}"


def _status_banner(layout_status: str, inventory_status: str) -> None:
    """Render a coloured banner for a strategy's status."""
    if layout_status == "complete" and inventory_status == "sufficient":
        st.success("Complete layout — full project coverage with sufficient inventory.")
    elif layout_status == "failed":
        st.error("Layout failed — no pieces were placed.")
    else:
        bits = []
        if layout_status != "complete":
            bits.append(f"layout is **{layout_status}**")
        if inventory_status != "sufficient":
            bits.append(f"inventory is **{inventory_status}**")
        st.warning("Needs review — " + ", ".join(bits) + ".")


def _render_strategy(result, strategy: str) -> None:
    """Render one strategy's results inside the current tab/column."""
    option = result.option(strategy)
    files = result.per_strategy_files[strategy]
    metrics = headline_metrics(option)

    _status_banner(metrics["layout_status"], metrics["inventory_status"])

    # Preview image.
    if "preview" in files and files["preview"].is_file():
        st.image(str(files["preview"]), caption=f"{strategy} layout preview",
                 use_container_width=True)

    # Headline metrics.
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Coverage", f"{metrics['coverage_percentage']}%")
    c2.metric("Slab waste", f"{metrics['waste_percentage']}%")
    c3.metric("Pieces", metrics["piece_count"])
    c4.metric("Slabs used", metrics["slabs_used"])
    c5, c6, c7, c8 = st.columns(4)
    c5.metric("Layout status", metrics["layout_status"])
    c6.metric("Inventory", metrics["inventory_status"])
    c7.metric("Seams", metrics["seam_count"])
    c8.metric("Total seam length", f"{metrics['total_seam_length']:.0f} mm")
    st.caption(
        "Coverage measures project completion (installed ÷ usable area). "
        "Waste measures unused material from the slabs consumed — the two "
        "are different numbers."
    )

    # Warnings / review notes.
    primary, technical = split_review_markers(option)
    if primary:
        st.markdown("**Review notes**")
        for m in primary:
            st.warning(f"`{m.type}` ({m.severity}) — {m.message}")
    risk_pieces = [p for p in option.placed_pieces if p.risk_flags]
    if risk_pieces:
        st.markdown("**Piece risk flags**")
        for p in risk_pieces:
            flags = ", ".join(f"{f.type} ({f.severity})" for f in p.risk_flags)
            st.write(f"- `{p.piece_id}`: {flags}")
    if not primary and not risk_pieces:
        st.info("No review warnings or piece risk flags raised.")
    if technical:
        with st.expander("Technical notes"):
            for m in technical:
                st.write(f"- `{m.type}`: {m.message}")

    # Full report, collapsed.
    with st.expander("View full designer report"):
        st.markdown(files["report"].read_text())

    # Downloads.
    st.markdown("**Downloads**")
    d1, d2, d3, d4 = st.columns(4)
    d1.download_button("Editable DXF", files["dxf"].read_bytes(),
                       file_name=f"{strategy}_layout.dxf",
                       key=f"dl_dxf_{strategy}")
    d2.download_button("Report (.md)", files["report"].read_text(),
                       file_name=f"{strategy}_layout_report.md",
                       key=f"dl_md_{strategy}")
    d3.download_button("Layout JSON", files["json"].read_text(),
                       file_name=f"{strategy}_layout.json",
                       key=f"dl_json_{strategy}")
    if "preview" in files and files["preview"].is_file():
        d4.download_button("Preview PNG", files["preview"].read_bytes(),
                           file_name=f"{strategy}_preview.png",
                           key=f"dl_png_{strategy}")


# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------


st.set_page_config(page_title="AI-Assisted Marble Layout — Internal MVP",
                   layout="wide")
st.title("AI-Assisted Marble Layout — Internal MVP")
st.write(
    "This prototype accepts standardized DXF floor plans and generates "
    "editable CAD layout drafts for Rhino/AutoCAD review. Current slab "
    "inventory is synthetic test data."
)

# --- Input panel -----------------------------------------------------------

st.header("1 · Input")

uploaded = st.file_uploader("Upload standardized DXF plan", type=["dxf"])
st.caption(
    "Required input standard:\n"
    "- exactly one closed polyline on **AI_PROJECT_BOUNDARY**\n"
    "- optional closed polylines on **AI_HOLES_CUTOUTS**\n"
    "- units: **millimetres**\n"
    "- unsupported geometry (arcs, splines, blocks) should be cleaned in "
    "Rhino/AutoCAD first"
)

default_project_id = _sanitise_project_id(uploaded.name) if uploaded else ""
project_id = st.text_input("Project ID", value=default_project_id,
                           placeholder="e.g. demo_floor_001")
project_type = st.selectbox("Project type", PROJECT_TYPES, index=0)
st.caption("The current tested workflow is primarily for floor surfaces.")

st.subheader("Slab inventory")
st.info(
    "Using synthetic test slab inventory for validation. Real Avandad slab "
    "database integration will be added later."
)
with st.expander("Synthetic slab settings (optional)"):
    slab_width = st.number_input("Slab width (mm)", value=3200.0, min_value=1.0)
    slab_height = st.number_input("Slab height (mm)", value=1800.0, min_value=1.0)
    slab_thickness = st.number_input("Slab thickness (mm)", value=20.0, min_value=1.0)
    count_mode = st.radio("Slab count", ["auto", "manual"], horizontal=True)
    manual_count = st.number_input("Number of slabs", value=20, min_value=1, step=1)
    buffer_factor = st.number_input("Buffer factor (auto mode)", value=1.25,
                                    min_value=1.0, step=0.05)

st.subheader("Strategies")
st.write("Both strategies are always run: **Balanced** and **Lowest Waste**.")

generate = st.button("Generate Layout Package", type="primary")

# --- Run -------------------------------------------------------------------

if generate:
    if uploaded is None:
        st.error("Please upload a standardized DXF file first.")
        st.stop()
    if not project_id.strip():
        st.error("Please enter a Project ID.")
        st.stop()

    # Persist the upload under the run folder's input/ subfolder.
    input_dir = UI_RUN_DIR / "input"
    input_dir.mkdir(parents=True, exist_ok=True)
    uploaded_dxf = input_dir / "uploaded_plan.dxf"
    uploaded_dxf.write_bytes(uploaded.getvalue())

    test_slab_count: str | int = (
        "auto" if count_mode == "auto" else int(manual_count)
    )

    try:
        with st.status("Generating layout package…", expanded=True) as status:
            st.write("Reading CAD input…")
            st.write("Generating synthetic slab inventory…")
            st.write("Running balanced and lowest-waste strategies…")
            result = generate_layout_package(
                uploaded_dxf,
                project_id=project_id.strip(),
                output_dir=UI_RUN_DIR,
                project_type=project_type,
                strategies=["balanced", "lowest_waste"],
                include_test_slabs=True,
                test_slab_count=test_slab_count,
                test_slab_width=slab_width,
                test_slab_height=slab_height,
                test_slab_thickness=slab_thickness,
                slab_buffer_factor=buffer_factor,
                generate_preview=True,
                clean_output=True,
            )
            st.write("Exporting CAD layout files and reports…")
            # clean_output wiped the run folder — re-persist the upload so it
            # is part of the downloadable package.
            input_dir.mkdir(parents=True, exist_ok=True)
            uploaded_dxf.write_bytes(uploaded.getvalue())
            zip_bytes = build_package_zip(
                UI_RUN_DIR, zip_path=UI_RUN_DIR / "layout_package.zip"
            )
            status.update(label="Package complete.", state="complete")
    except (CADIntakeError, CADConversionError) as exc:
        st.error(_friendly_error(exc))
        st.stop()
    except Exception as exc:  # noqa: BLE001 — UI must not crash silently
        st.error("Unexpected processing error. See technical detail below.")
        with st.expander("Technical detail"):
            st.exception(exc)
        st.stop()

    st.session_state["result"] = result
    st.session_state["zip_bytes"] = zip_bytes

# --- Results ---------------------------------------------------------------

result = st.session_state.get("result")
if result is not None:
    zip_bytes = st.session_state.get("zip_bytes")

    st.header("2 · Results")
    holes = result.payload["layout"]["holes"]
    usable_area = result.engine_output.layout_options[0].metrics.project_usable_area
    boundary_ok = result.inspection.boundary_polyline_count == 1

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Project ID", result.project_id)
    c2.metric("CAD intake", "boundary OK" if boundary_ok else "check boundary")
    c3.metric("Holes found", len(holes))
    c4.metric("Project usable area", f"{usable_area:.0f} mm²")

    if zip_bytes is not None:
        st.download_button(
            "Download complete package (.zip)", zip_bytes,
            file_name=f"{result.project_id}_layout_package.zip",
            mime="application/zip", type="primary", key="dl_zip",
        )

    tab_balanced, tab_lowest = st.tabs(["Balanced", "Lowest Waste"])
    with tab_balanced:
        _render_strategy(result, "balanced")
    with tab_lowest:
        _render_strategy(result, "lowest_waste")

    st.caption(
        "Outputs are AI-generated first drafts for designer review in "
        "Rhino/AutoCAD — not final factory files."
    )
