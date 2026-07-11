"""Factory DXF layout + manufacturing-fit validation.

The older ``dxf_export.build_dxf_bytes`` writer reconstructs the
FLOOR — pieces sit at their real-world architectural positions. That
is what the architect wants to review, but it's the wrong layout to
hand to the factory: a cutter needs to see each SLAB with its cut
piece placed inside, sized to the slab and offset by the blade kerf
and the edge trim, so the CNC can drop a program straight from the
DXF.

This module owns that transformation:

  * ``MarginPolicy``       — configurable blade kerf, edge trim, and
                             manufacturing tolerance (all in mm).
  * ``FactoryFitResult``   — per-piece verdict from the fit checker.
  * ``validate_factory_fit``  — walk assignments, return one result
                             per piece. Zero-margin fits are FLAGGED,
                             not silently marked ready.
  * ``build_factory_dxf_bytes`` — new DXF writer: one slab boundary
                             per assigned slab, cut piece placed
                             inside at the trim + kerf offset,
                             labelled with slab id / piece id / cut
                             dimensions / rotation / waste.

The fit checker is exposed independently so the frontend can call
it to gate the export button before it fires the download.
"""

from __future__ import annotations

import io
import math
from dataclasses import dataclass, field
from typing import Iterable, Sequence

import ezdxf
import ezdxf.enums


# ---------------------------------------------------------------------------
# Policy + result models
# ---------------------------------------------------------------------------


# Manufacturing profile keys the frontend + tests reference by name.
# Kept as module-level constants so a typo becomes a name error
# instead of a silent fallthrough.
PROFILE_STRICT = "strict"      # kerf + trim + tolerance required
PROFILE_STANDARD = "standard"  # kerf + tolerance required (no trim)
PROFILE_EXACT = "exact"        # raw geometry check only
_PROFILES = (PROFILE_STRICT, PROFILE_STANDARD, PROFILE_EXACT)

# How the checker handles a fit whose RAW geometric margin is zero on
# at least one axis (piece flush with the slab edge). This case is
# always physically possible — the operator just needs to align the
# piece to the slab edge — but automation tolerates it differently:
EXACT_EDGE_ALLOW = "allow"  # treat as ready, no warning shown
EXACT_EDGE_WARN = "warn"    # verdict = exact_edge but factory_ready
EXACT_EDGE_BLOCK = "block"  # verdict = exact_edge, factory_ready=False
_EXACT_EDGE_ACTIONS = (
    EXACT_EDGE_ALLOW, EXACT_EDGE_WARN, EXACT_EDGE_BLOCK,
)


@dataclass(frozen=True)
class MarginPolicy:
    """Manufacturing tolerances the fit checker + writer honour.

    Two knobs pick from three named profiles:

      * ``profile = "strict"``   — the historic behaviour: piece must
                                    clear kerf + trim + tolerance on
                                    all four sides.
      * ``profile = "standard"`` (default) — piece must clear kerf +
                                    tolerance. No edge-trim reduction
                                    of the slab. This matches typical
                                    cabinet-shop practice.
      * ``profile = "exact"``     — raw geometry only. Kerf, trim and
                                    tolerance are IGNORED for the
                                    ready/tight/insufficient split;
                                    the checker only refuses a piece
                                    that's larger than the slab.

    Independently, ``exact_edge_action`` decides what to do when the
    RAW geometric margin is zero on at least one axis (the piece
    lands exactly on the slab edge). ``"allow"`` reports ``ready``;
    ``"warn"`` reports ``exact_edge`` but keeps ``factory_ready =
    True`` so the export proceeds; ``"block"`` reports
    ``exact_edge`` with ``factory_ready = False``.

    Rationale for defaults: with the old model an exact-width slab
    (piece == slab, e.g. 1610 × 1610) was blocked with a large
    negative margin (-16 mm under kerf 3 mm + trim 5 mm). The bug
    report calling that "visually valid" is right — physically the
    piece IS the slab. The new defaults treat exact-edge as a
    warning (visible to the operator, not a hard block) and reduce
    the automatic slab reduction to kerf + tolerance.
    """

    blade_kerf_mm: float = 3.0
    edge_trim_mm: float = 5.0
    tolerance_mm: float = 2.0
    profile: str = PROFILE_STANDARD
    exact_edge_action: str = EXACT_EDGE_WARN
    # Two dimensions within this many mm of each other count as
    # "the same" for the exact-edge classifier. Kept small so a
    # measurement rounding to 1610 mm still detects the exact fit
    # against a 1610 mm slab.
    exact_edge_epsilon_mm: float = 0.5

    @property
    def uses_trim(self) -> bool:
        return self.profile == PROFILE_STRICT

    @property
    def uses_kerf(self) -> bool:
        return self.profile in (PROFILE_STRICT, PROFILE_STANDARD)

    @property
    def uses_tolerance(self) -> bool:
        return self.profile in (PROFILE_STRICT, PROFILE_STANDARD)

    def _effective_kerf(self) -> float:
        return self.blade_kerf_mm if self.uses_kerf else 0.0

    def _effective_tolerance(self) -> float:
        return self.tolerance_mm if self.uses_tolerance else 0.0

    def usable_slab_size(
        self, slab_width_mm: float, slab_height_mm: float,
    ) -> tuple[float, float]:
        """The rectangle the cut piece may occupy inside the slab.

        Only the strict profile subtracts an edge trim; the other
        profiles let the piece use the slab's full physical area.
        The blade kerf is applied around the piece itself, not the
        slab, so it doesn't come out of these values either.
        """
        trim = self.edge_trim_mm if self.uses_trim else 0.0
        return (
            slab_width_mm - 2.0 * trim,
            slab_height_mm - 2.0 * trim,
        )


