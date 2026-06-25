#!/usr/bin/env python3
"""Convenience launcher for the placement engine HTTP API.

    python scripts/run_api_server.py            # default port 8000
    python scripts/run_api_server.py --port 8080
    python scripts/run_api_server.py --no-reload

Identical to ``uvicorn placement_engine.api.main:app --reload`` —
exposed as a script so the run instructions in frontend/README.md
have one canonical form to point at.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def main(argv: list[str] | None = None) -> int:
    import uvicorn

    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    # Default to explicit IPv4 (127.0.0.1) so the loopback story is
    # unambiguous. ``--host localhost`` is risky on macOS + Node 18+:
    # uvicorn binds whichever family ``localhost`` resolves to first
    # on the server side, while Node's proxy hop resolves it
    # independently — the two ends can land on different families and
    # Node will hit ECONNREFUSED ::1:8000 while curl still works.
    # Use ``--host 0.0.0.0`` if you need to reach the server from
    # another host (binds all IPv4 interfaces).
    p.add_argument("--host", default="127.0.0.1",
                   help="Bind address (default 127.0.0.1 — IPv4 loopback).")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--no-reload", action="store_true",
                   help="Disable auto-reload (use in CI / production-like).")
    args = p.parse_args(argv)

    print(
        f"Starting placement-engine API on http://{args.host}:{args.port} "
        f"(reload={not args.no_reload})",
        flush=True,
    )
    uvicorn.run(
        "placement_engine.api.main:app",
        host=args.host, port=args.port,
        reload=not args.no_reload,
        log_level="info",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
