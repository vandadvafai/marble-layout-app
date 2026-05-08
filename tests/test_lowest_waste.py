"""`lowest_waste` strategy: offcut reuse from already-consumed slabs.

Two complementary scenarios:

  * **Corridor (insufficient material)** — the shipped fixture has six
    3 200 × 1 800 slabs against a 18 000 × 2 000 corridor. Total slab
    area (34.56 M mm²) is less than project area (36 M mm²), so 100 %
    is mathematically impossible. The strategy improves coverage from
    90 % (balanced) to 96 % by reusing slab S006's 1 200 × 1 800
    leftover, but the layout still ends `partial / insufficient`.

  * **Reachable corridor (sufficient material)** — a synthetic fixture
    where the math works out: 12 800 × 2 000 corridor with five
    3 200 × 1 800 slabs. Balanced reaches 92.5 %; lowest_waste reuses
    S005's leftover to hit 100 %.
"""
from pathlib import Path

import pytest
from shapely.geometry import Polygon

from placement_engine import engine
from placement_engine.geometry.validation import (
    assert_no_slab_local_overlaps,
    assert_pieces_within_slab_bounds,
)
from placement_engine.models import ProjectInput

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _balanced_and_lowest_waste(input_dict_or_path) -> dict:
    """Run both strategies via the engine and index the resulting options."""
    if isinstance(input_dict_or_path, Path):
        pi = engine.load_input_from_file(input_dict_or_path)
    else:
        pi = ProjectInput.model_validate(input_dict_or_path)
    pi.options_requested = ["balanced", "lowest_waste"]
    output = engine.run(pi)
    return {opt.strategy: opt for opt in output.layout_options}


# ---------------------------------------------------------------------------
# Shipped corridor: ~96 % coverage, 0 % slab waste, 9 offcut pieces from S006.
# ---------------------------------------------------------------------------


@pytest.fixture
def corridor_options():
    return _balanced_and_lowest_waste(
        EXAMPLES / "input_lowest_waste_corridor_offcut.json"
    )


def test_lowest_waste_beats_balanced_on_corridor(corridor_options):
    bal = corridor_options["balanced"].metrics
    low = corridor_options["lowest_waste"].metrics
    assert low.coverage_percentage > bal.coverage_percentage
    assert low.uncovered_area < bal.uncovered_area


def test_lowest_waste_creates_offcut_pieces(corridor_options):
    pieces = corridor_options["lowest_waste"].placed_pieces
    offcuts = [p for p in pieces if p.piece_role == "offcut"]
    mains = [p for p in pieces if p.piece_role == "main"]
    assert mains, "expected at least one main piece"
    assert offcuts, "expected at least one offcut piece"


def test_corridor_reuses_S006_leftover_into_multiple_pieces(corridor_options):
    """S006 is the single edge-clipped slab in the main pass; its
    1 200 × 1 800 leftover is the *only* offcut source. Lowest_waste
    must split it into multiple installed pieces."""
    pieces = corridor_options["lowest_waste"].placed_pieces
    by_slab: dict[str, list] = {}
    for p in pieces:
        by_slab.setdefault(p.slab_id, []).append(p)
    s006_pieces = by_slab["S006"]
    assert len(s006_pieces) >= 2, "expected S006 to contribute main + offcut pieces"
    assert any(p.piece_role == "main" for p in s006_pieces)
    assert any(p.piece_role == "offcut" for p in s006_pieces)


def test_corridor_zero_slab_waste_with_partial_coverage(corridor_options):
    """The flagship business outcome: every consumed slab is fully used
    (waste = 0 %) yet the project remains partial because the available
    material is below the project area."""
    m = corridor_options["lowest_waste"].metrics
    assert m.waste_percentage == pytest.approx(0.0, abs=0.01)
    # ~96% — math: 34.56M / 36M.
    assert 95.0 < m.coverage_percentage < 97.0
    assert m.layout_status == "partial"
    assert m.inventory_status == "insufficient"