# Verdicts, most severe last so ``max(...)`` picks the worst.
_VERDICT_ORDER = (
    "ready",
    "exact_edge",
    "tight",
    "insufficient_margin",
    "does_not_fit",
    "unknown_slab",
)


@dataclass(frozen=True)
class FactoryFitResult:
    """One row from ``validate_factory_fit``.

    Field layout is designed so both the UI and the operator can
    see both margins side by side:

      * ``geometric_margin_*`` — slab − piece. Positive means the
        piece physically fits; zero means it lands on the slab
        edge; negative means it's larger than the slab (hard
        block).
      * ``manufacturing_margin_*`` — the same margin AFTER the
        profile's kerf + trim + tolerance are subtracted. This is
        the value the ``ready`` / ``tight`` / ``insufficient_margin``
        verdicts key off. When the profile is ``exact`` the two
        margins are equal.

    ``margin_width_mm`` / ``margin_height_mm`` are retained as
    aliases of ``manufacturing_margin_*`` so callers that already
    read those field names keep working. ``factory_ready`` still
    controls the download button.
    """

    piece_id: str
    slab_id: str
    verdict: str
    factory_ready: bool
    reason: str
    piece_width_mm: float
    piece_height_mm: float
    slab_width_mm: float
    slab_height_mm: float
    rotation_needed: bool
    usable_width_mm: float
    usable_height_mm: float
    # Legacy field names — kept in sync with ``manufacturing_margin_*``.
    margin_width_mm: float
    margin_height_mm: float
    geometric_margin_width_mm: float = 0.0
    geometric_margin_height_mm: float = 0.0
    manufacturing_margin_width_mm: float = 0.0
    manufacturing_margin_height_mm: float = 0.0
    profile: str = PROFILE_STANDARD


@dataclass(frozen=True)
class AssignmentInput:
    """One (piece, assigned-slab) pair the checker + writer take.

    ``polygon`` is the closed ring of the REAL cut piece in mm. The
    checker uses the polygon's bbox (nominal_w × nominal_h fed in
    for defensive fallbacks). Cut width and height are the FINAL
    dimensions after any rotation the matcher chose."""

    piece_id: str
    polygon: Sequence[tuple[float, float]]
    cut_width_mm: float
    cut_height_mm: float
    slab_id: str
    slab_width_mm: float
    slab_height_mm: float
    rotation_needed: bool = False
    waste_fraction: float | None = None
    slab_serial: str | None = None


# ---------------------------------------------------------------------------
# Fit validation
# ---------------------------------------------------------------------------


def _polygon_bbox(
    polygon: Sequence[tuple[float, float]],
) -> tuple[float, float]:
    if not polygon:
        return (0.0, 0.0)
    xs = [pt[0] for pt in polygon]
    ys = [pt[1] for pt in polygon]
    return (max(xs) - min(xs), max(ys) - min(ys))


