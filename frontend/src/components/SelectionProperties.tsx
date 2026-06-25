// "Properties" card at the top of the right stack.
//
// One section that shows the relevant details for WHATEVER is
// currently selected — piece, seam, doorway, column, or guide line
// — so the designer always knows what they're looking at without
// hunting through expandable rows further down. Empty state
// ("Nothing selected · click a piece or seam") fills the slot when
// nothing is selected so the panel doesn't visually jump as
// selections come and go.
//
// V1 intentionally read-only:
//   * Doorway / column / guide line edits still live in the old
//     PropertiesPanel — when an annotation is selected we forward
//     to its editor form. Pieces and seams get a compact read-only
//     summary here.
// The editing UI for plan annotations is unchanged; only the
// pieces + seams summary is new in this milestone.

import { memo, useEffect, useState } from "react";

import { cutDimsForPiece } from "../lib/pieceGeom";
import { computePieceRisk } from "../lib/pieceRisk";
import type {
  EditorMode, InventoryMatchResponse, Layout, Piece, PieceMatchResult,
  Seam, Selection, ValidationResult,
} from "../lib/types";


interface Props {
  selection: Selection | null;
  pieces: Piece[];
  seams: Seam[];
  layout: Layout;
  validation: ValidationResult | null;
  inventoryMatch: InventoryMatchResponse | null;
  /** Drives what the panel can display. Per the 0.1.41 spec:
   *   * select_piece → only piece (and plan-annotation) selections
   *     ever surface here. Seam selections are filtered out.
   *   * edit_seam    → only seam selections surface; pieces are
   *     filtered out.
   *  Anything else (creation modes, Step 4) falls back to showing
   *  whatever's selected. */
  mode?: EditorMode;
  /** Optional Step-4 assignment lookup: piece_id → slab_id. When
   *  the selected piece is assigned AND the slab has an on-disk
   *  photo, the Properties card renders a large slab preview at
   *  the top so the designer / client can see the actual material
   *  picked for that cut. */
  assignments?: { [piece_id: string]: string | null };
}


function SelectionPropertiesImpl({
  selection, pieces, seams, layout, validation, inventoryMatch, mode,
  assignments,
}: Props) {
  // Spec: mode controls what the panel displays. Filter the
  // incoming selection accordingly so a stale piece/seam ID never
  // sneaks into the "wrong" mode's panel.
  const filtered: Selection | null = (() => {
    if (!selection) return null;
    if (mode === "select_piece" && selection.kind === "seam") return null;
    if (mode === "edit_seam" && selection.kind === "piece") return null;
    return selection;
  })();
  const emptyHint = mode === "edit_seam"
    ? "Nothing selected — click a seam on the canvas."
    : "Nothing selected — click a piece on the canvas.";

  let body: React.ReactNode;
  if (!filtered) {
    body = <div className="props-empty">{emptyHint}</div>;
  } else if (filtered.kind === "piece") {
    const piece = pieces.find((p) => p.piece_id === filtered.id) ?? null;
    body = piece
      ? <PieceBody
          piece={piece}
          layout={layout}
          validation={validation}
          inventoryMatch={inventoryMatch}
          assignedSlabId={assignments?.[piece.piece_id] ?? null}
        />
      : <div className="props-empty">Piece not in current layout.</div>;
  } else if (filtered.kind === "seam") {
    const seam = seams.find((s) => s.seam_id === filtered.id) ?? null;
    body = seam
      ? <SeamBody seam={seam} />
      : <div className="props-empty">Seam not in current layout.</div>;
  } else {
    // doorway / column / guide_line — the legacy PropertiesPanel
    // owns the editing surface for these. The summary card just
    // tells the designer what's selected; the editor still appears
    // below.
    body = (
      <div className="props-row">
        <span className="props-label">Selected</span>
        <span className="props-value">{filtered.kind} · {filtered.id}</span>
      </div>
    );
  }

  // The card title reflects what the panel CAN show in this mode,
  // not what's currently selected — designers asked for "never show
  // mixed information" + "the selected mode controls what is
  // displayed".
  const cardTitle = mode === "edit_seam"
    ? "Seam Properties"
    : mode === "select_piece"
      ? "Piece Properties"
      : "Properties";

  return (
    <section className="props-card">
      <header className="props-card-head">
        <span className="props-card-title">{cardTitle}</span>
        {filtered && (
          <span className="props-card-kind">{filtered.kind}</span>
        )}
      </header>
      <div className="props-card-body">{body}</div>
    </section>
  );
}

// 0.1.42 perf: memoised so the right panel doesn't re-render on
// every App tick (pan / drag preview). Default shallow compare is
// enough — App memoises the array props it passes in (pieces,
// seams, layout, validation, inventoryMatch).
const SelectionProperties = memo(SelectionPropertiesImpl);
export default SelectionProperties;


