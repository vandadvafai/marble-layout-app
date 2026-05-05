"""Write `EngineOutput` to disk as JSON."""

from __future__ import annotations

import json
from pathlib import Path

from placement_engine.models import EngineOutput


def write_output(output: EngineOutput, path: str | Path) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(output.to_json_dict(), indent=2))
    return target