def validate_factory_fit(
    assignments: Iterable[AssignmentInput],
    policy: MarginPolicy,
) -> list[FactoryFitResult]:
    """Compute one ``FactoryFitResult`` per assignment.

    Uses the polygon-derived bbox when the polygon is present; falls
    back to ``cut_width_mm × cut_height_mm``. Applies the piece
    rotation before comparing to the usable slab rect — a piece that
    fits when rotated but not otherwise is marked ``ready`` (or
    ``tight`` if inside the tolerance band).
    """
    out: list[FactoryFitResult] = []
    for a in assignments:
        # Prefer the polygon's real bbox — smaller than the nominal
        # cut for edge clips, which is exactly the value that must
        # fit inside the slab.
        poly_w, poly_h = _polygon_bbox(a.polygon)
        if poly_w <= 0 or poly_h <= 0:
            poly_w = a.cut_width_mm
            poly_h = a.cut_height_mm

        # If the matcher rotated the piece, the width/height that
        # need to fit inside the slab are swapped. The polygon we
        # ship is still in FLOOR orientation; the factory writer
        # applies the rotation when placing it inside the slab.
        piece_w = poly_h if a.rotation_needed else poly_w
        piece_h = poly_w if a.rotation_needed else poly_h

        # The slab isn't in the inventory (assignment references a
        # slab that was removed). Refuse the export.
        if a.slab_width_mm <= 0 or a.slab_height_mm <= 0:
            out.append(FactoryFitResult(
                piece_id=a.piece_id,
                slab_id=a.slab_id,
                verdict="unknown_slab",
                factory_ready=False,
                reason=(
                    f"Slab {a.slab_id!r} not found in the active inventory."
                ),
                piece_width_mm=piece_w,
                piece_height_mm=piece_h,
                slab_width_mm=a.slab_width_mm,
                slab_height_mm=a.slab_height_mm,
                rotation_needed=a.rotation_needed,
                usable_width_mm=0.0,
                usable_height_mm=0.0,
                margin_width_mm=-piece_w,
                margin_height_mm=-piece_h,
                geometric_margin_width_mm=-piece_w,
                geometric_margin_height_mm=-piece_h,
                manufacturing_margin_width_mm=-piece_w,
                manufacturing_margin_height_mm=-piece_h,
                profile=policy.profile,
            ))
            continue

        # --- Stage 1: raw GEOMETRY fit -----------------------------
        # Does the piece physically fit inside the slab? This is a
        # hard gate — no policy setting relaxes it.
        geo_margin_w = a.slab_width_mm - piece_w
        geo_margin_h = a.slab_height_mm - piece_h

        # Manufacturing allowance figures (needed for the label /
        # response even when we short-circuit to a hard block).
        kerf = policy._effective_kerf()
        tol = policy._effective_tolerance()
        usable_w, usable_h = policy.usable_slab_size(
            a.slab_width_mm, a.slab_height_mm,
        )
        required_w = piece_w + 2.0 * kerf
        required_h = piece_h + 2.0 * kerf
        mfg_margin_w = usable_w - required_w
        mfg_margin_h = usable_h - required_h

        # Exact-edge classifier — piece lands within ``epsilon`` of
        # the slab edge on at least one axis, both margins ≥ -epsilon.
        eps = policy.exact_edge_epsilon_mm
        is_exact_edge = (
            geo_margin_w >= -eps and geo_margin_h >= -eps
            and (abs(geo_margin_w) <= eps or abs(geo_margin_h) <= eps)
        )

        if geo_margin_w < -eps or geo_margin_h < -eps:
            # Piece is larger than the slab on at least one axis —
            # no cutting policy can rescue this.
            verdict = "does_not_fit"
            factory_ready = False
            reason = (
                "Cut piece is larger than the slab itself — a "
                "different slab is required."
            )

        elif is_exact_edge:
            # Piece flush with the slab edge on at least one axis.
            # Whether this blocks the export is a policy question,
            # not a geometry question.
            verdict = "exact_edge"
            if policy.exact_edge_action == EXACT_EDGE_ALLOW:
                verdict = "ready"
                factory_ready = True
                reason = (
                    "Piece matches the slab edge exactly — allowed "
                    "by the current profile."
                )
            elif policy.exact_edge_action == EXACT_EDGE_BLOCK:
                factory_ready = False
                reason = (
                    "Piece lands exactly on the slab edge. The "
                    "current profile blocks exact-edge fits — "
                    "switch it to 'warn' or 'allow' if the "
                    "operator can align the piece by hand."
                )
            else:
                # WARN — export proceeds, UI surfaces the flag.
                factory_ready = True
                reason = (
                    "Piece matches the slab edge exactly. Physically "
                    "possible; the operator will need to align the "
                    "cut to the slab edge with no kerf reserve."
                )

        elif policy.profile == PROFILE_EXACT:
            # Exact profile: geometry-only check, and the exact-edge
            # branch above already handled the flush cases. Anything
            # that gets here has positive raw margin → ready.
            verdict = "ready"
            factory_ready = True
            reason = (
                f"OK — {geo_margin_w:.1f} × {geo_margin_h:.1f} mm "
                "geometric margin (exact profile)."
            )

        # --- Stage 2: MANUFACTURING allowance fit -------------------
        elif mfg_margin_w < 0 or mfg_margin_h < 0:
            # Fits raw but not after kerf / trim. Not physically
            # impossible; the operator can loosen the profile to
            # let it through if they trust the tooling.
            verdict = "insufficient_margin"
            factory_ready = False
            missing_w = -mfg_margin_w if mfg_margin_w < 0 else 0.0
            missing_h = -mfg_margin_h if mfg_margin_h < 0 else 0.0
            reason = _describe_insufficient(
                policy, geo_margin_w, geo_margin_h,
                mfg_margin_w, mfg_margin_h, missing_w, missing_h,
            )

        elif mfg_margin_w < tol or mfg_margin_h < tol:
            verdict = "tight"
            factory_ready = False
            reason = (
                f"Only {mfg_margin_w:.1f} × {mfg_margin_h:.1f} mm "
                f"manufacturing margin — below the {tol:.1f} mm "
                "tolerance. Loosen the tolerance or switch to the "
                "'standard' / 'exact' profile if the tooling can "
                "cope with less clearance."
            )

        else:
            verdict = "ready"
            factory_ready = True
            reason = (
                f"OK — {mfg_margin_w:.1f} × {mfg_margin_h:.1f} mm "
                "manufacturing margin (above tolerance)."
            )

        out.append(FactoryFitResult(
            piece_id=a.piece_id,
            slab_id=a.slab_id,
            verdict=verdict,
            factory_ready=factory_ready,
            reason=reason,
            piece_width_mm=piece_w,
            piece_height_mm=piece_h,
            slab_width_mm=a.slab_width_mm,
            slab_height_mm=a.slab_height_mm,
            rotation_needed=a.rotation_needed,
            usable_width_mm=max(0.0, usable_w),
            usable_height_mm=max(0.0, usable_h),
            # ``margin_*`` remains the manufacturing figure so the
            # V1 UI keeps rendering the same value. Callers that want
            # to see both can now read the geometric_/manufacturing_
            # fields alongside.
            margin_width_mm=mfg_margin_w,
            margin_height_mm=mfg_margin_h,
            geometric_margin_width_mm=geo_margin_w,
            geometric_margin_height_mm=geo_margin_h,
            manufacturing_margin_width_mm=mfg_margin_w,
            manufacturing_margin_height_mm=mfg_margin_h,
            profile=policy.profile,
        ))
    return out