def test_corridor_lowest_waste_has_more_seams_than_balanced(corridor_options):
    bal_seams = corridor_options["balanced"].metrics.seam_count
    low_seams = corridor_options["lowest_waste"].metrics.seam_count
    assert low_seams > bal_seams, (
        "more pieces from offcut reuse should create more visible seams"
    )


def test_seams_between_same_slab_pieces_are_emitted(corridor_options):
    """The user's spec: same-slab pieces that touch in the project must
    still produce seams; the engine must not suppress them."""
    option = corridor_options["lowest_waste"]
    seams = option.seams
    pieces_by_id = {p.piece_id: p for p in option.placed_pieces}
    same_slab_seams = [
        s for s in seams
        if pieces_by_id[s.piece_ids[0]].slab_id
        == pieces_by_id[s.piece_ids[1]].slab_id
    ]
    assert same_slab_seams, "expected at least one seam between two pieces of the same slab"


# ---------------------------------------------------------------------------
# Same-slab IDs and the piece_id naming scheme
# ---------------------------------------------------------------------------


def test_same_slab_pieces_share_slab_id_and_source_slab_id(corridor_options):
    pieces = corridor_options["lowest_waste"].placed_pieces
    for p in pieces:
        assert p.source_slab_id == p.slab_id, (
            f"piece {p.piece_id}: source_slab_id should equal slab_id"
        )


def test_piece_ids_follow_slab_indexed_naming(corridor_options):
    """Each piece's id should be `{slab_id}_{N}` and `piece_index_from_slab`
    should agree with `N`. Indices per slab are 1-based and contiguous."""
    pieces = corridor_options["lowest_waste"].placed_pieces
    seen_indices: dict[str, set[int]] = {}
    for p in pieces:
        assert p.piece_id == f"{p.slab_id}_{p.piece_index_from_slab}", (
            f"piece_id={p.piece_id!r} disagrees with "
            f"slab_id={p.slab_id!r} index={p.piece_index_from_slab}"
        )
        seen_indices.setdefault(p.slab_id, set()).add(p.piece_index_from_slab)
    for sid, indices in seen_indices.items():
        assert indices == set(range(1, max(indices) + 1)), (
            f"non-contiguous indices for {sid}: {sorted(indices)}"
        )


def test_piece_ids_are_unique(corridor_options):
    pieces = corridor_options["lowest_waste"].placed_pieces
    ids = [p.piece_id for p in pieces]
    assert len(ids) == len(set(ids)), "duplicate piece_id in output"


# ---------------------------------------------------------------------------
# Material validity — the engine's own validators must pass
# ---------------------------------------------------------------------------


def test_no_offcut_invents_material_outside_slab_bounds(corridor_options):
    """Run the explicit slab-bounds validator on the output. The engine
    already does this; we re-run it to make the contract explicit in
    the test suite."""
    pi = engine.load_input_from_file(EXAMPLES / "input_lowest_waste_corridor_offcut.json")
    pieces = corridor_options["lowest_waste"].placed_pieces
    assert_pieces_within_slab_bounds(pieces, pi.slabs)


def test_no_two_pieces_overlap_in_slab_local_coordinates(corridor_options):
    pieces = corridor_options["lowest_waste"].placed_pieces
    assert_no_slab_local_overlaps(pieces)


def test_pieces_dont_overlap_in_project_space(corridor_options):
    """Re-derives the project-space disjointness check from the engine
    so a regression here would surface in this strategy's tests too."""
    pieces = corridor_options["lowest_waste"].placed_pieces
    polys = [Polygon(p.project_polygon) for p in pieces]
    sum_area = sum(p.area for p in polys)
    union = polys[0]
    for p in polys[1:]:
        union = union.union(p)
    assert union.area == pytest.approx(sum_area, rel=1e-6)


# ---------------------------------------------------------------------------
# Reachable corridor: lowest_waste should hit 100 % when math allows
# ---------------------------------------------------------------------------


