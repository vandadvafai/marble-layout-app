// SVG renderer + interactive canvas.
//
// Interaction is gated STRICTLY by ``mode`` — no intent inference,
// no hit-test priority games. The mode the toolbar advertises is
// the mode the canvas honours:
//
//   * mode = "select_piece"
//       * click a piece           → select it (blue outline)
//       * click a doorway/column/ → select it (plan annotations are
//         guide line                 separate from the seam/piece split)
//       * click a seam            → NOTHING. Seams are inert.
//       * drag empty canvas       → pan
//   * mode = "edit_seam"
//       * click a seam            → select it; drag = move (50 mm snap)
//       * click a piece           → NOTHING. Pieces are inert.
//       * doorways/columns/guides → still selectable
//       * drag empty canvas       → pan
//   * mode = "doorway"
//       * drag in empty area → live-preview line; on release create
//         a new doorway in plan
//   * mode = "column"
//       * drag in empty area → live-preview rectangle; on release
//         create a new column in plan
//   * mode = "guide_line"
//       * drag in empty area → live-preview line; on release create
//         a new guide line
//
// Wheel-zoom works in every mode (zooms around the cursor). Empty
// short clicks in creation modes are treated as no-ops via
// ``MIN_DRAW_LENGTH_MM`` — keeps designers from accidentally
// spawning zero-length doorways.
//
// Validation highlights are unchanged from the seam-editing
// milestone:
//   * R1 violator piece → red fill,
//   * absorbed-sliver holder → amber,
//   * R2 seam (crosses doorway) → red on top of the seam,
// and now:
//   * selected doorway/column/guide line → blue outline.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { applySeamMove, resolveTargetPosition } from "../lib/editing";
import { bboxFromPolygon, cutDimsForPiece } from "../lib/pieceGeom";
import {
  MIN_DRAW_LENGTH_MM, rectanglePolygon,
} from "../lib/planEdits";
import { deriveSeams } from "../lib/seams";
import {
  findPieceAt, inferOrientation, seamPositionFromDrag, zoneBbox,
} from "../lib/seamAdd";
import type {
  Assignments, DrawingState, EditorMode, InventoryMatchResponse,
  Layout, Piece, Plan, Point, Seam, Selection, ValidationResult,
} from "../lib/types";

type Box = { x: number; y: number; w: number; h: number };

interface Props {
  layout: Layout;
  pieces: Piece[];
  plan: Plan;
  validation?: ValidationResult | null;
  mode: EditorMode;
  selection: Selection | null;
  onSelect: (selection: Selection | null) => void;
  onSeamDragMove: (pieces: Piece[]) => void;
  onSeamDragEnd: () => void;
  onAddDoorway: (segment: [Point, Point]) => void;
  onAddColumn: (polygon: Point[]) => void;
  onAddGuideLine: (segment: [Point, Point]) => void;
  /** Working tile size + provenance for the canvas-toolbar label
   *  ("Working slab: 179 × 173 cm · from inventory"). Optional; the
   *  canvas falls back to the layout's intrinsic tile dimensions
   *  when not supplied (early loads, before any regeneration). */
  tileChoice?: {
    tile_width_mm: number;
    tile_height_mm: number;
    basis: "explicit_override" | "inventory_median";
  } | null;
  /** Step-4 slab assignments — when present, each assigned piece's
   *  polygon is filled with the slab photo (rendered via SVG
   *  pattern). Pieces without an assignment keep the default fill.
   *  Optional so Step 2 (no assignments yet) can omit it. */
  assignments?: Assignments;
  /** Matcher response used to look up image_path for assigned
   *  slabs. The slab-image endpoint serves bytes by slab_id, so
   *  what we actually need is "does this slab have an image at
   *  all?" — answered by the matcher's per-candidate
   *  ``image_path`` field. */
  inventoryMatch?: InventoryMatchResponse | null;
  /** 0.1.53 — Manual swap mode. When true (Step 4 only), pointer
   *  interactions on pieces switch from "select" to "drag-to-swap":
   *  pressing on Piece A and releasing on Piece B fires
   *  ``onSwapAssignments(A.id, B.id)``. Piece geometry is never
   *  mutated by the canvas — only the assignments map moves. */
  swapMode?: boolean;
  onSwapAssignments?: (piece_a_id: string, piece_b_id: string) => void;
  /** V1.1 — HTML5 drag-and-drop assignment. When a slab candidate
   *  is dragged from the inventory sidebar and dropped on a piece
   *  polygon, the canvas calls this with the target piece id and
   *  the dropped slab id. Runs regardless of ``swapMode``. */
  onAssignSlabToPiece?: (piece_id: string, slab_id: string) => void;
  /** Per-piece red-ring overlay used in Step 4 to flag pieces whose
   *  current slab is too small (typical after a manual swap). The
   *  canvas reads the set as a quick lookup; the source of truth is
   *  ``assignmentStatusFor`` in ``lib/finalAssign``. Optional. */
  invalidPieceIds?: Set<string>;
  onAddSeam: (args: {
    orientation: "vertical" | "horizontal";
    position: number;
    zone_id: string;
  }) => void;
}

const STYLES = {
  bg: "#ffffff",
  boundary: "#202020",
  boundaryWidth: 8,
  pieceFace: "#fafafa",
  pieceFaceR1: "#fde2e1",
  pieceFaceR1Edge: "#c01010",
  pieceFaceAbsorbed: "#fff4d6",
  pieceFaceAbsorbedEdge: "#d18b00",
  pieceEdge: "#909090",
  pieceWidth: 3,
  hole: "#eaeaea",
  holeStroke: "#808080",
  space: "#fffacc",
  spaceOpacity: 0.25,
  doorway: "#e07a00",
  doorwayMain: "#c01010",
  doorwayWidth: 24,
  column: "#7daccd",
  columnStroke: "#1f4c75",
  columnWidth: 4,
  guide: "#888888",
  selectedStroke: "#2050a0",
  selectedWidth: 5,
  selectedPieceLabelSize: 120,  // engine mm; readable when zoomed
  // 0.1.46 — assignment-status fills used in Step 4 when no slab
  // photo is available (image-fill takes priority when available).
  // Colours mirror the legend pills in Step4ExportBar.
  pieceFaceAssignedNoImg: "#dff0df",
  pieceFaceAssignedNoImgEdge: "#1f7a1f",
  pieceFaceNoMatch: "#f9d6db",
  pieceFaceNoMatchEdge: "#b00020",
  pieceFaceDuplicate: "#fbeacd",
  pieceFaceDuplicateEdge: "#8a6300",
  seamLine: "#cccccc",
  seamLineHover: "#88a8d0",
  seamLineSelected: "#2050a0",
  seamLineR2: "#cc1c1c",
  /** Cross-zone seams render in a slightly darker tone so the
   *  designer can still tell them apart from interior seams (the
   *  drag mechanics are identical). */
  seamLineBoundary: "#888888",
  /** "Mode is edit_seam" visual: all seams render in the editor's
   *  blue accent so the designer reads the canvas as "I'm editing
   *  seams now". */
  seamLineActive: "#3170c0",
  seamLineWidth: 2,
  seamLineBoundaryWidth: 3,
  seamLineSelectedWidth: 4,
  seamLineActiveWidth: 3,
  /** Piece border softened when seams are the focus — keeps the
   *  canvas readable without competing visually with the seams. */
  pieceEdgeMuted: "#c0c0c0",
  preview: "#2050a0",
  previewDash: "8 6",
};

