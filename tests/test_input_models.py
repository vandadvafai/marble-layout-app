"""Schema-level validation: bad inputs should fail loudly."""
import pytest
from pydantic import ValidationError

from placement_engine.models import ProjectInput


BASE_VALID = {
    "project_id": "p1",
    "layout": {
        "boundary": [[0, 0], [1000, 0], [1000, 500], [0, 500]],
    },
    "slabs": [
        {"slab_id": "S001", "width": 800, "height": 400, "thickness": 20}
    ],
}


def test_minimal_input_validates():
    parsed = ProjectInput.model_validate(BASE_VALID)
    assert parsed.project_id == "p1"
    assert parsed.units == "mm"
    assert parsed.rules.allowed_rotations == [0.0, 90.0]
    assert parsed.options_requested == ["balanced"]


def test_negative_slab_dimension_rejected():
    bad = {**BASE_VALID, "slabs": [
        {"slab_id": "S001", "width": -1, "height": 400, "thickness": 20}
    ]}
    with pytest.raises(ValidationError):
        ProjectInput.model_validate(bad)


def test_duplicate_slab_ids_rejected():
    bad = {**BASE_VALID, "slabs": [
        {"slab_id": "S001", "width": 800, "height": 400, "thickness": 20},
        {"slab_id": "S001", "width": 600, "height": 300, "thickness": 20},
    ]}
    with pytest.raises(ValidationError, match="unique"):
        ProjectInput.model_validate(bad)


def test_boundary_with_too_few_points_rejected():
    bad = {**BASE_VALID, "layout": {"boundary": [[0, 0], [1, 0]]}}
    with pytest.raises(ValidationError, match="at least 3 vertices"):
        ProjectInput.model_validate(bad)


def test_unsupported_rotation_rejected():
    bad = {**BASE_VALID, "rules": {"allowed_rotations": [45]}}
    with pytest.raises(ValidationError, match="not supported"):
        ProjectInput.model_validate(bad)


def test_empty_slab_inventory_rejected():
    bad = {**BASE_VALID, "slabs": []}
    with pytest.raises(ValidationError):
        ProjectInput.model_validate(bad)