REACHABLE_CORRIDOR = {
    "project_id": "lowest_waste_reachable_demo",
    "layout": {
        # 12 800 × 2 000 = 25.6 M mm². Five 3 200 × 1 800 slabs total
        # 28.8 M mm² — enough to cover the project with offcut reuse.
        "boundary": [[0, 0], [12800, 0], [12800, 2000], [0, 2000]],
    },
    "slabs": [
        {"slab_id": "S001", "width": 3200, "height": 1800, "thickness": 20},
        {"slab_id": "S002", "width": 3200, "height": 1800, "thickness": 20},
        {"slab_id": "S003", "width": 3200, "height": 1800, "thickness": 20},
        {"slab_id": "S004", "width": 3200, "height": 1800, "thickness": 20},
        {"slab_id": "S005", "width": 3200, "height": 1800, "thickness": 20},
    ],
    "rules": {
        "min_piece_width": 100, "min_piece_height": 100, "min_piece_area": 10000,
    },
}


def test_lowest_waste_reaches_100_percent_when_material_allows():
    options = _balanced_and_lowest_waste(REACHABLE_CORRIDOR)
    bal = options["balanced"].metrics
    low = options["lowest_waste"].metrics

    # Balanced leaves a strip uncovered, lowest_waste should not.
    assert bal.coverage_percentage < 100.0
    assert low.coverage_percentage == pytest.approx(100.0, abs=0.5)
    assert low.layout_status == "complete"
    assert low.inventory_status == "sufficient"
    assert low.uncovered_area == pytest.approx(0.0, abs=1.0)

    # And: at least one slab must have produced both a main and an offcut.
    pieces = options["lowest_waste"].placed_pieces
    by_slab: dict[str, set[str]] = {}
    for p in pieces:
        by_slab.setdefault(p.slab_id, set()).add(p.piece_role)
    assert any(roles == {"main", "offcut"} for roles in by_slab.values()), (
        "expected some slab to yield both a main piece and an offcut piece"
    )


# ---------------------------------------------------------------------------
# Insufficient inventory remains honest
# ---------------------------------------------------------------------------


def test_insufficient_inventory_still_reported_partial():
    """Replay the existing insufficient-slabs fixture under lowest_waste:
    even with offcut reuse, two 3 200 × 1 800 slabs can't cover a
    12 000 × 8 000 floor. Status fields must stay honest."""
    fixture = {
        "project_id": "low_waste_insufficient_demo",
        "layout": {"boundary": [[0, 0], [12000, 0], [12000, 8000], [0, 8000]]},
        "slabs": [
            {"slab_id": "S001", "width": 3200, "height": 1800, "thickness": 20},
            {"slab_id": "S002", "width": 3200, "height": 1800, "thickness": 20},
        ],
        "rules": {"min_piece_width": 0, "min_piece_height": 0, "min_piece_area": 0},
        "options_requested": ["lowest_waste"],
    }
    pi = ProjectInput.model_validate(fixture)
    output = engine.run(pi)
    m = output.layout_options[0].metrics
    assert m.layout_status == "partial"
    assert m.inventory_status == "insufficient"
    types = {mk.type for mk in output.layout_options[0].review_markers}
    assert "incomplete_coverage" in types
    assert "insufficient_inventory" in types


# ---------------------------------------------------------------------------
# Backward compatibility — balanced strategy unchanged
# ---------------------------------------------------------------------------


def test_balanced_still_uses_P_prefixed_piece_ids():
    """The schema additions (source_slab_id, piece_index_from_slab,
    piece_role) must not change balanced's piece_id naming."""
    pi = engine.load_input_from_file(EXAMPLES / "input_floor_simple.json")
    pi.options_requested = ["balanced"]
    output = engine.run(pi)
    for p in output.layout_options[0].placed_pieces:
        assert p.piece_id.startswith("P"), (
            f"balanced piece_id should start with 'P', got {p.piece_id!r}"
        )
        assert p.piece_role == "main"
        assert p.source_slab_id == p.slab_id