// Hit widths are in viewBox units (mm). ``non-scaling-stroke`` keeps
// the rendered hit area at ``SEAM_HIT_WIDTH`` SCREEN pixels regardless
// of zoom, so a value of 24 buys a 24 px-wide click target — wide
// enough to grab reliably with mouse + touch.
const SEAM_HIT_WIDTH = 24;
const DOORWAY_HIT_WIDTH = 30;
const GUIDE_HIT_WIDTH = 16;

/** Either of the two selection modes — pan + plan-annotation clicks
 *  work in both. The piece-vs-seam split inside the mode is what
 *  determines which gets primary attention. */
function isSelectionMode(m: EditorMode): boolean {
  return m === "select_piece" || m === "edit_seam";
}

export default function LayoutCanvas({
  layout, pieces, plan, validation, mode, selection,
  onSelect, onSeamDragMove, onSeamDragEnd,
  onAddDoorway, onAddColumn, onAddGuideLine, onAddSeam,
  tileChoice, assignments, inventoryMatch,
  swapMode = false, onSwapAssignments, invalidPieceIds,
  onAssignSlabToPiece,
}: Props) {
  const [dropTargetPieceId, setDropTargetPieceId] = useState<string | null>(null);
  const initialBox = useMemo<Box>(() => {
    const [x0, y0, x1, y1] = layout.target.bbox;
    const w = x1 - x0;
    const h = y1 - y0;
    const margin = Math.max(w, h) * 0.04;
    return {
      x: x0 - margin, y: y0 - margin,
      w: w + margin * 2, h: h + margin * 2,
    };
  }, [layout.target.bbox]);

  const [box, setBox] = useState<Box>(initialBox);
  const svgRef = useRef<SVGSVGElement | null>(null);
  const panState = useRef<{ startX: number; startY: number; startBox: Box } | null>(null);
  const seamDrag = useRef<{ seam: Seam; basePieces: Piece[] } | null>(null);
  // 0.1.53 — manual swap drag. ``pointerId`` is the captured pointer
  // so onPointerMove/Up on the SVG root receive events even when the
  // cursor leaves the source polygon. ``pos`` is the latest pointer
  // position in viewBox/mm coordinates — used both for the floating
  // chip overlay and for hit-testing the drop target. ``hoverPieceId``
  // is whichever piece the cursor is currently over (computed cheaply
  // from the rectangular bbox of each piece).
  const [swapDrag, setSwapDrag] = useState<{
    fromPieceId: string;
    pointerId: number;
    pos: Point;
    hoverPieceId: string | null;
  } | null>(null);
  const [drawing, setDrawing] = useState<DrawingState | null>(null);
  // Track the currently-hovered seam so the canvas can highlight it
  // before the designer actually grabs it. Helps with the "I can't
  // tell what I'm about to click" problem on dense layouts. Reset
  // to null on mouse-leave or when the cursor exits the seam stroke.
  const [hoveredSeamId, setHoveredSeamId] = useState<string | null>(null);
  // Mirror ``seamDrag.current`` into React state so the renderer can
  // disable piece pointer-events for the whole duration of a drag —
  // a ref alone doesn't trigger a rerender.
  const [isSeamDragging, setIsSeamDragging] = useState(false);
  // Live HUD for the seam being dragged: the current target position
  // (after snap+clamp) plus the resulting dimensions of the two
  // adjacent pieces. Lets the designer see "left = 142 cm, right =
  // 176 cm" in real time without waiting for the panel to update.
  const [seamHud, setSeamHud] = useState<{
    seamId: string;
    orientation: "vertical" | "horizontal";
    position: number;
    leftLabel: string;
    rightLabel: string;
  } | null>(null);

  const seams = useMemo(() => deriveSeams(pieces), [pieces]);

  // 0.1.46 — duplicate detection for Step-4 canvas fill colouring.
  // The pieces panel computes the same set with its own
  // ``detectDuplicateSlabs`` helper; the canvas re-derives it
  // locally to avoid threading another prop through. Cheap O(N).
  const duplicateSlabIds = useMemo(() => {
    if (!assignments) return new Set<string>();
    const counts = new Map<string, number>();
    for (const v of Object.values(assignments)) {
      if (!v) continue;
      counts.set(v, (counts.get(v) ?? 0) + 1);
    }
    const dups = new Set<string>();
    for (const [id, n] of counts) if (n > 1) dups.add(id);
    return dups;
  }, [assignments]);

  // Belt-and-braces: if the canvas unmounts (step change, navigation)
  // while a seam drag is in flight, clear the document-scroll lock.
  useEffect(() => {
    return () => {
      document.documentElement.classList.remove("dragging-seam");
      document.body.classList.remove("dragging-seam");
    };
  }, []);

  const r1PieceIds = useMemo(
    () => new Set(
      validation?.pieces.filter((p) => p.is_below_min).map((p) => p.piece_id)
        ?? [],
    ),
    [validation],
  );
  const r2SeamPairKeys = useMemo(
    () => new Set(
      (validation?.seams ?? [])
        .filter((s) => s.crosses_doorways.length > 0)
        .map((s) => pairKey(s.piece_a_id, s.piece_b_id)),
    ),
    [validation],
  );

  const resetView = useCallback(() => setBox(initialBox), [initialBox]);

  // --- zoom via toolbar buttons --------------------------------------------
  // The previous implementation listened on the wheel, which was
  // easy to trigger by accident while scrolling the page near the
  // canvas. Explicit buttons make the action discoverable and
  // remove that accidental-zoom failure mode. Zoom centres on the
  // current viewBox centre (consistent: clicking "+" always zooms
  // into whatever's already centred).
  const zoomIn = useCallback(
    () => setBox((cur) => zoomAroundCentre(cur, ZOOM_IN_FACTOR)),
    [],
  );
  const zoomOut = useCallback(
    () => setBox((cur) => zoomAroundCentre(cur, ZOOM_OUT_FACTOR)),
    [],
  );

  // --- canvas-level pointer down: starts pan (select) or drawing (creation)
  const onPointerDownCanvas = useCallback(
    (e: React.PointerEvent<SVGSVGElement>) => {
      if (!svgRef.current) return;
      // A seam drag may have begun in the same tick (the seam-hit
      // handler runs before this one bubbles up). If so, the seam
      // owns the gesture — don't start a pan or clear the seam's
      // own selection.
      if (seamDrag.current) return;
      svgRef.current.setPointerCapture(e.pointerId);
      const pt = svgPoint(svgRef.current, e.clientX, e.clientY);

      if (isSelectionMode(mode)) {
        // Clear selection (nothing was clicked since selectable
        // children stop propagation), then start a pan. Both
        // selection modes — select_piece + edit_seam — share this
        // behaviour so the canvas pan UX feels identical between
        // them.
        onSelect(null);
        panState.current = {
          startX: e.clientX, startY: e.clientY, startBox: { ...box },
        };
        return;
      }

      // Creation modes — start drawing.
      setDrawing({
        mode: mode as Exclude<EditorMode, "select_piece" | "edit_seam">,
        start: pt,
        current: pt,
      });
    },
    [box, mode, onSelect],
  );

  const onPointerDownSeam = useCallback(
    (e: React.PointerEvent<SVGLineElement>, seam: Seam) => {
      // Strict mode gate — seams only respond when the designer
      // explicitly chose Edit Seams. In any other mode (including
      // Select Pieces) seams are inert.
      if (mode !== "edit_seam") return;
      // Block any other element (pieces, pan) from also reacting to
      // this pointerdown. The SVG paint order already puts seam hit
      // handles on top, but stopPropagation is the belt + braces.
      e.stopPropagation();
      e.preventDefault();
      if (!svgRef.current) return;
      svgRef.current.setPointerCapture(e.pointerId);
      onSelect({ kind: "seam", id: seam.seam_id });
      seamDrag.current = { seam, basePieces: pieces };
      setIsSeamDragging(true);
      setHoveredSeamId(null);
      // 0.1.42 — lock the document so nothing scrolls under the
      // drag (wheel, trackpad, touch, autoscroll near viewport
      // edges). The CSS rule for ``html.dragging-seam`` /
      // ``body.dragging-seam`` enforces overflow:hidden +
      // touch-action:none for the gesture's lifetime.
      document.documentElement.classList.add("dragging-seam");
      document.body.classList.add("dragging-seam");
    },
    [mode, onSelect, pieces],
  );

  const onPointerDownPlanObject = useCallback(
    (e: React.PointerEvent<SVGElement>, selection: Selection) => {
      // Piece clicks ONLY register in select_piece mode — even in
      // edit_seam mode, pieces are inert. Other plan annotations
      // (doorway/column/guide_line) remain selectable in either
      // selection mode since they're outside the piece-vs-seam
      // ambiguity this milestone is fixing.
      if (selection.kind === "piece") {
        if (mode !== "select_piece") return;
        // 0.1.53 — Manual swap intercept. When the designer has
        // toggled swap mode on, a press on a piece STARTS a drag
        // (rather than selecting). The actual swap fires on
        // pointer-up over the drop-target piece in onPointerUp.
        if (swapMode && onSwapAssignments) {
          if (!svgRef.current) return;
          e.stopPropagation();
          const pt = svgPoint(svgRef.current, e.clientX, e.clientY);
          // Pointer capture on the SVG root so pointermove/up keep
          // firing here even when the cursor leaves the source
          // polygon (or drops over an inert area).
          try {
            svgRef.current.setPointerCapture(e.pointerId);
          } catch { /* iOS may throw on a synthesised pointer */ }
          setSwapDrag({
            fromPieceId: selection.id,
            pointerId: e.pointerId,
            pos: pt,
            hoverPieceId: selection.id,
          });
          // Still select the source piece so the properties panel
          // shows context while the designer is mid-drag.
          onSelect(selection);
          return;
        }
      } else if (!isSelectionMode(mode)) {
        return;
      }
      e.stopPropagation();
      onSelect(selection);
    },
    [mode, onSelect, swapMode, onSwapAssignments],
  );

  // Hit-test which piece's REAL polygon bbox contains the given mm
  // point. Reading the polygon (rather than the nominal rect) makes
  // the swap drop target track edge-clipped strips correctly — a
  // piece's nominal bbox can extend into empty space beyond its cut.
  // Returns null when the cursor is over empty floor space.
  const pieceAtPoint = useCallback((pt: Point): string | null => {
    for (const p of pieces) {
      const bb = bboxFromPolygon(p.polygon);
      if (!bb) continue;
      if (pt[0] >= bb.x0 && pt[0] <= bb.x1
          && pt[1] >= bb.y0 && pt[1] <= bb.y1) {
        return p.piece_id;
      }
    }
    return null;
  }, [pieces]);

  const onPointerMove = useCallback(
    (e: React.PointerEvent<SVGSVGElement>) => {
      if (!svgRef.current) return;
      // 0.1.53 — manual swap drag. Update the floating chip position
      // and the live hover target for visual feedback.
      if (swapDrag) {
        const pt = svgPoint(svgRef.current, e.clientX, e.clientY);
        const hover = pieceAtPoint(pt);
        // Only re-render when the hover target actually changes —
        // pos updates every frame regardless because the chip
        // tracks the cursor.
        setSwapDrag((cur) => cur
          ? { ...cur, pos: pt, hoverPieceId: hover }
          : cur,
        );
        return;
      }
      // Seam drag has top priority.
      const sd = seamDrag.current;
      if (sd) {
        const pt = svgPoint(svgRef.current, e.clientX, e.clientY);
        const raw = sd.seam.orientation === "vertical" ? pt[0] : pt[1];
        const target = resolveTargetPosition(sd.seam, raw);
        const next = applySeamMove(sd.basePieces, sd.seam, target);
        onSeamDragMove(next);
        // Live HUD: pick one piece on each side of the seam and read
        // its NEW dimension in cm. With the 0.1.40 rebuild folded
        // into ``applySeamMove`` the new geometry is already in
        // ``next``, so this is a pure lookup.
        const leftId = sd.seam.piece_left_ids[0];
        const rightId = sd.seam.piece_right_ids[0];
        const leftPiece = leftId
          ? next.find((p) => p.piece_id === leftId) : undefined;
        const rightPiece = rightId
          ? next.find((p) => p.piece_id === rightId) : undefined;
        setSeamHud({
          seamId: sd.seam.seam_id,
          orientation: sd.seam.orientation,
          position: target,
          leftLabel: leftPiece ? pieceDimLabel(leftPiece) : "—",
          rightLabel: rightPiece ? pieceDimLabel(rightPiece) : "—",
        });
        return;
      }
      // Drawing in creation modes.
      if (drawing) {
        const pt = svgPoint(svgRef.current, e.clientX, e.clientY);
        setDrawing({ ...drawing, current: pt });
        return;
      }
      // Pan in select mode.
      //
      // We translate cursor pixels → viewBox units using the
      // screen-to-local scale derived from getScreenCTM, so the
      // pan matches cursor motion 1:1 even when preserveAspectRatio
      // letterboxes the viewBox inside the SVG element. The old
      // implementation divided box.w / rect.width which only
      // worked when the viewBox and element shared an aspect ratio.
      const ps = panState.current;
      if (!ps) return;
      const scale = screenPixelsPerLocalUnit(svgRef.current);
      // Pan doesn't change box.w/box.h, so the scale is constant
      // throughout the drag — reading it now is correct.
      const dxLocal = (e.clientX - ps.startX) / scale;
      const dyLocal = (e.clientY - ps.startY) / scale;
      setBox({
        x: ps.startBox.x - dxLocal,
        y: ps.startBox.y + dyLocal,
        w: ps.startBox.w,
        h: ps.startBox.h,
      });
    },
    [drawing, onSeamDragMove, swapDrag, pieceAtPoint],
  );

  const onPointerUp = useCallback(
    (e: React.PointerEvent<SVGSVGElement>) => {
      // 0.1.53 — manual swap completion. Fire the swap if the drop
      // landed on a different piece; otherwise treat as a click
      // (no-op for assignments, source piece stays selected).
      if (swapDrag && e.pointerId === swapDrag.pointerId) {
        let dropPt: Point;
        if (svgRef.current) {
          dropPt = svgPoint(svgRef.current, e.clientX, e.clientY);
        } else {
          dropPt = swapDrag.pos;
        }
        const target = pieceAtPoint(dropPt);
        if (target && target !== swapDrag.fromPieceId) {
          onSwapAssignments?.(swapDrag.fromPieceId, target);
        }
        setSwapDrag(null);
        try {
          svgRef.current?.releasePointerCapture(e.pointerId);
        } catch { /* already released */ }
        return;
      }
      const wasSeamDragging = seamDrag.current !== null;
      seamDrag.current = null;
      panState.current = null;
      if (wasSeamDragging) {
        setIsSeamDragging(false);
        setSeamHud(null);
        // Release the document-scroll lock added in
        // ``onPointerDownSeam``. Done unconditionally on pointer up
        // so an interrupted drag (focus loss, alt-tab) doesn't leave
        // the page frozen.
        document.documentElement.classList.remove("dragging-seam");
        document.body.classList.remove("dragging-seam");
      }
      svgRef.current?.releasePointerCapture(e.pointerId);

      // Commit any in-flight drawing.
      if (drawing) {
        const d = drawing;
        setDrawing(null);
        commitDrawing(
          d, pieces,
          { onAddDoorway, onAddColumn, onAddGuideLine, onAddSeam },
        );
      }

      if (wasSeamDragging) onSeamDragEnd();
    },
    [drawing, pieces, onAddColumn, onAddDoorway, onAddGuideLine,
     onAddSeam, onSeamDragEnd, swapDrag, pieceAtPoint, onSwapAssignments],
  );

  // Selection lookups for canvas highlight.
  const selectedDoorwayId = selection?.kind === "doorway" ? selection.id : null;
  const selectedColumnId = selection?.kind === "column" ? selection.id : null;
  const selectedGuideId = selection?.kind === "guide_line" ? selection.id : null;
  const selectedSeamId = selection?.kind === "seam" ? selection.id : null;
  const selectedPieceId = selection?.kind === "piece" ? selection.id : null;

  return (
    <div className="canvas-wrap">
      <div className="canvas-toolbar">
        <span className="target-label">
          {layout.target.name || layout.target.target_id}
        </span>
        <span className="piece-count">{pieces.length} pieces</span>
        {/* Working slab label — the median (or explicit-override)
            tile size in cm, plus where it came from. Drops back to
            the layout's intrinsic tile dims when no explicit
            regeneration has happened yet, so a fresh boot still
            shows a slab size. */}
        <span
          className="working-slab"
          title={
            tileChoice?.basis === "explicit_override"
              ? "Custom tile size (Step 3 override)"
              : tileChoice?.basis === "inventory_median"
                ? "Median width × height from uploaded inventory"
                : "Layout's default tile size"
          }
        >
          working slab&nbsp;
          <strong>
            {Math.round(
              (tileChoice?.tile_width_mm ?? layout.grid.tile_width_mm) / 10,
            )}
            {" × "}
            {Math.round(
              (tileChoice?.tile_height_mm ?? layout.grid.tile_height_mm) / 10,
            )}
            {" cm"}
          </strong>
          {tileChoice && (
            <span className="working-slab-basis">
              {" "}·{" "}
              {tileChoice.basis === "explicit_override"
                ? "custom"
                : "from inventory"}
            </span>
          )}
        </span>
        <div className="canvas-zoom-controls">
          <button
            type="button"
            className="zoom-btn"
            onClick={zoomOut}
            title="Zoom out"
            aria-label="Zoom out"
          >
            −
          </button>
          <button
            type="button"
            className="zoom-btn"
            onClick={zoomIn}
            title="Zoom in"
            aria-label="Zoom in"
          >
            +
          </button>
          <button type="button" onClick={resetView} className="reset-btn">
            Reset view
          </button>
        </div>
        <span className="hint">drag canvas to pan</span>
      </div>
      <svg
        ref={svgRef}
        className={`canvas-svg canvas-mode-${mode}`}
        viewBox={`${box.x} ${box.y} ${box.w} ${box.h}`}
        preserveAspectRatio="xMidYMid meet"
        onPointerDown={onPointerDownCanvas}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerCancel={onPointerUp}
      >
        <g transform="scale(1 -1)">
          {/* Spaces background. */}
          {plan.spaces.map((s) => (
            <polygon
              key={`space:${s.space_id}`}
              points={ptsToString(s.polygon)}
              fill={STYLES.space}
              opacity={STYLES.spaceOpacity}
              stroke="transparent"
            />
          ))}

          {/* Holes. */}
          {layout.target.holes.map((hole, i) => (
            <polygon
              key={`hole:${i}`}
              points={ptsToString(hole)}
              fill={STYLES.hole}
              stroke={STYLES.holeStroke}
              strokeWidth={STYLES.pieceWidth}
              vectorEffect="non-scaling-stroke"
            />
          ))}

          {/* Slab-image patterns — one per assigned piece with an
              on-disk photo. Used as ``fill="url(#…)"`` on the
              piece polygon below so the slab visually fills the cut
              shape. patternUnits="userSpaceOnUse" anchors the image
              to the piece's bbox in viewBox coordinates; the inner
              counter-flip is needed because the outer
              <g transform="scale(1 -1)"> would otherwise render the
              image upside-down. */}
          <defs>
            {pieces.map((p) => {
              const slabId = assignments?.[p.piece_id];
              if (!slabId) return null;
              if (!candidateImagePath(inventoryMatch, p.piece_id, slabId)) {
                return null;
              }
              const w = p.nominal_width_mm;
              const h = p.nominal_height_mm;
              const rotated = !!candidateRotation(
                inventoryMatch, p.piece_id, slabId,
              );
              // 0.1.47 — small 0.5% overscale soaks up the
              // sub-pixel rounding between the polygon and the
              // pattern's bbox, so adjacent assigned pieces don't
              // show a hairline white seam between them. The crop
              // pass already produced an aspect-correct image, so
              // slice still fills cleanly. Rotated slabs swap
              // image w/h inside the pattern and then rotate 90°
              // around the centre.
              const pad = 0.005;
              const iw = w * (1 + pad * 2);
              const ih = h * (1 + pad * 2);
              const ox = -w * pad;
              const oy = -h * pad;
              return (
                <pattern
                  key={`pat:${p.piece_id}`}
                  id={`slab-pat-${cssId(p.piece_id)}`}
                  patternUnits="userSpaceOnUse"
                  x={p.nominal_x_mm} y={p.nominal_y_mm}
                  width={w} height={h}
                >
                  <g transform={`translate(0 ${h}) scale(1 -1)`}>
                    {rotated ? (
                      // Rotate the image 90° around the piece centre,
                      // swap the image's width/height so its aspect
                      // matches what the slab covers after rotation.
                      <g transform={`rotate(90 ${w / 2} ${h / 2})`}>
                        <image
                          href={`/api/inventory/slab-image/${encodeURIComponent(slabId)}?crop=safe-area`}
                          x={(w - h) / 2 - h * pad}
                          y={(h - w) / 2 - w * pad}
                          width={h * (1 + pad * 2)}
                          height={w * (1 + pad * 2)}
                          preserveAspectRatio="xMidYMid slice"
                        />
                      </g>
                    ) : (
                      <image
                        href={`/api/inventory/slab-image/${encodeURIComponent(slabId)}?crop=safe-area`}
                        x={ox} y={oy}
                        width={iw} height={ih}
                        preserveAspectRatio="xMidYMid slice"
                      />
                    )}
                  </g>
                </pattern>
              );
            })}
          </defs>

          {/* Pieces. Clickable in select mode so the properties panel
              can show width/height/area/full-tile status. The visible
              piece polygon stays the same shape; selection is shown
              by upgrading the stroke colour + width to the editor's
              selection blue. */}
          {pieces.map((p) => {
            const isAbsorbed = (p.notes || []).some((n) =>
              n.startsWith("absorbed_sliver:"),
            );
            const isR1 = r1PieceIds.has(p.piece_id);
            const isSelected = p.piece_id === selectedPieceId;
            // Slab-image fill takes priority when an assigned slab
            // has an on-disk photo. R1 / absorbed colouring still
            // wins when no slab image is available — they remain
            // actionable warnings the designer needs to see.
            const assignedSlabId = assignments?.[p.piece_id];
            const slabImageAvailable = !!assignedSlabId
              && !!candidateImagePath(
                inventoryMatch, p.piece_id, assignedSlabId,
              );
            const matchStatus = inventoryMatch?.pieces.find(
              (pm) => pm.piece_id === p.piece_id,
            )?.status;
            const isDuplicateAssigned = !!assignedSlabId
              && (duplicateSlabIds?.has(assignedSlabId) ?? false);
            // 0.1.53 — manual swap visuals + post-swap validation.
            //   * ``isInvalidAssignment`` flags pieces whose current
            //     slab is too small (chip + canvas ring stay red until
            //     the conflict is resolved).
            //   * ``isSwapSource`` / ``isSwapHover`` paint the in-flight
            //     drag so the designer can see what they're moving
            //     and where they'd drop it.
            const isInvalidAssignment = invalidPieceIds?.has(p.piece_id)
              ?? false;
            const isSwapSource = swapDrag?.fromPieceId === p.piece_id;
            const isSwapHover = swapDrag !== null
              && swapDrag.hoverPieceId === p.piece_id
              && swapDrag.fromPieceId !== p.piece_id;
            // Pick the tinted fill for the "assigned without photo"
            // and "no-match" and "duplicate" cases so the legend
            // chips in Step 4 line up with what the canvas shows.
            // Validation colours (R1 / absorbed) still beat them.
            const assignmentTint =
              isDuplicateAssigned ? STYLES.pieceFaceDuplicate
              : assignedSlabId ? STYLES.pieceFaceAssignedNoImg
              : matchStatus === "no_match" ? STYLES.pieceFaceNoMatch
              : null;
            const fill = slabImageAvailable
              ? `url(#slab-pat-${cssId(p.piece_id)})`
              : isR1
                ? STYLES.pieceFaceR1
                : isAbsorbed
                  ? STYLES.pieceFaceAbsorbed
                  : assignmentTint ?? STYLES.pieceFace;
            // In edit_seam mode, mute the normal piece border so the
            // seam lines win the visual hierarchy. Validation
            // colours (R1 red, absorbed amber) still take priority
            // because they're still actionable warnings — the mute
            // is for the default-state border only.
            const defaultEdge = mode === "edit_seam"
              ? STYLES.pieceEdgeMuted : STYLES.pieceEdge;
            // Stroke priority (most → least important):
            //   1. invalid-after-swap red ring — must always be loud
            //   2. swap-drag hover / source highlight
            //   3. user selection
            //   4. R1 / absorbed callouts
            //   5. default edge
            const isDropTarget = dropTargetPieceId === p.piece_id;
            const stroke = isDropTarget
              ? STYLES.selectedStroke
              : isInvalidAssignment
                ? STYLES.pieceFaceNoMatchEdge
                : isSwapHover
                  ? STYLES.selectedStroke
                  : isSwapSource
                    ? STYLES.selectedStroke
                    : isSelected
                      ? STYLES.selectedStroke
                      : isR1
                        ? STYLES.pieceFaceR1Edge
                        : isAbsorbed
                          ? STYLES.pieceFaceAbsorbedEdge
                          : defaultEdge;
            const strokeWidth =
              isInvalidAssignment || isDropTarget
                ? STYLES.selectedWidth
              : (isSwapSource || isSwapHover || isSelected)
                ? STYLES.selectedWidth
                : STYLES.pieceWidth;
            // Dashed border on the swap source so the designer can
            // tell which piece they're dragging FROM at a glance.
            // Drop-target pieces get a dashed border too as a live
            // "you can release here" affordance.
            const strokeDash = isDropTarget
              ? "8 6"
              : isSwapSource ? "6 6" : undefined;
            return (
              <polygon
                key={`piece:${p.piece_id}`}
                points={ptsToString(p.polygon)}
                fill={fill}
                stroke={stroke}
                strokeWidth={strokeWidth}
                strokeDasharray={strokeDash}
                vectorEffect="non-scaling-stroke"
                style={{
                  // Pieces ONLY respond in select_piece mode. In
                  // edit_seam mode the cursor stays default (or the
                  // seam's resize cursor when over a seam hit
                  // handle) and click events fall through to the
                  // canvas, which is exactly the spec ("editor
                  // should behave as if pieces do not exist").
                  // Swap mode upgrades the cursor so the designer
                  // can see that pieces are now draggable.
                  cursor: mode === "select_piece"
                    ? (swapMode ? "grab" : "pointer")
                    : "default",
                  pointerEvents: mode === "select_piece" ? "auto" : "none",
                }}
                onPointerDown={(e) =>
                  onPointerDownPlanObject(e, { kind: "piece", id: p.piece_id })
                }
                onDragOver={(e) => {
                  // Accept only our own drag payloads.
                  const types = Array.from(e.dataTransfer.types);
                  if (!types.includes("application/x-stonelayout")) return;
                  e.preventDefault();
                  e.dataTransfer.dropEffect = "copy";
                  if (dropTargetPieceId !== p.piece_id) {
                    setDropTargetPieceId(p.piece_id);
                  }
                }}
                onDragLeave={() => {
                  if (dropTargetPieceId === p.piece_id) {
                    setDropTargetPieceId(null);
                  }
                }}
                onDrop={(e) => {
                  e.preventDefault();
                  setDropTargetPieceId(null);
                  const raw = e.dataTransfer.getData(
                    "application/x-stonelayout",
                  );
                  if (!raw) return;
                  try {
                    const payload = JSON.parse(raw);
                    if (payload.kind === "slab" && payload.slab_id) {
                      onAssignSlabToPiece?.(p.piece_id, payload.slab_id);
                    } else if (payload.kind === "piece" && payload.piece_id) {
                      // Piece-to-piece drop = swap.
                      if (payload.piece_id !== p.piece_id) {
                        onSwapAssignments?.(payload.piece_id, p.piece_id);
                      }
                    }
                  } catch { /* ignore */ }
                }}
              />
            );
          })}

          {/* Selected-piece label — small piece_id near centroid.
              The outer <g> flips y; counter-flipping the text keeps
              it upright. ``vector-effect=non-scaling-stroke`` isn't
              valid on text so we don't use it here; the text scales
              with the viewBox along with everything else, which
              actually makes it legible when zoomed in. */}
          {selectedPieceId && (() => {
            const sp = pieces.find((p) => p.piece_id === selectedPieceId);
            if (!sp) return null;
            // Anchor the label to the REAL polygon centre — for edge
            // clips the nominal centre sits outside the visible shape
            // and the label would float over empty floor space.
            const bb = bboxFromPolygon(sp.polygon);
            const cx = bb
              ? (bb.x0 + bb.x1) / 2
              : sp.nominal_x_mm + sp.nominal_width_mm / 2;
            const cy = bb
              ? (bb.y0 + bb.y1) / 2
              : sp.nominal_y_mm + sp.nominal_height_mm / 2;
            return (
              <g transform={`translate(${cx} ${cy}) scale(1 -1)`}>
                <text
                  x="0" y="0"
                  textAnchor="middle"
                  dominantBaseline="middle"
                  fontSize={STYLES.selectedPieceLabelSize}
                  fill={STYLES.selectedStroke}
                  fontWeight={600}
                  style={{ pointerEvents: "none" }}
                >
                  {sp.piece_id}
                </text>
              </g>
            );
          })()}

          {/* Seams — visible line. Coloured by state (R2 / selected /
              hovered / boundary / interior). Drawn here so the line
              renders on top of pieces; the WIDE invisible hit handle
              is rendered later in the SVG so it sits on top of the
              boundary + columns + everything else. */}
          {seams.map((seam) => {
            const isSelected = seam.seam_id === selectedSeamId;
            const isHovered = seam.seam_id === hoveredSeamId;
            const isBad = seamHasR2(seam, r2SeamPairKeys);
            // In edit_seam mode, every seam is rendered prominently
            // (stronger blue, thicker line) so the designer
            // immediately reads the canvas as "I'm editing seams".
            // In other modes seams stay visually secondary.
            const seamsAreActive = mode === "edit_seam";
            const color = isBad
              ? STYLES.seamLineR2
              : isSelected
                ? STYLES.seamLineSelected
                : isHovered
                  ? STYLES.seamLineHover
                  : seamsAreActive
                    ? STYLES.seamLineActive
                    : seam.is_boundary
                      ? STYLES.seamLineBoundary
                      : STYLES.seamLine;
            const width = isSelected
              ? STYLES.seamLineSelectedWidth
              : seamsAreActive
                ? STYLES.seamLineActiveWidth
                : (isHovered || seam.is_boundary)
                  ? STYLES.seamLineBoundaryWidth
                  : STYLES.seamLineWidth;
            const [x1, y1, x2, y2] = seamEndpoints(seam);
            return (
              <line
                key={`seam:${seam.seam_id}`}
                x1={x1} y1={y1} x2={x2} y2={y2}
                stroke={color} strokeWidth={width}
                vectorEffect="non-scaling-stroke"
                strokeLinecap="butt"
                style={{ pointerEvents: "none" }}
              />
            );
          })}

          {/* Target boundary on top of pieces. */}
          <polygon
            points={ptsToString(layout.target.boundary)}
            fill="none"
            stroke={STYLES.boundary}
            strokeWidth={STYLES.boundaryWidth}
            vectorEffect="non-scaling-stroke"
          />

          {/* Columns. */}
          {plan.columns.map((c) => {
            const isSelected = c.column_id === selectedColumnId;
            return (
              <g key={`col:${c.column_id}`}>
                <polygon
                  points={ptsToString(c.polygon)}
                  fill={STYLES.column}
                  stroke={isSelected ? STYLES.selectedStroke : STYLES.columnStroke}
                  strokeWidth={isSelected ? STYLES.selectedWidth : STYLES.columnWidth}
                  vectorEffect="non-scaling-stroke"
                  style={{
                    cursor: isSelectionMode(mode) ? "pointer" : "default",
                    pointerEvents: isSelectionMode(mode) ? "auto" : "none",
                  }}
                  onPointerDown={(e) =>
                    onPointerDownPlanObject(e, { kind: "column", id: c.column_id })
                  }
                />
              </g>
            );
          })}

          {/* Doorways. */}
          {plan.doorways.map((d) => {
            const isSelected = d.doorway_id === selectedDoorwayId;
            return (
              <g key={`door:${d.doorway_id}`}>
                <line
                  x1={d.segment[0][0]} y1={d.segment[0][1]}
                  x2={d.segment[1][0]} y2={d.segment[1][1]}
                  stroke={
                    isSelected ? STYLES.selectedStroke
                      : d.is_main_entrance ? STYLES.doorwayMain : STYLES.doorway
                  }
                  strokeWidth={
                    isSelected
                      ? STYLES.selectedWidth + STYLES.doorwayWidth / 4
                      : STYLES.doorwayWidth
                  }
                  strokeLinecap="butt"
                  vectorEffect="non-scaling-stroke"
                />
                <line
                  x1={d.segment[0][0]} y1={d.segment[0][1]}
                  x2={d.segment[1][0]} y2={d.segment[1][1]}
                  stroke="transparent"
                  strokeWidth={DOORWAY_HIT_WIDTH}
                  vectorEffect="non-scaling-stroke"
                  style={{
                    cursor: isSelectionMode(mode) ? "pointer" : "default",
                    pointerEvents: isSelectionMode(mode) ? "stroke" : "none",
                  }}
                  onPointerDown={(e) =>
                    onPointerDownPlanObject(e, { kind: "doorway", id: d.doorway_id })
                  }
                />
              </g>
            );
          })}

          {/* Guide lines. */}
          {plan.guide_lines.map((g) => {
            const isSelected = g.guide_line_id === selectedGuideId;
            return (
              <g key={`guide:${g.guide_line_id}`}>
                <line
                  x1={g.segment[0][0]} y1={g.segment[0][1]}
                  x2={g.segment[1][0]} y2={g.segment[1][1]}
                  stroke={isSelected ? STYLES.selectedStroke : STYLES.guide}
                  strokeWidth={isSelected ? STYLES.selectedWidth : 2}
                  strokeDasharray="12 8"
                  vectorEffect="non-scaling-stroke"
                />
                <line
                  x1={g.segment[0][0]} y1={g.segment[0][1]}
                  x2={g.segment[1][0]} y2={g.segment[1][1]}
                  stroke="transparent"
                  strokeWidth={GUIDE_HIT_WIDTH}
                  vectorEffect="non-scaling-stroke"
                  style={{
                    cursor: isSelectionMode(mode) ? "pointer" : "default",
                    pointerEvents: isSelectionMode(mode) ? "stroke" : "none",
                  }}
                  onPointerDown={(e) =>
                    onPointerDownPlanObject(e, {
                      kind: "guide_line", id: g.guide_line_id,
                    })
                  }
                />
              </g>
            );
          })}

          {/* Seam hit handles — rendered LAST so they sit on top of
              every piece, every plan annotation, and the boundary.
              Pointer events are limited to ``stroke`` so the wide
              transparent strip only catches clicks ON the line, not
              over the whole bounding rectangle. */}
          {mode === "edit_seam" && seams.map((seam) => {
            const [x1, y1, x2, y2] = seamEndpoints(seam);
            return (
              <line
                key={`seam-hit:${seam.seam_id}`}
                x1={x1} y1={y1} x2={x2} y2={y2}
                stroke="transparent"
                strokeWidth={SEAM_HIT_WIDTH}
                vectorEffect="non-scaling-stroke"
                style={{
                  cursor: seam.orientation === "vertical"
                    ? "ew-resize" : "ns-resize",
                  pointerEvents: "stroke",
                }}
                onPointerDown={(e) => onPointerDownSeam(e, seam)}
                onPointerEnter={() => {
                  if (!isSeamDragging) setHoveredSeamId(seam.seam_id);
                }}
                onPointerLeave={() => {
                  setHoveredSeamId((cur) =>
                    cur === seam.seam_id ? null : cur,
                  );
                }}
              />
            );
          })}

          {/* Alignment guides — render the positions of all OTHER
              seams of the same orientation while a drag is in
              flight. Helps the designer line up the dragged seam
              with an existing cut without snapping. Drawn after
              the seams so they sit on top of the muted layer; the
              dragged seam itself stays in its selected blue. */}
          {seamHud && (() => {
            const isVertical = seamHud.orientation === "vertical";
            const [bx, by, bw, bh] = [box.x, box.y, box.w, box.h];
            return (
              <g style={{ pointerEvents: "none" }}>
                {seams
                  .filter(
                    (s) =>
                      s.orientation === seamHud.orientation
                      && s.seam_id !== seamHud.seamId,
                  )
                  .map((s) => {
                    const aligned = Math.abs(s.position - seamHud.position) < 1;
                    return isVertical ? (
                      <line
                        key={`align:${s.seam_id}`}
                        x1={s.position} y1={by}
                        x2={s.position} y2={by + bh}
                        stroke={aligned ? STYLES.seamLineSelected : "#cfd9e8"}
                        strokeWidth={aligned ? 2 : 1}
                        strokeDasharray={aligned ? "0" : "4 4"}
                        vectorEffect="non-scaling-stroke"
                      />
                    ) : (
                      <line
                        key={`align:${s.seam_id}`}
                        x1={bx} y1={s.position}
                        x2={bx + bw} y2={s.position}
                        stroke={aligned ? STYLES.seamLineSelected : "#cfd9e8"}
                        strokeWidth={aligned ? 2 : 1}
                        strokeDasharray={aligned ? "0" : "4 4"}
                        vectorEffect="non-scaling-stroke"
                      />
                    );
                  })}
              </g>
            );
          })()}

          {/* Drawing preview (in-flight new object). */}
          {drawing && <DrawingPreview drawing={drawing} pieces={pieces} />}
        </g>
      </svg>

      {/* Live seam HUD — DOM overlay so the labels stay readable
          regardless of zoom and don't have to deal with SVG y-flip.
          Anchored to the corner so it's never under the cursor and
          never clipped by the canvas edge. */}
      {seamHud && (
        <div className="seam-hud">
          <div className="seam-hud-title">
            {seamHud.orientation === "vertical" ? "Vertical seam" : "Horizontal seam"}
            {" "}@ {(seamHud.position / 10).toFixed(1)} cm
          </div>
          <div className="seam-hud-row">
            <span className="seam-hud-label">
              {seamHud.orientation === "vertical" ? "Left piece" : "Bottom piece"}
            </span>
            <span className="seam-hud-value">{seamHud.leftLabel}</span>
          </div>
          <div className="seam-hud-row">
            <span className="seam-hud-label">
              {seamHud.orientation === "vertical" ? "Right piece" : "Top piece"}
            </span>
            <span className="seam-hud-value">{seamHud.rightLabel}</span>
          </div>
        </div>
      )}

      {/* 0.1.53 — swap mode HUD. A persistent banner explains the
          mode is active; while a drag is in flight, a status line
          surfaces the source + drop-target so the designer can see
          the operation before releasing the pointer. */}
      {swapMode && (
        <div className="swap-mode-banner" role="status">
          <strong>Swap slabs</strong> · drag a piece onto another to
          swap their assigned slabs
          {swapDrag && (
            <>
              <span className="swap-mode-sep">·</span>
              <span className="swap-mode-trace">
                {swapDrag.fromPieceId}
                {" → "}
                {swapDrag.hoverPieceId && swapDrag.hoverPieceId !== swapDrag.fromPieceId
                  ? swapDrag.hoverPieceId
                  : "drop on another piece"}
              </span>
            </>
          )}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// drawing preview + commit
// ---------------------------------------------------------------------------

function DrawingPreview(
  { drawing, pieces }: { drawing: DrawingState; pieces: Piece[] },
) {
  const { start, current } = drawing;
  if (drawing.mode === "column") {
    const poly = rectanglePolygon(start, current);
    return (
      <polygon
        points={poly.map(([x, y]) => `${x},${y}`).join(" ")}
        fill="rgba(32, 80, 160, 0.12)"
        stroke="#2050a0"
        strokeWidth={3}
        strokeDasharray="8 6"
        vectorEffect="non-scaling-stroke"
      />
    );
  }
  if (drawing.mode === "add_seam") {
    // Show a SNAPPED guide line spanning the target zone so the
    // designer sees exactly where the seam will land. If the drag
    // didn't start inside a piece (rare — the bbox is forgiving)
    // we still draw the raw drag line so the cursor has feedback.
    const piece = findPieceAt(pieces, start);
    if (!piece) {
      return (
        <line
          x1={start[0]} y1={start[1]}
          x2={current[0]} y2={current[1]}
          stroke="#2050a0" strokeWidth={2} strokeDasharray="8 6"
          vectorEffect="non-scaling-stroke"
        />
      );
    }
    const orientation = inferOrientation(start, current);
    const position = seamPositionFromDrag(orientation, start, current);
    const bbox = zoneBbox(pieces, piece.zone_id);
    if (!bbox) return null;
    const [zx0, zy0, zx1, zy1] = bbox;
    const x1 = orientation === "vertical" ? position : zx0;
    const x2 = orientation === "vertical" ? position : zx1;
    const y1 = orientation === "vertical" ? zy0 : position;
    const y2 = orientation === "vertical" ? zy1 : position;
    return (
      <line
        x1={x1} y1={y1} x2={x2} y2={y2}
        stroke="#2050a0" strokeWidth={3} strokeDasharray="8 6"
        vectorEffect="non-scaling-stroke"
      />
    );
  }
  // Doorway / guide-line previews are both line segments.
  return (
    <line
      x1={start[0]} y1={start[1]}
      x2={current[0]} y2={current[1]}
      stroke="#2050a0"
      strokeWidth={drawing.mode === "doorway" ? 6 : 3}
      strokeDasharray="8 6"
      vectorEffect="non-scaling-stroke"
    />
  );
}

interface CommitCallbacks {
  onAddDoorway: (segment: [Point, Point]) => void;
  onAddColumn: (polygon: Point[]) => void;
  onAddGuideLine: (segment: [Point, Point]) => void;
  onAddSeam: (args: {
    orientation: "vertical" | "horizontal";
    position: number;
    zone_id: string;
  }) => void;
}

function commitDrawing(
  d: DrawingState, pieces: Piece[], cb: CommitCallbacks,
): void {
  const dx = d.current[0] - d.start[0];
  const dy = d.current[1] - d.start[1];
  if (d.mode === "column") {
    if (Math.abs(dx) < MIN_DRAW_LENGTH_MM || Math.abs(dy) < MIN_DRAW_LENGTH_MM) {
      return;
    }
    cb.onAddColumn(rectanglePolygon(d.start, d.current));
    return;
  }
  if (d.mode === "add_seam") {
    // Need a drag of at least MIN_DRAW_LENGTH_MM in either axis
    // before we'll commit a seam — guards against accidental clicks.
    if (Math.abs(dx) < MIN_DRAW_LENGTH_MM && Math.abs(dy) < MIN_DRAW_LENGTH_MM) {
      return;
    }
    const piece = findPieceAt(pieces, d.start);
    if (!piece) return;  // drag started outside every piece — bail
    const orientation = inferOrientation(d.start, d.current);
    const position = seamPositionFromDrag(orientation, d.start, d.current);
    cb.onAddSeam({ orientation, position, zone_id: piece.zone_id });
    return;
  }
  const length = Math.sqrt(dx * dx + dy * dy);
  if (length < MIN_DRAW_LENGTH_MM) return;
  const segment: [Point, Point] = [d.start, d.current];
  if (d.mode === "doorway") cb.onAddDoorway(segment);
  else cb.onAddGuideLine(segment);
}

// ---------------------------------------------------------------------------
// math helpers (shared with the seam-editing milestone)
// ---------------------------------------------------------------------------

function seamEndpoints(seam: Seam): [number, number, number, number] {
  if (seam.orientation === "vertical") {
    return [seam.position, seam.range[0], seam.position, seam.range[1]];
  }
  return [seam.range[0], seam.position, seam.range[1], seam.position];
}

function pairKey(a: string, b: string): string {
  return a < b ? `${a}|${b}` : `${b}|${a}`;
}

function seamHasR2(seam: Seam, badPairKeys: Set<string>): boolean {
  for (const left of seam.piece_left_ids) {
    for (const right of seam.piece_right_ids) {
      if (badPairKeys.has(pairKey(left, right))) return true;
    }
  }
  return false;
}

function svgPoint(
  svg: SVGSVGElement, clientX: number, clientY: number,
): Point {
  // Use the browser's getScreenCTM (current transformation matrix
  // from local user units to screen pixels) so the conversion
  // accounts for preserveAspectRatio="xMidYMid meet" letterboxing.
  //
  // The old implementation used `clientX / rect.width` which treats
  // the entire SVG element as the visible viewBox area. With "meet"
  // fitting, the actual viewBox content is centred inside the
  // element with letterbox padding on the longer axis; that
  // padding made the cursor->engine conversion drift away from
  // the rendered geometry by however many pixels of padding
  // appeared, which the user observed as the seam not following
  // the cursor on drag.
  //
  // getScreenCTM gives us local→screen; inverse() gives screen→local.
  // The local frame is the viewBox space; engine coords differ from
  // viewBox coords by the scale(1 -1) on the inner <g>, so we
  // negate y at the end.
  const ctm = svg.getScreenCTM();
  if (!ctm) return [0, 0];
  const pt = svg.createSVGPoint();
  pt.x = clientX;
  pt.y = clientY;
  const local = pt.matrixTransform(ctm.inverse());
  return [local.x, -local.y];
}

/** Screen-pixels-per-viewBox-unit at the moment of the call. Used by
 *  the pan handler to translate a cursor delta into a viewBox shift
 *  correctly under preserveAspectRatio letterboxing. With "meet"
 *  fitting the same scale applies to both axes — we return one
 *  number rather than two. */
function screenPixelsPerLocalUnit(svg: SVGSVGElement): number {
  const ctm = svg.getScreenCTM();
  // ctm.a is the x-axis scale (screen px per local unit). For "meet"
  // fitting, ctm.a === ctm.d. Default to 1 only if the element
  // isn't laid out yet — never expected at drag time.
  return ctm && ctm.a !== 0 ? ctm.a : 1;
}

/** Toolbar zoom factors. ``IN`` shrinks the viewBox (closer view);
 *  ``OUT`` grows it. The 1.20× / 0.833× pair is a single zoom level
 *  per click — enough motion to feel responsive without overshooting.
 *  These are intentionally constants (not props) — they're a UX
 *  decision that lives next to the buttons that use them. */
const ZOOM_IN_FACTOR = 1 / 1.2;
const ZOOM_OUT_FACTOR = 1.2;

/** Zoom around the current viewBox centre. Wheel-zoom used to
 *  anchor on the cursor, but the toolbar buttons have no cursor
 *  position to anchor to — keeping the centre fixed gives the
 *  designer a predictable "zooms into the middle of what I'm
 *  looking at" feel. */
function zoomAroundCentre(cur: Box, factor: number): Box {
  const cx = cur.x + cur.w / 2;
  const cy = cur.y + cur.h / 2;
  const newW = cur.w * factor;
  const newH = cur.h * factor;
  return {
    x: cx - newW / 2,
    y: cy - newH / 2,
    w: newW,
    h: newH,
  };
}

function ptsToString(pts: Point[]): string {
  return pts.map(([x, y]) => `${x},${y}`).join(" ");
}

/** "159 × 220 cm" style label for a piece — used by the live seam
 *  HUD so the designer sees the resulting slab size while dragging.
 *  Reads from the polygon (real cut bbox) so edge-clipped strips
 *  show their real width instead of the working slab size. */
function pieceDimLabel(piece: Piece): string {
  const cut = cutDimsForPiece(piece);
  const w = (cut.width_mm / 10).toFixed(0);
  const h = (cut.height_mm / 10).toFixed(0);
  return `${w} × ${h} cm`;
}

/** Look up whether the slab assigned to ``piece_id`` has an
 *  on-disk image in the current matcher response. Returns the
 *  image_path (truthy) or null/undefined. Cheap to call for every
 *  piece on render — matcher responses cap at top-3 per piece. */
function candidateImagePath(
  match: InventoryMatchResponse | null | undefined,
  piece_id: string,
  slab_id: string,
): string | null | undefined {
  if (!match) return null;
  const pm = match.pieces.find((p) => p.piece_id === piece_id);
  if (!pm) return null;
  const cand = pm.candidates.find((c) => c.slab_id === slab_id);
  return cand?.image_path ?? null;
}

/** Whether the matched candidate needs a 90° rotation to cover the
 *  piece. Used by the SVG pattern to rotate the cropped image so
 *  its aspect matches the piece bbox without distorting. */
function candidateRotation(
  match: InventoryMatchResponse | null | undefined,
  piece_id: string,
  slab_id: string,
): boolean {
  if (!match) return false;
  const pm = match.pieces.find((p) => p.piece_id === piece_id);
  if (!pm) return false;
  return !!pm.candidates.find((c) => c.slab_id === slab_id)?.rotation_needed;
}

/** Sanitise an arbitrary id (slab serials contain ``/`` and ``-``)
 *  into something safe to use as an SVG element id. CSS / SVG ids
 *  can't contain slashes, can't start with a digit, etc. */
function cssId(raw: string): string {
  return raw.replace(/[^A-Za-z0-9_-]/g, "_");
}
