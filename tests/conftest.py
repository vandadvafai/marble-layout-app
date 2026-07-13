"""Global test fixtures.

* Make the project root importable so ``import placement_engine`` works.
* Point the API test suite at the bundled sample inventory via
  ``AVANDAD_INVENTORY_PATH`` so tests aren't sensitive to whether
  ``outputs/slab_ingestion_test/clean_slabs.json`` exists (which it
  does not on a fresh clone). Tests that want to exercise the
  empty-inventory portability path deliberately clear the env
  variable — see ``test_portability.py`` for examples.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Default to the bundled sample inventory. Individual tests override
# with ``monkeypatch.setenv`` / ``monkeypatch.delenv`` when they need
# to simulate a clean environment.
_SAMPLE_INVENTORY = ROOT / "examples" / "demo" / "clean_slabs.json"
os.environ.setdefault("AVANDAD_INVENTORY_PATH", str(_SAMPLE_INVENTORY))
