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


@dataclass(frozen=True)
class MarginPolicy:
    """Manufacturing tolerances the fit checker + writer honour.

    Defaults come from typical stone-cutting practice:

      * ``blade_kerf_mm``   — 3 mm width the saw removes per cut. Doubled
                              on each axis because the piece needs a full
                              kerf on each side (unless it's flush with
                              the slab edge, which we don't detect —
                              conservative default).
      * ``edge_trim_mm``    — 5 mm the fabricator trims off each edge of
                              the slab before cutting to remove chipping.
      * ``tolerance_mm``    — 2 mm dimensional slop the checker allows
                              before flagging a piece as ``tight``.
                              Cuts that fit only within this margin are
                              produced with a warning; anything below
                              zero margin is refused.
    """

    blade_kerf_mm: float = 3.0
    edge_trim_mm: float = 5.0
    tolerance_mm: float = 2.0

    def usable_slab_size(
        self, slab_width_mm: float, slab_height_mm: float,
    ) -> tuple[float, float]:
        """The rectangle the cut piece may occupy inside the slab.

        Subtracts an edge trim on all four sides. The blade kerf is
        applied around the piece itself (not the slab) — the fit
        check adds it to the piece's bbox — so it doesn't come out
        of the usable slab dims here.
        """
        return (
            slab_width_mm - 2.0 * self.edge_trim_mm,
            slab_height_mm - 2.0 * self.edge_trim_mm,
        )


# Verdicts, ordered by severity so ``max(verdict, other)`` picks the
# worst finding when a piece exposes several. ``ready`` means the cut
# is factory-ready with margin left over the tolerance; ``tight``
# means it fits but the margin is smaller than the tolerance and the
# operator should review; ``insufficient_margin`` means the cut fits
# only if the blade kerf were smaller; ``does_not_fit`` means the
# slab is too small even before allowances; ``unknown_slab`` means
# the assigned slab id wasn't found in the inventory.
_VERDICT_ORDER = (
    "ready",
    "tight",
    "insufficient_margin",
    "does_not_fit",
    "unknown_slab",
)


@dataclass(frozen=True)
class FactoryFitResult:
    """One row from ``validate_factory_fit``.

    ``verdict`` is the coarse status; ``factory_ready`` is a boolean
    convenience that pinches the verdict to a strict "may we run the
    export today?" answer. ``margin_*`` values are the leftover mm
    per axis after subtracting piece + kerf from the usable slab
    rect — negative when the piece doesn't fit. ``reason`` is a
    single sentence for the UI to show under the affected piece.
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
    margin_width_mm: float
    margin_height_mm: float


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
            ))
            continue

        usable_w, usable_h = policy.usable_slab_size(
            a.slab_width_mm, a.slab_height_mm,
        )
        # Kerf lives on all four sides of the piece (conservative —
        # the writer doesn't try to figure out which edges are flush
        # with the slab so we always demand full clearance).
        required_w = piece_w + 2.0 * policy.blade_kerf_mm
        required_h = piece_h + 2.0 * policy.blade_kerf_mm

        margin_w = usable_w - required_w
        margin_h = usable_h - required_h

        if usable_w <= 0 or usable_h <= 0:
            verdict = "does_not_fit"
            reason = (
                f"Slab is smaller than the edge trim ({policy.edge_trim_mm} "
                f"mm each side)."
            )
        elif margin_w < 0 or margin_h < 0:
            # Doesn't fit AT ALL after allowances. Distinguish
            # "insufficient margin" (piece would fit without kerf/trim
            # but not with) from "does not fit" (piece bigger than the
            # slab even ignoring allowances) so the operator can tell
            # whether tightening the policy would help.
            if piece_w > a.slab_width_mm or piece_h > a.slab_height_mm:
                verdict = "does_not_fit"
                reason = (
                    "Cut piece is larger than the slab itself — a "
                    "different slab is required."
                )
            else:
                verdict = "insufficient_margin"
                reason = (
                    "Blade kerf and edge trim leave "
                    f"{max(0.0, usable_w) - required_w:+.1f} × "
                    f"{max(0.0, usable_h) - required_h:+.1f} mm — "
                    "not enough clearance for a safe cut."
                )
        elif margin_w < policy.tolerance_mm or margin_h < policy.tolerance_mm:
            # Fits, but the leftover margin is smaller than the
            # dimensional tolerance the operator specified. Refuse
            # the "factory-ready" tag; the export path treats this
            # the same as a hard rejection.
            verdict = "tight"
            reason = (
                f"Only {margin_w:.1f} × {margin_h:.1f} mm margin left — "
                f"below the {policy.tolerance_mm:.1f} mm tolerance. "
                "Increase slab size or reduce kerf/trim before cutting."
            )
        else:
            verdict = "ready"
            reason = "OK — margin exceeds the manufacturing tolerance."

        # Zero-margin (exact fit) MUST NOT report ready — that's the
        # explicit spec. The tolerance branch above catches it because
        # tolerance defaults to > 0, but re-guard here so future default
        # tweaks don't accidentally allow a 0.0 margin export.
        if verdict == "ready" and (margin_w <= 0.0 or margin_h <= 0.0):
            verdict = "tight"
            reason = (
                "Cut lands exactly on the slab edge — zero margin. Not "
                "safe for production without manual adjustment."
            )

        out.append(FactoryFitResult(
            piece_id=a.piece_id,
            slab_id=a.slab_id,
            verdict=verdict,
            factory_ready=(verdict == "ready"),
            reason=reason,
            piece_width_mm=piece_w,
            piece_height_mm=piece_h,
            slab_width_mm=a.slab_width_mm,
            slab_height_mm=a.slab_height_mm,
            rotation_needed=a.rotation_needed,
            usable_width_mm=max(0.0, usable_w),
            usable_height_mm=max(0.0, usable_h),
            margin_width_mm=margin_w,
            margin_height_mm=margin_h,
        ))
    return out


def all_factory_ready(results: Iterable[FactoryFitResult]) -> bool:
    return all(r.factory_ready for r in results)


# ---------------------------------------------------------------------------
# DXF writer
# ---------------------------------------------------------------------------


_LAYER_DEFS: dict[str, dict] = {
    "SLAB_BOUNDARIES": {"color": 7},   # slab outline (black/white)
    "SLAB_USABLE_AREA": {"color": 8},  # dashed rect after edge trim + kerf
    "CUT_PIECES":      {"color": 5},   # blue closed cut contour
    "PIECE_LABELS":    {"color": 2},   # yellow — piece id
    "SLAB_INFO":       {"color": 4},   # cyan — slab id + cut + rotation
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
            dxfattribs={"layer": "SLAB_INFO"},
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
            dxfattribs={"layer": "SLAB_BOUNDARIES"},
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
                dxfattribs={"layer": "PIECE_LABELS"},
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
            dxfattribs={"layer": "SLAB_INFO"},
        )
        head_txt.set_placement(
            (sx, sy + sh + text_h * 0.4),
            align=ezdxf.enums.TextEntityAlignment.LEFT,
        )

        # Slab dims — under the slab id.
        dims = (
            f"slab {a.slab_width_mm:.0f} × {a.slab_height_mm:.0f} mm"
        )
        dims_txt = msp.add_text(
            dims, height=text_h * 0.55,
            dxfattribs={"layer": "SLAB_INFO"},
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
            dxfattribs={"layer": "SLAB_INFO"},
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
                dxfattribs={"layer": "SLAB_INFO"},
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