def _describe_insufficient(
    policy: MarginPolicy,
    geo_w: float, geo_h: float,
    mfg_w: float, mfg_h: float,
    missing_w: float, missing_h: float,
) -> str:
    """One-sentence explanation of a failing manufacturing check
    that highlights the difference between raw geometry and the
    allowances the current profile subtracts."""
    parts: list[str] = []
    if policy.uses_kerf and policy.blade_kerf_mm > 0:
        parts.append(f"kerf {policy.blade_kerf_mm:.1f} mm")
    if policy.uses_trim and policy.edge_trim_mm > 0:
        parts.append(f"trim {policy.edge_trim_mm:.1f} mm")
    allowance_desc = " + ".join(parts) if parts else "manufacturing allowance"
    return (
        f"Piece fits raw ({geo_w:+.1f} × {geo_h:+.1f} mm geometric "
        f"margin) but the {allowance_desc} leaves "
        f"{mfg_w:+.1f} × {mfg_h:+.1f} mm — need "
        f"{max(missing_w, missing_h):.1f} mm more. Loosen the "
        f"profile (try 'standard' or 'exact') if the tooling can "
        "cope."
    )


def all_factory_ready(results: Iterable[FactoryFitResult]) -> bool:
    return all(r.factory_ready for r in results)


# ---------------------------------------------------------------------------
# DXF writer
# ---------------------------------------------------------------------------


_LAYER_DEFS: dict[str, dict] = {
    # Layer names spec'd by the V1.1 milestone brief. Rename from
    # the earlier ``SLAB_BOUNDARIES`` / ``SLAB_INFO`` scheme so
    # AutoCAD operators see the standard convention.
    "SLAB_BOUNDARY":    {"color": 7},   # slab outline (black/white)
    "SLAB_USABLE_AREA": {"color": 8},   # dashed rect after edge trim + kerf
    "CUT_PIECES":       {"color": 5},   # blue closed cut contour
    "DIMENSIONS":       {"color": 4},   # cyan — dimension lines / text
    "LABELS":           {"color": 2},   # yellow — piece + slab ids
}

# Slab-tile layout in the DXF file. Slabs are arranged in a grid so
# the operator can page through the file per-slab without having to
# scroll a wall of overlapping geometry. Numbers in mm.
_SLAB_GAP_MM = 200.0


def _rotate_polygon(
    polygon: Sequence[tuple[float, float]],
    angle_deg: float,
    pivot: tuple[float, float],
) -> list[tuple[float, float]]:
    theta = math.radians(angle_deg)
    cos_t = math.cos(theta)
    sin_t = math.sin(theta)
    px, py = pivot
    out: list[tuple[float, float]] = []
    for x, y in polygon:
        dx = x - px
        dy = y - py
        out.append((
            px + dx * cos_t - dy * sin_t,
            py + dx * sin_t + dy * cos_t,
        ))
    return out


