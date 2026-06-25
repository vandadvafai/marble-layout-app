"""HTTP API surface for the placement engine.

This package wraps the existing layout / inventory / architectural
modules with a FastAPI server so the interactive designer UI
(frontend/) can read demo plans and layouts without duplicating
Python logic in JavaScript.

The API is read-only at this foundation milestone — no editing or
mutation endpoints yet. Subsequent milestones will add layout-edit
ingestion, validation, and export endpoints.

Run locally:

    uvicorn placement_engine.api.main:app --reload --port 8000

or via the convenience script:

    python scripts/run_api_server.py
"""
