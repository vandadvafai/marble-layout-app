"""FastAPI app entry point.

Wires up CORS for the local Vite dev server (which runs on
``http://localhost:5173`` by default) and mounts the route module.
A frozen production build would tighten the CORS allowlist, but
the foundation milestone targets local dev only.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from placement_engine.api.routes import router
from placement_engine.config import ENGINE_VERSION

log = logging.getLogger(__name__)

app = FastAPI(
    title="Stonelayout placement engine API",
    version=ENGINE_VERSION,
    description=(
        "HTTP surface for the interactive designer editor. "
        "Foundation milestone: read-only demo-layout endpoints."
    ),
)

# Local-dev CORS. Vite's default is :5173; Next.js dev is :3000;
# both are allowed so the same backend serves whichever frontend
# the team is iterating on.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health() -> dict:
    """Cheap liveness probe — also lets the frontend display the
    engine version it's talking to."""
    return {"status": "ok", "engine_version": ENGINE_VERSION}


app.include_router(router)