function PieceBody({
  piece, layout, validation, inventoryMatch, assignedSlabId,
}: {
  piece: Piece;
  layout: Layout;
  validation: ValidationResult | null;
  inventoryMatch: InventoryMatchResponse | null;
  assignedSlabId: string | null;
}) {
  // Real cut dimensions come from the polygon, NOT the nominal grid
  // tile — for edge clips / hole splits / absorbed slivers the
  // polygon is much smaller than ``nominal_width × nominal_height``
  // and that's the value the factory cuts to.
  const cut = cutDimsForPiece(piece);
  const wCm = (cut.width_mm / 10).toFixed(1);
  const hCm = (cut.height_mm / 10).toFixed(1);
  const areaM2 = cut.area_m2.toFixed(3);
  const match: PieceMatchResult | null = inventoryMatch
    ? inventoryMatch.pieces.find((pm) => pm.piece_id === piece.piece_id)
        ?? null
    : null;
  const risk = computePieceRisk(piece, validation, layout, match);

  // Resolve the assigned candidate (if any) so we can show the
  // photo prominently AND the per-slab metadata under it.
  const assignedCandidate = assignedSlabId
    ? match?.candidates.find((c) => c.slab_id === assignedSlabId) ?? null
    : null;
  const hasSlabImage = !!assignedSlabId
    && !!assignedCandidate?.image_path;

  // 0.1.47 — fetch the safe-crop availability flag for the
  // currently-selected slab. The state is null while in flight;
  // true / false once resolved. Drives the "Safe crop not detected"
  // warning chip under the photo. We cancel in-flight requests when
  // the user clicks a different piece so a slow response never
  // overwrites the fresh slab's state.
  const [safeCrop, setSafeCrop] = useState<
    { available: boolean; reason?: string } | null
  >(null);
  useEffect(() => {
    if (!assignedSlabId) {
      setSafeCrop(null);
      return;
    }
    let cancelled = false;
    setSafeCrop(null);
    fetch(
      `/api/inventory/slab-crop-info/${encodeURIComponent(assignedSlabId)}`,
    )
      .then((r) => r.json())
      .then((info) => {
        if (cancelled) return;
        setSafeCrop({
          available: !!info.available,
          reason: info.reason,
        });
      })
      .catch(() => {
        if (cancelled) return;
        setSafeCrop({ available: false, reason: "fetch_error" });
      });
    return () => { cancelled = true; };
  }, [assignedSlabId]);

  return (
    <>
      {assignedSlabId && (
        <div className="props-slab-photo">
          {hasSlabImage ? (
            <img
              className="props-slab-photo-img"
              src={`/api/inventory/slab-image/${encodeURIComponent(assignedSlabId)}?crop=safe-area`}
              alt={`Assigned slab ${assignedSlabId}`}
              loading="lazy"
              onError={(e) => {
                (e.target as HTMLImageElement).style.display = "none";
              }}
            />
          ) : (
            <div className="props-slab-photo-empty">
              <strong>No image</strong>
              <span>this slab was uploaded without a photo</span>
            </div>
          )}
          <div className="props-slab-photo-caption">
            <span className="props-slab-photo-id" title={assignedSlabId}>
              {assignedSlabId}
            </span>
            {assignedCandidate && (
              <span className="props-slab-photo-meta">
                {Math.round(assignedCandidate.width_mm / 10)}×
                {Math.round(assignedCandidate.height_mm / 10)} cm
                {" · "}
                {Math.round(assignedCandidate.waste_fraction * 100)}% waste
                {assignedCandidate.rotation_needed && (
                  <span className="props-slab-photo-rotated">
                    {" "}· ↻ rotated
                  </span>
                )}
              </span>
            )}
            {/* Safe-crop status. Drawn only after the fetch resolves
                so the panel doesn't flicker "warning" on a slab that
                actually has detection. */}
            {hasSlabImage && safeCrop && !safeCrop.available && (
              <span className="props-slab-photo-warn">
                Safe crop not detected — using full image.
              </span>
            )}
            {hasSlabImage && safeCrop && safeCrop.available && (
              <span className="props-slab-photo-ok">
                Safe crop applied
              </span>
            )}
          </div>
        </div>
      )}
      <div className="props-row">
        <span className="props-label">Piece</span>
        <span className="props-value props-mono">{piece.piece_id}</span>
      </div>
      <div className="props-row">
        <span className="props-label">Zone</span>
        <span className="props-value props-mono">{piece.zone_id}</span>
      </div>
      {/* 0.1.49 — disambiguated dimension rows. "Piece size" is the
          cut shape's nominal bbox; "Original slab size" and "Final
          cut size" only appear when a slab is assigned. The labels
          intentionally don't reuse the generic word "Dimensions"
          since that hid which value was which. */}
      <div className="props-row">
        <span className="props-label">Piece size</span>
        <span className="props-value">{wCm} × {hCm} cm</span>
      </div>
      <div className="props-row">
        <span className="props-label">Area</span>
        <span className="props-value">{areaM2} m²</span>
      </div>
      {assignedSlabId && (
        <div className="props-row">
          <span className="props-label">Assigned slab</span>
          <span className="props-value props-mono" title={assignedSlabId}>
            {assignedSlabId}
          </span>
        </div>
      )}
      {assignedCandidate && (
        <>
          <div className="props-row">
            <span className="props-label">Original slab size</span>
            <span className="props-value">
              {(assignedCandidate.width_mm / 10).toFixed(1)}
              {" × "}
              {(assignedCandidate.height_mm / 10).toFixed(1)} cm
            </span>
          </div>
          <div className="props-row">
            <span className="props-label">Final cut size</span>
            <span className="props-value">
              {(assignedCandidate.cut_width_mm / 10).toFixed(1)}
              {" × "}
              {(assignedCandidate.cut_height_mm / 10).toFixed(1)} cm
            </span>
          </div>
          <div className="props-row">
            <span
              className="props-label"
              title="Material wasted relative to the original slab area"
            >
              Slab waste
            </span>
            <span className="props-value">
              {Math.round(assignedCandidate.waste_fraction * 100)}%
              {" "}
              <span className="props-value-aux">
                ({(assignedCandidate.waste_mm2 / 1_000_000).toFixed(3)} m²
                {" "}of slab)
              </span>
            </span>
          </div>
          <div className="props-row">
            <span className="props-label">Rotation</span>
            <span className="props-value">
              {assignedCandidate.rotation_needed
                ? <span className="props-slab-photo-rotated">↻ 90°</span>
                : "none"}
            </span>
          </div>
          <div className="props-row">
            <span className="props-label">Safe crop</span>
            <span className="props-value">
              {!hasSlabImage
                ? <span className="props-value-aux">no photo</span>
                : safeCrop === null
                  ? <span className="props-value-aux">checking…</span>
                  : safeCrop.available
                    ? <span className="props-slab-photo-ok">applied</span>
                    : <span className="props-slab-photo-warn">fallback (full image)</span>}
            </span>
          </div>
        </>
      )}
      {risk.badges.length > 0 && (
        <div className="props-row">
          <span className="props-label">Risk</span>
          <span className="props-value props-badges">
            {risk.badges.map((b, i) => (
              <span
                key={i}
                className={`props-badge props-badge-${b.variant}`}
                title={b.label}
              >
                {b.label}
              </span>
            ))}
          </span>
        </div>
      )}
      {match && (
        <div className="props-row">
          <span className="props-label">Slab match</span>
          <span className="props-value">
            <span className={`props-badge props-badge-match-${match.status}`}>
              {match.status === "no_match"
                ? "no match"
                : match.status === "exact_fit"
                  ? "exact fit"
                  : match.status === "multiple_options"
                    ? "multiple options"
                    : "matched"}
            </span>
            {match.candidates.length > 0 && (
              <span className="props-match-best">
                {" "}best: {match.candidates[0].slab_id}
                {" · "}
                {Math.round(match.candidates[0].waste_fraction * 100)}% waste
              </span>
            )}
          </span>
        </div>
      )}
    </>
  );
}


function SeamBody({ seam }: { seam: Seam }) {
  const posCm = (seam.position / 10).toFixed(1);
  const minCm = (seam.min_position / 10).toFixed(1);
  const maxCm = (seam.max_position / 10).toFixed(1);
  const lengthCm = ((seam.range[1] - seam.range[0]) / 10).toFixed(1);
  return (
    <>
      <div className="props-row">
        <span className="props-label">Seam</span>
        <span className="props-value">
          {seam.orientation}
          {seam.is_boundary && (
            <span className="props-badge props-badge-boundary">
              zone boundary
            </span>
          )}
        </span>
      </div>
      <div className="props-row">
        <span className="props-label">Position</span>
        <span className="props-value">{posCm} cm</span>
      </div>
      <div className="props-row">
        <span className="props-label">Drag range</span>
        <span className="props-value">{minCm} – {maxCm} cm</span>
      </div>
      <div className="props-row">
        <span className="props-label">Length</span>
        <span className="props-value">{lengthCm} cm</span>
      </div>
      <div className="props-row">
        <span className="props-label">Affects</span>
        <span className="props-value">
          {seam.piece_left_ids.length + seam.piece_right_ids.length} pieces
        </span>
      </div>
    </>
  );
}