def _translate_polygon(
    polygon: Sequence[tuple[float, float]],
    dx: float, dy: float,
) -> list[tuple[float, float]]:
    return [(x + dx, y + dy) for x, y in polygon]


def _place_piece_in_slab(
    polygon: Sequence[tuple[float, float]],
    rotation_needed: bool,
    slab_origin: tuple[float, float],
    inset: float,
) -> list[tuple[float, float]]:
    """Rotate + translate a piece polygon into its slab local frame.

    The piece polygon comes in FLOOR coordinates; for the factory
    layout we want it sitting inside the slab rectangle at
    ``(slab_x + inset, slab_y + inset)``. Applies the assigned
    rotation first, then translates the piece's bbox min to the
    inset corner.
    """
    if not polygon:
        return []
    # Rotate around the piece centroid so the resulting bbox is
    # tight around the rotated shape (rotating around (0,0) would
    # translate the piece off into some far corner first).
    xs = [pt[0] for pt in polygon]
    ys = [pt[1] for pt in polygon]
    cx = (min(xs) + max(xs)) / 2.0
    cy = (min(ys) + max(ys)) / 2.0
    if rotation_needed:
        rotated = _rotate_polygon(polygon, 90.0, (cx, cy))
    else:
        rotated = list(polygon)
    rxs = [pt[0] for pt in rotated]
    rys = [pt[1] for pt in rotated]
    xmin = min(rxs)
    ymin = min(rys)
    sx, sy = slab_origin
    return _translate_polygon(rotated, sx + inset - xmin, sy + inset - ymin)


def _ensure_layers(doc: "ezdxf.document.Drawing") -> None:
    for name, attrs in _LAYER_DEFS.items():
        if name not in doc.layers:
            doc.layers.add(name=name, **attrs)


def _label_text_height(policy_max_slab_mm: float) -> float:
    """Text height that scales with the slab so labels stay legible
    from a small tile to a full 3200 × 2000 mm slab. 1/60th of the
    max slab dim, clamped between 30 and 200 mm."""
    return max(30.0, min(policy_max_slab_mm / 60.0, 200.0))


