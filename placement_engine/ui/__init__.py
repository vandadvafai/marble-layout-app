"""Helpers shared by the package CLIs and the local Streamlit UI.

`app_helpers` holds the single orchestration entry point
(`generate_layout_package`) that both `make_package.py` and
`streamlit_app.py` call — so the UI never re-implements CAD intake,
engine execution, or package export.
"""

from placement_engine.ui.app_helpers import (
    PackageResult,
    build_package_zip,
    generate_layout_package,
    headline_metrics,
    split_review_markers,
)

__all__ = [
    "PackageResult",
    "build_package_zip",
    "generate_layout_package",
    "headline_metrics",
    "split_review_markers",
]
