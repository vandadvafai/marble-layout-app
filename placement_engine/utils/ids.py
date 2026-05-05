"""Deterministic ID generation.

Engine output IDs (P001, OPT_001, ...) are sequence-based rather than UUIDs
so the same input + seed always produces the same JSON. This matters for
diffing layouts and for designer-facing references.
"""

from __future__ import annotations


class IdSequence:
    def __init__(self, prefix: str, width: int = 3, start: int = 1) -> None:
        self._prefix = prefix
        self._width = width
        self._next = start

    def next(self) -> str:
        out = f"{self._prefix}{self._next:0{self._width}d}"
        self._next += 1
        return out