def build_factory_dxf_bytes(
    demo_id: str,
    assignments: Sequence[AssignmentInput],
    policy: MarginPolicy,
    fit_results: Sequence[FactoryFitResult] | None = None,
) -> bytes:
    """Emit a factory cut plan DXF.

    Contract:

      * One rectangle per assigned slab on ``SLAB_BOUNDARIES`` with
        the slab's real w × h.
      * A dashed rectangle on ``SLAB_USABLE_AREA`` showing the
        allowed cut region after ``edge_trim + blade_kerf``.
      * The assigned piece polygon on ``CUT_PIECES`` placed inside
        the slab at ``edge_trim + blade_kerf`` from the bottom-left
        corner. Rotation is applied when the matcher said so.
      * ``PIECE_LABELS`` — piece id, centred inside the piece.
      * ``SLAB_INFO`` — slab id above the boundary, and a metadata
        strip under the piece with cut dims / rotation / waste /
        fit verdict.

    The slab rectangles are packed into a grid so the operator can
    open the file and see every slab at once instead of having to
    disambiguate overlapping cut plans.
    """
    doc = ezdxf.new(dxfversion="R2013", setup=True)
    doc.units = ezdxf.units.MM
    _ensure_layers(doc)
    msp = doc.modelspace()

    fit_by_key: dict[tuple[str, str], FactoryFitResult] = {}
    if fit_results is not None:
        for r in fit_results:
            fit_by_key[(r.piece_id, r.slab_id)] = r

    if not assignments:
        # Empty file is still legal, but keeps a single note so the
        # operator knows what happened.
        msp.add_text(
            "No assigned pieces — nothing to cut.",
            height=100.0,
            dxfattribs={"layer": "LABELS"},
        ).set_placement((0, 0), align=ezdxf.enums.TextEntityAlignment.LEFT)
        sink = io.StringIO()
        doc.write(sink)
        return sink.getvalue().encode("utf-8")

    max_slab_dim = max(
        max(a.slab_width_mm, a.slab_height_mm) for a in assignments
    )
    text_h = _label_text_height(max_slab_dim)
    inset = policy.edge_trim_mm + policy.blade_kerf_mm

    # Grid layout: pack slabs left-to-right, wrap after a row of
    # roughly a square. Compute cols from the count so a 4-piece
    # export produces 2×2, 9-piece → 3×3, etc.
    n = len(assignments)
    cols = max(1, int(math.ceil(math.sqrt(n))))
    max_row_w = 0.0
    row_h = 0.0
    col = 0
    x_cursor = 0.0
    y_cursor = 0.0
    row_slabs: list[float] = []

    for idx, a in enumerate(assignments):
        # Slab boundary rectangle at (x_cursor, y_cursor).
        sx, sy = x_cursor, y_cursor
        sw, sh = a.slab_width_mm, a.slab_height_mm
        msp.add_lwpolyline(
            [(sx, sy), (sx + sw, sy),
             (sx + sw, sy + sh), (sx, sy + sh)],
            close=True,
            dxfattribs={"layer": "SLAB_BOUNDARY"},
        )

        # Usable-area rectangle (dashed) inside the boundary.
        usable_w, usable_h = policy.usable_slab_size(sw, sh)
        if usable_w > 0 and usable_h > 0:
            ux0 = sx + policy.edge_trim_mm
            uy0 = sy + policy.edge_trim_mm
            usable = msp.add_lwpolyline(
                [(ux0, uy0), (ux0 + usable_w, uy0),
                 (ux0 + usable_w, uy0 + usable_h), (ux0, uy0 + usable_h)],
                close=True,
                dxfattribs={"layer": "SLAB_USABLE_AREA"},
            )
            # Dashed rendering is set via linetype; fall back cleanly
            # if the DXF viewer doesn't have the DASHED linetype.
            usable.dxf.linetype = "DASHED"

        # Place the piece inside the slab at (inset, inset) from the
        # bottom-left corner.
        placed = _place_piece_in_slab(
            a.polygon, a.rotation_needed, (sx, sy), inset,
        )
        if len(placed) >= 3:
            msp.add_lwpolyline(
                placed, close=True,
                dxfattribs={"layer": "CUT_PIECES"},
            )
            pxs = [pt[0] for pt in placed]
            pys = [pt[1] for pt in placed]
            pcx = (min(pxs) + max(pxs)) / 2.0
            pcy = (min(pys) + max(pys)) / 2.0

            # Piece id — centred inside the piece.
            piece_label = msp.add_text(
                a.piece_id,
                height=text_h,
                dxfattribs={"layer": "LABELS"},
            )
            piece_label.set_placement(
                (pcx, pcy),
                align=ezdxf.enums.TextEntityAlignment.MIDDLE_CENTER,
            )

        # Slab id — above the slab boundary. Kept above so it doesn't
        # collide with the piece label.
        head = f"SLAB {a.slab_id}"
        if a.slab_serial:
            head += f" · S/N {a.slab_serial}"
        head_txt = msp.add_text(
            head, height=text_h * 0.9,
            dxfattribs={"layer": "LABELS"},
        )
        head_txt.set_placement(
            (sx, sy + sh + text_h * 0.4),
            align=ezdxf.enums.TextEntityAlignment.LEFT,
        )

        # Slab dims — under the slab id. On the DIMENSIONS layer so
        # dimension-only viewers pick them up.
        dims = (
            f"slab {a.slab_width_mm:.0f} × {a.slab_height_mm:.0f} mm"
        )
        dims_txt = msp.add_text(
            dims, height=text_h * 0.55,
            dxfattribs={"layer": "DIMENSIONS"},
        )
        dims_txt.set_placement(
            (sx, sy + sh + text_h * 0.4 + text_h * 1.05),
            align=ezdxf.enums.TextEntityAlignment.LEFT,
        )

        # Cut summary — under the slab boundary.
        cut_w = a.cut_width_mm
        cut_h = a.cut_height_mm
        rot = " · ↻90°" if a.rotation_needed else ""
        waste = (
            f" · waste {a.waste_fraction * 100:.0f}%"
            if a.waste_fraction is not None else ""
        )
        cut_line = (
            f"cut {cut_w:.0f} × {cut_h:.0f} mm{rot}{waste}"
        )
        cut_txt = msp.add_text(
            cut_line, height=text_h * 0.55,
            dxfattribs={"layer": "DIMENSIONS"},
        )
        cut_txt.set_placement(
            (sx, sy - text_h * 0.8),
            align=ezdxf.enums.TextEntityAlignment.LEFT,
        )

        # Fit verdict text (if we have one). Sits under the cut line.
        result = fit_by_key.get((a.piece_id, a.slab_id))
        if result is not None:
            verdict = (
                f"fit: {result.verdict}"
                f" · margin {result.margin_width_mm:+.1f} × "
                f"{result.margin_height_mm:+.1f} mm"
            )
            verdict_txt = msp.add_text(
                verdict, height=text_h * 0.5,
                dxfattribs={"layer": "LABELS"},
            )
            verdict_txt.set_placement(
                (sx, sy - text_h * 0.8 - text_h * 0.9),
                align=ezdxf.enums.TextEntityAlignment.LEFT,
            )

        # Advance the grid cursor.
        row_slabs.append(sh)
        col += 1
        if col >= cols:
            col = 0
            max_row_w = max(max_row_w, x_cursor + sw)
            row_h = max(row_h, max(row_slabs)) if row_slabs else 0.0
            row_slabs = []
            y_cursor += row_h + _SLAB_GAP_MM + text_h * 3.0
            x_cursor = 0.0
            row_h = 0.0
        else:
            x_cursor += sw + _SLAB_GAP_MM

    sink = io.StringIO()
    doc.write(sink)
    return sink.getvalue().encode("utf-8")


