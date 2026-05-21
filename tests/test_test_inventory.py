"""Synthetic test slab inventory generator."""
import pytest

from placement_engine.cad_intake import build_project_input_dict
from placement_engine.models import ProjectInput
from placement_engine.utils.test_inventory import (
    DEFAULT_BUFFER_FACTOR,
    SlabInventorySpec,
    estimate_slab_count,
    generate_test_slabs,
)

EXAMPLES_CAD = __import__("pathlib").Path(
    __file__
).resolve().parents[1] / "examples" / "cad_inputs"


# ---------------------------------------------------------------------------
# generate_test_slabs
# ---------------------------------------------------------------------------


def test_generates_requested_count():
    slabs = generate_test_slabs(12)
    assert len(slabs) == 12


def test_slab_ids_are_unique_and_sequential():
    slabs = generate_test_slabs(5)
    ids = [s["slab_id"] for s in slabs]
    assert ids == ["S001", "S002", "S003", "S004", "S005"]
    assert len(set(ids)) == 5


def test_default_dimensions():
    slab = generate_test_slabs(1)[0]
    assert slab["width"] == 3200.0
    assert slab["height"] == 1800.0
    assert slab["thickness"] == 20.0
    assert slab["image_path"] == "images/test_slabs/S001.png"


def test_custom_dimensions_applied():
    slabs = generate_test_slabs(3, width=2000, height=1000, thickness=30)
    for s in slabs:
        assert s["width"] == 2000
        assert s["height"] == 1000
        assert s["thickness"] == 30


def test_zero_count_rejected():
    with pytest.raises(ValueError, match="count must be"):
        generate_test_slabs(0)


def test_negative_dimensions_rejected():
    with pytest.raises(ValueError, match="dimensions must be positive"):
        generate_test_slabs(1, width=-1)


# ---------------------------------------------------------------------------
# estimate_slab_count
# ---------------------------------------------------------------------------


def test_estimate_uses_buffer_factor_and_ceils():
    """Milestone-spec example: 80 M mm² project, 5.76 M mm² slab,
    1.25 buffer → 13.89 × 1.25 = 17.36 → ceil = 18."""
    n = estimate_slab_count(80_000_000, 5_760_000, buffer_factor=1.25)
    assert n == 18


def test_estimate_default_buffer_is_1_25():
    assert DEFAULT_BUFFER_FACTOR == 1.25
    n_default = estimate_slab_count(80_000_000, 5_760_000)
    n_explicit = estimate_slab_count(80_000_000, 5_760_000, 1.25)
    assert n_default == n_explicit == 18


def test_estimate_floors_at_one():
    """A project much smaller than a slab still needs at least 1 slab."""
    n = estimate_slab_count(100_000, 5_760_000)
    assert n == 1


def test_estimate_rejects_non_positive_area():
    with pytest.raises(ValueError):
        estimate_slab_count(0, 5_760_000)
    with pytest.raises(ValueError):
        estimate_slab_count(5_000_000, 0)


def test_estimate_rejects_buffer_below_one():
    with pytest.raises(ValueError, match="buffer_factor"):
        estimate_slab_count(5_000_000, 5_760_000, buffer_factor=0.9)


# ---------------------------------------------------------------------------
# SlabInventorySpec
# ---------------------------------------------------------------------------


def test_spec_auto_resolves_count_from_area():
    spec = SlabInventorySpec(count="auto")
    # 80 M project against the default 5.76 M slab → 18.
    slabs = spec.resolve(80_000_000)
    assert len(slabs) == 18


def test_spec_explicit_count_ignores_area():
    spec = SlabInventorySpec(count=4)
    slabs = spec.resolve(80_000_000)
    assert len(slabs) == 4


def test_spec_explicit_count_below_one_rejected():
    spec = SlabInventorySpec(count=0)
    with pytest.raises(ValueError):
        spec.resolve(10_000_000)


# ---------------------------------------------------------------------------
# Integration with the CAD intake
# ---------------------------------------------------------------------------


def test_cad_to_input_with_auto_slabs_validates_via_pydantic():
    """A DXF + auto-sized inventory must build a valid ProjectInput."""
    payload = build_project_input_dict(
        EXAMPLES_CAD / "demo" / "demo_long_corridor.dxf",
        project_id="cad_corridor_auto",
        test_slab_spec=SlabInventorySpec(count="auto"),
    )
    project = ProjectInput.model_validate(payload)
    # Corridor is 18 m × 2 m = 36 M mm²; auto → ceil(36/5.76 × 1.25) = 8.
    assert len(project.slabs) == 8


def test_cad_to_input_auto_count_scales_with_project_size():
    """A bigger floor must get more auto slabs than a smaller one."""
    small = build_project_input_dict(
        EXAMPLES_CAD / "demo" / "demo_rectangle_floor.dxf",
        project_id="cad_small",
        test_slab_spec=SlabInventorySpec(count="auto"),
    )
    big = build_project_input_dict(
        EXAMPLES_CAD / "demo" / "demo_irregular_apartment_floor.dxf",
        project_id="cad_big",
        test_slab_spec=SlabInventorySpec(count="auto"),
    )
    assert len(big["slabs"]) > len(small["slabs"])


def test_cad_to_input_explicit_count():
    payload = build_project_input_dict(
        EXAMPLES_CAD / "demo" / "demo_rectangle_floor.dxf",
        project_id="cad_explicit",
        test_slab_spec=SlabInventorySpec(count=20),
    )
    assert len(payload["slabs"]) == 20


def test_legacy_include_test_slabs_still_gives_six():
    """The pre-existing `include_test_slabs=True` path must be unchanged."""
    payload = build_project_input_dict(
        EXAMPLES_CAD / "demo" / "demo_rectangle_floor.dxf",
        project_id="cad_legacy",
        include_test_slabs=True,
    )
    assert len(payload["slabs"]) == 6
