"""Light smoke test for the Streamlit UI.

This is intentionally shallow — it confirms the app script runs end to
end without an exception and renders its title. The real logic lives
in `placement_engine.ui.app_helpers` and is covered by
`test_ui_helpers.py`. No brittle browser-level testing.
"""
from pathlib import Path

import pytest

APP_PATH = Path(__file__).resolve().parents[1] / "streamlit_app.py"

# Streamlit's headless app-testing harness arrived in 1.28; skip cleanly
# if an older Streamlit (or none) is installed.
AppTest = None
try:  # pragma: no cover - import guard
    from streamlit.testing.v1 import AppTest as _AppTest
    AppTest = _AppTest
except Exception:  # noqa: BLE001
    AppTest = None


@pytest.mark.skipif(AppTest is None,
                    reason="streamlit.testing.v1.AppTest not available")
def test_streamlit_app_initializes_without_error():
    app = AppTest.from_file(str(APP_PATH))
    app.run()
    assert not app.exception, f"app raised: {app.exception}"


@pytest.mark.skipif(AppTest is None,
                    reason="streamlit.testing.v1.AppTest not available")
def test_streamlit_app_renders_title_and_inputs():
    app = AppTest.from_file(str(APP_PATH))
    app.run()
    # Title present.
    titles = [t.value for t in app.title]
    assert any("Marble Layout" in t for t in titles)
    # The core inputs render: a Generate button exists.
    button_labels = [b.label for b in app.button]
    assert any("Generate Layout Package" in lbl for lbl in button_labels)