def group_by_slab(
    assignments: Sequence[AssignmentInput],
) -> dict[str, list[AssignmentInput]]:
    """Group assignments by ``slab_id`` preserving insertion order.

    Duplicate-slab assignments (allowed by the Step-4 override) end
    up on a single slab DXF containing every piece that shares the
    slab, which is what the CNC operator needs — they'll do all cuts
    on that slab in one pass. Order preservation keeps the DXFs
    deterministic for a given (input, layout option).
    """
    out: dict[str, list[AssignmentInput]] = {}
    for a in assignments:
        out.setdefault(a.slab_id, []).append(a)
    return out


def build_single_slab_dxf_bytes(
    slab_assignments: Sequence[AssignmentInput],
    policy: MarginPolicy,
    fit_results: Sequence[FactoryFitResult] | None = None,
) -> bytes:
    """Emit a single-slab factory DXF.

    Contract:
      * One rectangle on ``SLAB_BOUNDARY`` with the slab's real
        width × height.
      * Dashed usable area on ``SLAB_USABLE_AREA`` inside the
        boundary.
      * Every piece assigned to this slab rendered on
        ``CUT_PIECES`` at the ``edge_trim + kerf`` offset. When
        multiple pieces share a slab they are stacked vertically
        inside the usable area (a simple "shelf" packer — one
        piece per row) so cuts don't overlap.
      * ``LABELS`` — slab id header + per-piece labels centred
        inside each piece.
      * ``DIMENSIONS`` — slab and per-piece dimension text.

    Callers must pass at least one assignment; every assignment
    must share the same ``slab_id``.
    """
    if not slab_assignments:
        raise ValueError("build_single_slab_dxf_bytes: no assignments")
    slab_id = slab_assignments[0].slab_id
    if any(a.slab_id != slab_id for a in slab_assignments):
        raise ValueError(
            "build_single_slab_dxf_bytes: mixed slab_ids in one call",
        )
    sw = slab_assignments[0].slab_width_mm
    sh = slab_assignments[0].slab_height_mm
    slab_serial = slab_assignments[0].slab_serial

    doc = ezdxf.new(dxfversion="R2013", setup=True)
    doc.units = ezdxf.units.MM
    _ensure_layers(doc)
    msp = doc.modelspace()

    text_h = _label_text_height(max(sw, sh))
    inset = policy.edge_trim_mm + policy.blade_kerf_mm

    # Slab boundary (closed).
    msp.add_lwpolyline(
        [(0.0, 0.0), (sw, 0.0), (sw, sh), (0.0, sh)],
        close=True,
        dxfattribs={"layer": "SLAB_BOUNDARY"},
    )

    # Usable area (dashed).
    usable_w, usable_h = policy.usable_slab_size(sw, sh)
    if usable_w > 0 and usable_h > 0:
        ux0 = policy.edge_trim_mm
        uy0 = policy.edge_trim_mm
        usable = msp.add_lwpolyline(
            [(ux0, uy0), (ux0 + usable_w, uy0),
             (ux0 + usable_w, uy0 + usable_h), (ux0, uy0 + usable_h)],
            close=True,
            dxfattribs={"layer": "SLAB_USABLE_AREA"},
        )
        usable.dxf.linetype = "DASHED"

    # Slab id + dims (top-left of the drawing, above the slab).
    header = f"SLAB {slab_id}"
    if slab_serial:
        header += f" · S/N {slab_serial}"
    msp.add_text(
        header, height=text_h * 0.9,
        dxfattribs={"layer": "LABELS"},
    ).set_placement(
        (0.0, sh + text_h * 0.4),
        align=ezdxf.enums.TextEntityAlignment.LEFT,
    )
    msp.add_text(
        f"slab {sw:.0f} × {sh:.0f} mm",
        height=text_h * 0.55,
        dxfattribs={"layer": "DIMENSIONS"},
    ).set_placement(
        (0.0, sh + text_h * 0.4 + text_h * 1.05),
        align=ezdxf.enums.TextEntityAlignment.LEFT,
    )

    fit_by_key: dict[tuple[str, str], FactoryFitResult] = {}
    if fit_results is not None:
        for r in fit_results:
            fit_by_key[(r.piece_id, r.slab_id)] = r

    # Shelf packer: stack pieces vertically inside the usable area.
    # Every piece gets the LEFT edge of the usable region as its x
    # origin; y advances by piece_height + kerf per row. This isn't
    # optimal packing, but it's deterministic, easy for the operator
    # to read, and the common case is 1 piece / slab anyway.
    y_cursor = policy.edge_trim_mm + policy.blade_kerf_mm
    for a in slab_assignments:
        # Rotate the polygon around its own centroid so the bbox is
        # tight, then translate into place.
        xs = [pt[0] for pt in a.polygon]
        ys = [pt[1] for pt in a.polygon]
        cx = (min(xs) + max(xs)) / 2.0
        cy = (min(ys) + max(ys)) / 2.0
        if a.rotation_needed:
            rotated = _rotate_polygon(a.polygon, 90.0, (cx, cy))
        else:
            rotated = list(a.polygon)
        rxs = [pt[0] for pt in rotated]
        rys = [pt[1] for pt in rotated]
        rw = max(rxs) - min(rxs)
        rh = max(rys) - min(rys)
        x_place = policy.edge_trim_mm + policy.blade_kerf_mm
        placed = _translate_polygon(
            rotated, x_place - min(rxs), y_cursor - min(rys),
        )
        if len(placed) < 3:
            continue

        msp.add_lwpolyline(
            placed, close=True,
            dxfattribs={"layer": "CUT_PIECES"},
        )

        pcx = x_place + rw / 2.0
        pcy = y_cursor + rh / 2.0
        msp.add_text(
            a.piece_id, height=text_h,
            dxfattribs={"layer": "LABELS"},
        ).set_placement(
            (pcx, pcy),
            align=ezdxf.enums.TextEntityAlignment.MIDDLE_CENTER,
        )
        rot = " · ↻90°" if a.rotation_needed else ""
        waste = (
            f" · waste {a.waste_fraction * 100:.0f}%"
            if a.waste_fraction is not None else ""
        )
        msp.add_text(
            (
                f"{a.piece_id}: {a.cut_width_mm:.0f} × "
                f"{a.cut_height_mm:.0f} mm{rot}{waste}"
            ),
            height=text_h * 0.5,
            dxfattribs={"layer": "DIMENSIONS"},
        ).set_placement(
            (x_place, y_cursor + rh + text_h * 0.15),
            align=ezdxf.enums.TextEntityAlignment.LEFT,
        )

        result = fit_by_key.get((a.piece_id, a.slab_id))
        if result is not None:
            msp.add_text(
                (
                    f"fit: {result.verdict} · margin "
                    f"{result.margin_width_mm:+.1f} × "
                    f"{result.margin_height_mm:+.1f} mm"
                ),
                height=text_h * 0.45,
                dxfattribs={"layer": "LABELS"},
            ).set_placement(
                (x_place, y_cursor - text_h * 0.6),
                align=ezdxf.enums.TextEntityAlignment.LEFT,
            )

        y_cursor += rh + policy.blade_kerf_mm * 2.0 + policy.edge_trim_mm

    sink = io.StringIO()
    doc.write(sink)
    return sink.getvalue().encode("utf-8")


