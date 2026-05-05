"""Pydantic models for placement-engine input and output.

These schemas are the contract between the engine and any future caller
(Blender add-on, web UI, AI assistant). Optional fields are included now so
later features can populate them without a breaking schema change. Anything
the MVP does not yet act on is still accepted and round-tripped.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# A 2D point is just a tuple of two floats in millimetres.
Point = tuple[float, float]
# A polygon is a list of points; the ring is implicitly closed.
PolygonCoords = list[Point]


# ---------------------------------------------------------------------------
# Input models
# ---------------------------------------------------------------------------


class SourceFile(BaseModel):
    """Pointer to whatever the layout was originally extracted from.

    For MVP only `manual_json` is meaningful; DXF/SVG/PDF/image importers
    will plug in later without changing this shape.
    """

    type: Literal["manual_json", "dxf", "svg", "pdf", "image", "cad_export"] = (
        "manual_json"
    )
    path: str | None = None
    notes: str | None = None


class Zone(BaseModel):
    """A sub-region of the project that designers care about visually."""

    zone_id: str
    name: str | None = None
    polygon: PolygonCoords
    visibility: Literal["low", "medium", "high"] = "medium"
    notes: str | None = None


class Layout(BaseModel):
    source_file: SourceFile | None = None
    boundary: PolygonCoords
    holes: list[PolygonCoords] = Field(default_factory=list)
    zones: list[Zone] = Field(default_factory=list)

    @field_validator("boundary")
    @classmethod
    def _boundary_min_vertices(cls, v: PolygonCoords) -> PolygonCoords:
        if len(v) < 3:
            raise ValueError("boundary must have at least 3 vertices")
        return v

    @field_validator("holes")
    @classmethod
    def _holes_min_vertices(cls, v: list[PolygonCoords]) -> list[PolygonCoords]:
        for i, hole in enumerate(v):
            if len(hole) < 3:
                raise ValueError(f"hole[{i}] must have at least 3 vertices")
        return v


class ImageMetadata(BaseModel):
    """Stored as-is in MVP. Future image-analysis work will read this."""

    model_config = ConfigDict(extra="allow")

    original_filename: str | None = None
    dpi: float | None = None
    notes: str | None = None


class Defect(BaseModel):
    defect_id: str
    type: str
    polygon: PolygonCoords
    severity: Literal["low", "medium", "high"] = "medium"
    notes: str | None = None


class Slab(BaseModel):
    slab_id: str
    width: float = Field(gt=0, description="Slab width in mm")
    height: float = Field(gt=0, description="Slab height in mm")
    thickness: float = Field(gt=0, description="Slab thickness in mm")
    image_path: str | None = None
    image_metadata: ImageMetadata | None = None
    vein_direction: Literal["horizontal", "vertical", "diagonal", "none"] | None = None
    design_notes: str | None = None
    defects: list[Defect] = Field(default_factory=list)


class DesignRequirements(BaseModel):
    """Free-form designer intent. Most fields are advisory in MVP."""

    model_config = ConfigDict(extra="allow")

    general_notes: str | None = None
    preferred_visual_style: str | None = None
    preferred_vein_direction: str | None = None
    priority: Literal["balanced", "lowest_waste", "best_visual", "pattern_match"] = (
        "balanced"
    )
    avoid_high_visibility_seams: bool = False
    avoid_defects: bool = True


class RiskThresholds(BaseModel):
    """Soft warning thresholds for the risk evaluator.

    These are independent of `Rules.min_piece_*`. Those drop pieces from
    the layout entirely; these only attach a `RiskFlag` to pieces that
    survived the hard filter but may still be uncomfortable to fabricate.

    Defaults are conservative: a 150 mm × 150 mm rectangular piece of
    50 000 mm² (≈ 7 cm²) sits comfortably; anything smaller, narrower,
    shorter, more elongated, or noticeably non-rectangular gets flagged.
    """

    min_piece_width: float = Field(default=150.0, ge=0)
    min_piece_height: float = Field(default=150.0, ge=0)
    min_piece_area: float = Field(default=50_000.0, ge=0)
    max_aspect_ratio: float = Field(default=8.0, ge=1.0)
    max_vertex_count: int = Field(default=6, ge=4)


class Rules(BaseModel):
    allowed_rotations: list[float] = Field(default_factory=lambda: [0.0, 90.0])
    # Hard-drop filters: pieces below any of these are removed from the
    # layout entirely by the placement strategy. To disable a filter, set
    # the value to 0.
    min_piece_width: float = Field(default=0.0, ge=0)
    min_piece_height: float = Field(default=0.0, ge=0)
    min_piece_area: float = Field(default=0.0, ge=0)
    seam_tolerance: float = Field(default=2.0, ge=0)
    allow_partial_slab_use: bool = True
    allow_piece_reuse_from_offcuts: bool = False
    max_waste_percentage_target: float = Field(default=25.0, ge=0, le=100)
    # Soft warning thresholds: pieces breaching these stay in the layout
    # but receive `RiskFlag` entries and trigger `piece_risk` review markers.
    risk_thresholds: RiskThresholds = Field(default_factory=RiskThresholds)

    @field_validator("allowed_rotations")
    @classmethod
    def _rotations_supported(cls, v: list[float]) -> list[float]:
        # MVP: only axis-aligned placement. Unblocking arbitrary angles needs
        # both clipping and texture-transform updates, so reject early.
        for angle in v:
            if angle not in (0.0, 90.0, 180.0, 270.0):
                raise ValueError(
                    f"rotation {angle} not supported in MVP; use 0/90/180/270"
                )
        return v


StrategyName = Literal[
    "balanced", "lowest_waste", "best_visual", "pattern_match", "natural_random"
]


class ProjectInput(BaseModel):
    """Top-level input document."""

    model_config = ConfigDict(extra="allow")

    project_id: str
    project_type: str = "floor"
    units: Literal["mm"] = "mm"

    layout: Layout
    slabs: list[Slab]
    design_requirements: DesignRequirements = Field(default_factory=DesignRequirements)
    rules: Rules = Field(default_factory=Rules)
    options_requested: list[StrategyName] = Field(default_factory=lambda: ["balanced"])
    random_seed: int = 42

    @field_validator("slabs")
    @classmethod
    def _slabs_non_empty(cls, v: list[Slab]) -> list[Slab]:
        if not v:
            raise ValueError("at least one slab is required")
        return v

    @model_validator(mode="after")
    def _slab_ids_unique(self) -> "ProjectInput":
        ids = [s.slab_id for s in self.slabs]
        if len(ids) != len(set(ids)):
            raise ValueError("slab_id values must be unique")
        return self


# ---------------------------------------------------------------------------
# Output models
# ---------------------------------------------------------------------------


class TextureTransform(BaseModel):
    """Enough info for Blender to map the slab image onto the placed piece."""

    image_path: str | None = None
    uv_origin: Point
    uv_width: float
    uv_height: float
    rotation: float = 0.0
    scale: tuple[float, float] = (1.0, 1.0)


RiskFlagType = Literal[
    "small_piece",
    "narrow_piece",
    "short_piece",
    "thin_aspect_ratio",
    "irregular_piece",
]


class RiskFlag(BaseModel):
    """A soft warning attached to a single placed piece.

    The piece is geometrically valid and stays in the layout. The flag
    only signals that a designer or fabricator should look at it.
    """

    type: RiskFlagType
    severity: Literal["low", "medium", "high"] = "medium"
    message: str


class PlacedPiece(BaseModel):
    piece_id: str
    slab_id: str
    project_polygon: PolygonCoords
    slab_polygon: PolygonCoords
    rotation: float = 0.0
    texture_transform: TextureTransform
    is_full_slab: bool = False
    risk_flags: list[RiskFlag] = Field(default_factory=list)


class Seam(BaseModel):
    seam_id: str
    piece_ids: list[str]
    line: list[Point]
    length: float
    visibility: Literal["low", "medium", "high"] = "medium"


class ReviewMarker(BaseModel):
    review_id: str
    type: str
    location: Point
    related_piece_ids: list[str] = Field(default_factory=list)
    severity: Literal["low", "medium", "high"] = "medium"
    message: str


class LayoutMetrics(BaseModel):
    installed_area: float = 0.0
    total_slab_area_used: float = 0.0
    waste_area: float = 0.0
    waste_percentage: float = 0.0
    reusable_offcut_area: float = 0.0
    non_reusable_waste_area: float = 0.0
    piece_count: int = 0
    slabs_used: int = 0
    cut_count_estimate: int = 0
    seam_count: int = 0
    total_seam_length: float = 0.0
    small_piece_count: int = 0
    cutting_complexity_score: int = 1
    estimated_production_difficulty: Literal["low", "medium", "high"] = "low"


class Explanation(BaseModel):
    summary: str = ""
    tradeoffs: list[str] = Field(default_factory=list)


class LayoutOption(BaseModel):
    option_id: str
    option_name: str
    strategy: StrategyName
    recommended: bool = False
    score: float = 0.0
    metrics: LayoutMetrics
    placed_pieces: list[PlacedPiece]
    seams: list[Seam] = Field(default_factory=list)
    review_markers: list[ReviewMarker] = Field(default_factory=list)
    explanation: Explanation = Field(default_factory=Explanation)


class EngineOutput(BaseModel):
    project_id: str
    engine_version: str
    units: Literal["mm"] = "mm"
    generated_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )
    layout_options: list[LayoutOption]

    # Allow callers to attach extra debug payloads without a schema bump.
    model_config = ConfigDict(extra="allow")

    def to_json_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")