# ---------------------------------------------------------------------------
# Filename helpers — kept here so the same rules apply to overview
# and per-slab DXFs, and the frontend can request the same pattern.
# ---------------------------------------------------------------------------


_FILENAME_SAFE_CHARS = set(
    "abcdefghijklmnopqrstuvwxyz"
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "0123456789-_",
)


def sanitize_filename_component(raw: str, fallback: str = "project") -> str:
    """Reduce ``raw`` to a filesystem-safe fragment.

    * Whitespace and unsafe punctuation collapse to underscores.
    * Consecutive underscores collapse to one.
    * Leading / trailing underscores are trimmed.
    * If nothing safe survives, returns ``fallback``.

    This is intentionally strict — the resulting fragment must be
    safe on Windows, macOS and Linux without additional quoting.
    """
    if not raw:
        return fallback
    out: list[str] = []
    prev_underscore = False
    for ch in raw:
        if ch in _FILENAME_SAFE_CHARS:
            out.append(ch)
            prev_underscore = False
        else:
            if not prev_underscore:
                out.append("_")
                prev_underscore = True
    cleaned = "".join(out).strip("_")
    return cleaned or fallback


def slab_filename_component(slab_id: str) -> str:
    """Sanitize a slab id for use inside a filename. Slab ids can
    include slashes and colons in the real inventory (e.g.
    ``1781722-4731/AV2040643-04``) which are invalid on most
    filesystems — collapse them to underscores."""
    return sanitize_filename_component(slab_id, fallback="slab")
