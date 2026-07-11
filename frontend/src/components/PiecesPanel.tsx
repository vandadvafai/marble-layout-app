// Risk-sorted piece list — the right-panel's default surface.
//
// Each row shows the piece_id, dimensions in centimetres, area in
// m², and a strip of risk badges. Clicking a row selects the piece
// (the canvas highlights it via the existing piece-selection path);
// the selected row auto-expands with the full detail card.
//
// Design notes:
//   * The list is virtualization-free — the L-shape demo has 10
//     pieces, the apartment 29, the rectangle ~6. A real install
//     plan could reach hundreds; a future milestone will swap in
//     react-window if profiling complains.
//   * Sort is driven by lib/pieceRisk.sortPiecesByRisk — see that
//     module for the policy. We pass the latest validation result
//     in so R1/R2/R6 classifications widen the picture.
//   * Dimension formatting is mm → cm (whole centimetres, no
//     decimals — designers reason about 159 cm, not 159.0 cm).
//   * Click-to-toggle: clicking the SELECTED piece's row deselects
//     it, otherwise the click selects this piece. The canvas
//     selection state is the single source of truth.

import { memo, useMemo } from "react";

import {
  assignmentStatusFor, assignmentStatusLabel,
  detectDuplicateSlabs, isSlabValidForPiece,
} from "../lib/finalAssign";
import { cutDimsForPiece } from "../lib/pieceGeom";
import { sortPiecesByRisk } from "../lib/pieceRisk";
import type {
  AssignmentStatus, Assignments, InventoryInfo, InventoryMatchResponse,
  Layout, MatchStatus, Piece, PieceMatchResult, RiskBadge, SlabCandidate,
  ValidationResult,
} from "../lib/types";

interface Props {
  pieces: Piece[];
  layout: Layout;
  validation: ValidationResult | null;
  /** Result of POST /match-inventory for the current pieces. When
   *  null the panel still renders — match-status badges and the
   *  slab list inside the detail card just don't appear. */
  inventoryMatch: InventoryMatchResponse | null;
  /** Resolved inventory source (path / label / counts), fetched on
   *  boot. Drives the header line above the pieces list. Null while
   *  the boot fetch is in flight; an error string flows in via
   *  ``inventoryInfoError`` instead. */
  inventoryInfo: InventoryInfo | null;
  inventoryInfoError: string | null;
  selectedPieceId: string | null;
  onSelectPiece: (piece_id: string | null) => void;

  // --- Assignment surface (Step 4 only) ----------------------------------
  // When ``assignmentMode`` is false the panel hides the Assign /
  // Unassign controls and the assignment status chip — it's pure
  // pieces + slabs in that case (Step 2 usage). When true, every
  // candidate gets an "Assign" button and the row shows its
  // assignment-status chip. ``assignmentMode`` also unlocks the
  // inline detail card (Step 4 needs it for the slab candidates);
  // Step 2 hides it so the panel stays compact — the right-stack
  // ``SelectionProperties`` shows piece details there.
  assignmentMode?: boolean;
  assignments?: Assignments;
  allowDuplicateAssignments?: boolean;
  onAssignSlab?: (piece_id: string, slab_id: string) => void;
  onClearAssignment?: (piece_id: string) => void;
  onToggleAllowDuplicates?: () => void;
}

function PiecesPanelImpl({
  pieces, layout, validation, inventoryMatch,
  inventoryInfo, inventoryInfoError,
  selectedPieceId, onSelectPiece,
  assignmentMode = false,
  assignments = {},
  allowDuplicateAssignments = false,
  onAssignSlab, onClearAssignment, onToggleAllowDuplicates,
}: Props) {
  const duplicateSlabIds = useMemo(
    () => detectDuplicateSlabs(assignments, allowDuplicateAssignments),
    [assignments, allowDuplicateAssignments],
  );
  const sorted = useMemo(
    () => sortPiecesByRisk(pieces, validation, layout, inventoryMatch),
    [pieces, validation, layout, inventoryMatch],
  );

  // O(1) lookup of the per-piece match result by piece_id so each
  // row can fetch its own without re-walking the response list.
  const matchByPieceId = useMemo(() => {
    const m = new Map<string, PieceMatchResult>();
    if (inventoryMatch) {
      for (const pm of inventoryMatch.pieces) {
        m.set(pm.piece_id, pm);
      }
    }
    return m;
  }, [inventoryMatch]);

  const criticalCount = sorted.filter((r) => r.risk.level === "critical").length;
  const highCount = sorted.filter((r) => r.risk.level === "high").length;
  const mediumCount = sorted.filter((r) => r.risk.level === "medium").length;

  return (
    <section className="pieces-panel">
      <header className="pieces-panel-header">
        <div className="pieces-panel-title">Pieces</div>
        <div className="pieces-panel-meta">
          {pieces.length} pieces · sorted by risk
          {inventoryMatch
            && ` · ${inventoryMatch.inventory_count} slabs in inventory`}
        </div>
        <div className="pieces-panel-summary">
          {criticalCount > 0 && (
            <span className="pieces-summary-pill pieces-summary-critical">
              {criticalCount} critical
            </span>
          )}
          {highCount > 0 && (
            <span className="pieces-summary-pill pieces-summary-high">
              {highCount} high
            </span>
          )}
          {mediumCount > 0 && (
            <span className="pieces-summary-pill pieces-summary-medium">
              {mediumCount} medium
            </span>
          )}
          {criticalCount + highCount + mediumCount === 0 && (
            <span className="pieces-summary-pill pieces-summary-ok">
              all clean
            </span>
          )}
          {inventoryMatch && inventoryMatch.summary.no_match > 0 && (
            <span className="pieces-summary-pill pieces-summary-critical">
              {inventoryMatch.summary.no_match} no slab
            </span>
          )}
        </div>
      </header>

      <InventoryHeader info={inventoryInfo} error={inventoryInfoError} />

      {assignmentMode && (
        <div className="pieces-assign-bar">
          <label className="pieces-allow-dup">
            <input
              type="checkbox"
              checked={allowDuplicateAssignments}
              onChange={() => onToggleAllowDuplicates?.()}
            />{" "}
            allow same slab on multiple pieces
          </label>
          <div
            className="pieces-unassign-target"
            onDragOver={(e) => {
              // Accept only piece drags — dropping a slab candidate
              // back onto the inventory doesn't make sense.
              const types = Array.from(e.dataTransfer.types);
              if (types.includes("application/x-stonelayout")) {
                e.preventDefault();
                e.currentTarget.classList.add("pieces-unassign-target-hover");
              }
            }}
            onDragLeave={(e) => {
              e.currentTarget.classList.remove("pieces-unassign-target-hover");
            }}
            onDrop={(e) => {
              e.preventDefault();
              e.currentTarget.classList.remove("pieces-unassign-target-hover");
              const raw = e.dataTransfer.getData("application/x-stonelayout");
              if (!raw) return;
              try {
                const payload = JSON.parse(raw);
                if (payload.kind === "piece" && payload.piece_id) {
                  onClearAssignment?.(payload.piece_id);
                }
              } catch { /* ignore malformed drag */ }
            }}
            title="Drag an assigned piece here to remove its slab"
          >
            Drop a piece here to unassign
          </div>
        </div>
      )}
      <ul className="pieces-list">
        {sorted.map(({ piece, risk }) => {
          const isSelected = piece.piece_id === selectedPieceId;
          const match = matchByPieceId.get(piece.piece_id) ?? null;
          // Use real cut dimensions (polygon bbox + shoelace area), NOT
          // the nominal tile size — see lib/pieceGeom for the rationale.
          const cut = cutDimsForPiece(piece);
          const assignmentStatus: AssignmentStatus | null = assignmentMode
            ? assignmentStatusFor(
                piece.piece_id, assignments, match, duplicateSlabIds,
              )
            : null;
          const assignedSlabId = assignments[piece.piece_id] ?? null;
          return (
            <li
              key={piece.piece_id}
              className={
                `piece-row piece-row-${risk.level} `
                + (isSelected ? "piece-row-selected" : "")
              }
            >
              <button
                type="button"
                className="piece-row-summary"
                onClick={() =>
                  onSelectPiece(isSelected ? null : piece.piece_id)
                }
                aria-expanded={isSelected}
              >
                <span className="piece-row-id" title={piece.piece_id}>
                  {piece.piece_id}
                </span>
                <span className="piece-row-dims">
                  {mmToCm(cut.width_mm)}×{mmToCm(cut.height_mm)} cm
                </span>
                <span className="piece-row-area">
                  {cut.area_m2.toFixed(3)} m²
                </span>
                <span className="piece-row-badges">
                  {assignmentStatus && (
                    <AssignmentStatusChip status={assignmentStatus} />
                  )}
                  {match && <MatchStatusChip status={match.status} />}
                  {risk.badges.map((b, i) => (
                    <BadgeChip key={i} badge={b} />
                  ))}
                </span>
                {assignmentMode && (
                  <span className="piece-row-chevron">
                    {isSelected ? "▾" : "▸"}
                  </span>
                )}
              </button>

              {isSelected && assignmentMode && (
                <PieceDetails
                  piece={piece}
                  layout={layout}
                  riskBadges={risk.badges}
                  match={match}
                  inventoryLoaded={inventoryMatch !== null}
                  assignmentMode={assignmentMode}
                  assignedSlabId={assignedSlabId}
                  duplicateSlabIds={duplicateSlabIds}
                  onAssignSlab={onAssignSlab}
                  onClearAssignment={onClearAssignment}
                />
              )}
            </li>
          );
        })}
        {sorted.length === 0 && (
          <li className="piece-row-empty">No pieces in this layout.</li>
        )}
      </ul>
    </section>
  );
}

// 0.1.42 perf: memoised. Pan / zoom / selection of an annotation
// (doorway / column / guide line) doesn't change any of the piece
// list's inputs, so shallow-compare skips the entire panel render
// in those cases. Piece selection itself does change
// ``selectedPieceId`` and goes through.
const PiecesPanel = memo(PiecesPanelImpl);
export default PiecesPanel;

// ---------------------------------------------------------------------------
// row detail card
// ---------------------------------------------------------------------------

function PieceDetails({
  piece, layout, riskBadges, match, inventoryLoaded,
  assignmentMode, assignedSlabId, duplicateSlabIds,
  onAssignSlab, onClearAssignment,
}: {
  piece: Piece;
  layout: Layout;
  riskBadges: RiskBadge[];
  match: PieceMatchResult | null;
  inventoryLoaded: boolean;
  assignmentMode: boolean;
  assignedSlabId: string | null;
  duplicateSlabIds: Set<string>;
  onAssignSlab?: (piece_id: string, slab_id: string) => void;
  onClearAssignment?: (piece_id: string) => void;
}) {
  // Real cut dims drive everything in this card: the dimension rows,
  // the area, the "needs oversized slab" callout, and the AssignedSlabCard
  // below. The nominal tile size lives on the piece for traceability
  // ("which row/col did this strip come from?") but it is NOT what the
  // factory cuts — see lib/pieceGeom.
  const cut = cutDimsForPiece(piece);
  const w = cut.width_mm;
  const h = cut.height_mm;
  const isAbsorbed = (piece.notes || []).some((n) =>
    n.startsWith("absorbed_sliver:"),
  );
  const tileW = layout.grid.tile_width_mm;
  const tileH = layout.grid.tile_height_mm;
  const oversized = w > tileW + 1 || h > tileH + 1;

  return (
    <div className="piece-details">
      <div className="piece-detail-grid">
        <DetailRow label="Width" value={`${mmToCm(w)} cm`} />
        <DetailRow label="Height" value={`${mmToCm(h)} cm`} />
        <DetailRow
          label="Area"
          value={`${cut.area_m2.toFixed(3)} m²`}
        />
        <DetailRow label="Zone" value={piece.zone_id} mono />
        <DetailRow
          label="Position"
          value={`(${mmToCm(piece.nominal_x_mm)} cm, `
            + `${mmToCm(piece.nominal_y_mm)} cm)`}
          mono
        />
        <DetailRow
          label="Type"
          value={
            piece.is_full_tile
              ? "full tile"
              : piece.is_edge_piece
                ? "edge piece"
                : "—"
          }
        />
        {isAbsorbed && (
          <DetailRow label="Status" value="absorbed sliver holder" />
        )}
        {piece.intersects_hole && (
          <DetailRow label="Geometry" value="intersects boundary hole" />
        )}
      </div>

      {oversized && (
        <div className="piece-callout">
          <strong>Needs oversized slab.</strong>{" "}
          Nominal {mmToCm(w)} × {mmToCm(h)} cm exceeds the
          median tile {mmToCm(tileW)} × {mmToCm(tileH)} cm —
          stock slabs matching the median won't fit this piece.
          Inventory matching is not wired yet; check stock manually.
        </div>
      )}

      {riskBadges.length > 0 && (
        <div className="piece-detail-badges">
          {riskBadges.map((b, i) => (
            <BadgeChip key={i} badge={b} />
          ))}
        </div>
      )}

      {piece.notes && piece.notes.length > 0 && (
        <div className="piece-detail-notes">
          <div className="piece-detail-notes-label">Notes</div>
          <ul>
            {piece.notes.map((n, i) => (
              <li key={i}><code>{n}</code></li>
            ))}
          </ul>
        </div>
      )}

      {assignmentMode && (
        <AssignedSlabCard
          piece_id={piece.piece_id}
          assignedSlabId={assignedSlabId}
          match={match}
          isDuplicate={
            assignedSlabId !== null
            && duplicateSlabIds.has(assignedSlabId)
          }
          isTooSmall={
            assignedSlabId !== null
            && match !== null
            && !isSlabValidForPiece(assignedSlabId, match)
          }
          piece_width_mm={w}
          piece_height_mm={h}
          onClear={() => onClearAssignment?.(piece.piece_id)}
        />
      )}

      <SlabsBlock
        match={match}
        inventoryLoaded={inventoryLoaded}
        assignmentMode={assignmentMode}
        piece_id={piece.piece_id}
        assignedSlabId={assignedSlabId}
        duplicateSlabIds={duplicateSlabIds}
        onAssignSlab={onAssignSlab}
      />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Assigned-slab card — sits above the candidate list in Step 4.
// When no slab is assigned, prompts the designer to pick one. When
// a slab IS assigned, shows its id and a quick Unassign control;
// flips to a warning state when the slab is shared with another
// piece (duplicate).
// ---------------------------------------------------------------------------

function AssignedSlabCard({
  piece_id, assignedSlabId, match, isDuplicate, isTooSmall,
  piece_width_mm, piece_height_mm, onClear,
}: {
  piece_id: string;
  assignedSlabId: string | null;
  match: PieceMatchResult | null;
  isDuplicate: boolean;
  isTooSmall: boolean;
  piece_width_mm: number;
  piece_height_mm: number;
  onClear: () => void;
}) {
  if (!assignedSlabId) {
    return (
      <div className="piece-assigned-card piece-assigned-empty">
        <div className="piece-assigned-label">Assigned slab</div>
        <div className="piece-assigned-none">none — pick one below</div>
      </div>
    );
  }
  // Look up the slab's metadata from the candidate list so dims +
  // waste are visible without a separate request. Falls back to a
  // "not in current candidates" hint if the inventory has been
  // refreshed without the assigned slab.
  const slab = match?.candidates.find((c) => c.slab_id === assignedSlabId)
    ?? null;
  return (
    <div
      className={
        "piece-assigned-card piece-assigned-card-draggable"
        + (isDuplicate ? " piece-assigned-card-duplicate" : "")
        + (isTooSmall ? " piece-assigned-card-toosmall" : "")
      }
      draggable
      onDragStart={(e) => {
        // V1.1 — the assigned card is a drag SOURCE. Dragging it
        // onto another piece = swap; onto the inventory drop-zone
        // = unassign. Payload identifies the piece, not the slab,
        // so the target can look up the current slab if needed.
        const payload = JSON.stringify({
          kind: "piece",
          piece_id,
          slab_id: assignedSlabId,
        });
        e.dataTransfer.setData("application/x-stonelayout", payload);
        e.dataTransfer.setData("text/plain", piece_id);
        e.dataTransfer.effectAllowed = "move";
      }}
      title="Drag onto another piece to swap, or onto the inventory to unassign"
    >
      <div className="piece-assigned-label">
        Assigned slab
        {isDuplicate && (
          <span className="piece-assigned-conflict">
            also assigned elsewhere
          </span>
        )}
        {isTooSmall && (
          <span className="piece-assigned-conflict">
            too small for {Math.round(piece_width_mm / 10)}×
            {Math.round(piece_height_mm / 10)} cm piece
          </span>
        )}
      </div>
      <div className="piece-assigned-row">
        <img
          className="piece-assigned-thumb"
          src={`/api/inventory/slab-image/${encodeURIComponent(assignedSlabId)}?crop=safe-area`}
          alt={assignedSlabId}
          loading="lazy"
          onError={(e) => {
            (e.target as HTMLImageElement).style.display = "none";
          }}
        />
        <div className="piece-assigned-info">
          <div className="piece-assigned-id" title={assignedSlabId}>
            {assignedSlabId}
          </div>
          {slab && (
            <div className="piece-assigned-meta">
              {Math.round(slab.width_mm / 10)}×
              {Math.round(slab.height_mm / 10)} cm
              {" · "}{Math.round(slab.waste_fraction * 100)}% waste
              {slab.rotation_needed && " · rotated"}
              {slab.material_name && ` · ${slab.material_name}`}
            </div>
          )}
          {!slab && (
            <div className="piece-assigned-meta piece-assigned-stale">
              slab not in current candidate list
            </div>
          )}
        </div>
        <button
          type="button"
          className="piece-assigned-clear"
          onClick={onClear}
          title={`Unassign ${assignedSlabId} from ${piece_id}`}
        >
          Unassign
        </button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// inventory match — top-3 slab candidates, surfaced inside the
// expanded piece-details card
// ---------------------------------------------------------------------------

function SlabsBlock({
  match, inventoryLoaded, assignmentMode, piece_id, assignedSlabId,
  duplicateSlabIds, onAssignSlab,
}: {
  match: PieceMatchResult | null;
  inventoryLoaded: boolean;
  assignmentMode: boolean;
  piece_id: string;
  assignedSlabId: string | null;
  duplicateSlabIds: Set<string>;
  onAssignSlab?: (piece_id: string, slab_id: string) => void;
}) {
  // Three loading/empty states:
  //   * inventoryLoaded=false → backend hasn't responded yet
  //     (typically the first 200 ms after an edit). Show a soft
  //     status line; don't pretend there's no inventory.
  //   * match is null but inventoryLoaded=true → the matcher
  //     hasn't seen this piece (race: edit happened after the last
  //     match call). Same soft message.
  //   * match.status === "no_match" → real "no slab" state with the
  //     critical chip.
  //   * candidates present → show the top-3 list.
  if (!inventoryLoaded || !match) {
    return (
      <div className="piece-slabs">
        <div className="piece-slabs-label">Inventory match</div>
        <div className="piece-slabs-empty">waiting for inventory…</div>
      </div>
    );
  }
  if (match.status === "no_match") {
    return (
      <div className="piece-slabs">
        <div className="piece-slabs-label">Inventory match</div>
        <div className="piece-slabs-no-match">
          No stock slab can cover {Math.round(match.required_width_mm / 10)}
          ×{Math.round(match.required_height_mm / 10)} cm.
          The piece needs to be redesigned, sourced from a new slab
          batch, or split further before fabrication.
        </div>
      </div>
    );
  }
  return (
    <div className="piece-slabs">
      <div className="piece-slabs-label">
        Top {match.candidates.length}{" "}
        {match.candidates.length === 1 ? "slab" : "slabs"}
        {" · "}
        <span className="piece-slabs-status">
          {matchStatusLabel(match.status)}
        </span>
      </div>
      <ul className="piece-slabs-list">
        {match.candidates.map((c, i) => (
          <SlabCandidateRow
            key={c.slab_id + ":" + i}
            candidate={c}
            assignmentMode={assignmentMode}
            isAssignedHere={c.slab_id === assignedSlabId}
            isDuplicate={duplicateSlabIds.has(c.slab_id)}
            onAssign={() => onAssignSlab?.(piece_id, c.slab_id)}
          />
        ))}
      </ul>
    </div>
  );
}

function SlabCandidateRow({
  candidate, assignmentMode, isAssignedHere, isDuplicate, onAssign,
}: {
  candidate: SlabCandidate;
  assignmentMode: boolean;
  isAssignedHere: boolean;
  isDuplicate: boolean;
  onAssign: () => void;
}) {
  const wCm = Math.round(candidate.width_mm / 10);
  const hCm = Math.round(candidate.height_mm / 10);
  const wastePct = Math.round(candidate.waste_fraction * 100);
  // V1.1 — HTML5 drag source. The payload is a JSON blob rather
  // than a plain slab id so the canvas can tell inventory drags
  // apart from piece-to-piece drags (which carry ``kind: "piece"``).
  const dragProps = assignmentMode ? {
    draggable: true,
    onDragStart: (e: React.DragEvent<HTMLLIElement>) => {
      const payload = JSON.stringify({
        kind: "slab",
        slab_id: candidate.slab_id,
      });
      e.dataTransfer.setData("application/x-stonelayout", payload);
      // Fallback for browsers/tests that only surface text/plain.
      e.dataTransfer.setData("text/plain", candidate.slab_id);
      e.dataTransfer.effectAllowed = "copyMove";
    },
  } : {};
  return (
    <li className={
      "piece-slab-row"
      + (isAssignedHere ? " piece-slab-row-assigned" : "")
      + (assignmentMode ? " piece-slab-row-draggable" : "")
    } {...dragProps}>
      {candidate.image_path && (
        <img
          className="piece-slab-thumb"
          src={`/api/inventory/slab-image/${encodeURIComponent(candidate.slab_id)}`}
          alt={candidate.slab_id}
          loading="lazy"
          onError={(e) => {
            // Hide broken thumbs silently — some inventory rows
            // have image_path entries pointing at files that
            // aren't on disk.
            (e.target as HTMLImageElement).style.display = "none";
          }}
        />
      )}
      <div className="piece-slab-id" title={candidate.slab_id}>
        {candidate.slab_id}
      </div>
      <div className="piece-slab-dims">{wCm}×{hCm} cm</div>
      <div className="piece-slab-waste">
        <span className="piece-slab-waste-pct">{wastePct}%</span>{" "}
        <span className="piece-slab-waste-label">waste</span>
      </div>
      <div className="piece-slab-flags">
        {candidate.rotation_needed && (
          <span className="piece-slab-flag piece-slab-rotate">↻ rotated</span>
        )}
        {candidate.material_name && (
          <span className="piece-slab-flag piece-slab-material">
            {candidate.material_name}
          </span>
        )}
        {candidate.image_path && (
          <span className="piece-slab-flag piece-slab-image" title={candidate.image_path}>
            🖼 image
          </span>
        )}
        {isDuplicate && !isAssignedHere && (
          <span className="piece-slab-flag piece-slab-dup">
            in use elsewhere
          </span>
        )}
        {assignmentMode && (
          isAssignedHere ? (
            <span className="piece-slab-flag piece-slab-current">
              ✓ assigned here
            </span>
          ) : (
            <button
              type="button"
              className="piece-slab-assign-btn"
              onClick={onAssign}
              title={`Assign ${candidate.slab_id} to this piece`}
            >
              Assign
            </button>
          )
        )}
      </div>
    </li>
  );
}

function MatchStatusChip({ status }: { status: MatchStatus }) {
  const cls = {
    exact_fit: "match-status-exact",
    matched: "match-status-matched",
    multiple_options: "match-status-options",
    no_match: "match-status-no",
  }[status];
  return (
    <span className={`piece-match-chip ${cls}`}>
      {matchStatusLabel(status)}
    </span>
  );
}

function matchStatusLabel(status: MatchStatus): string {
  return {
    exact_fit: "exact fit",
    matched: "matched",
    multiple_options: "multiple options",
    no_match: "no match",
  }[status];
}

// ---------------------------------------------------------------------------
// helpers
// ---------------------------------------------------------------------------

function DetailRow({
  label, value, mono = false,
}: {
  label: string; value: string; mono?: boolean;
}) {
  return (
    <>
      <span className="piece-detail-label">{label}</span>
      <span
        className={
          "piece-detail-value" + (mono ? " piece-detail-mono" : "")
        }
      >
        {value}
      </span>
    </>
  );
}

function BadgeChip({ badge }: { badge: RiskBadge }) {
  return (
    <span className={`piece-badge piece-badge-${badge.variant}`}>
      {badge.label}
    </span>
  );
}

/** mm → cm rounded to whole centimetres. We round rather than
 *  truncate so a 1595 mm piece reads as 160 cm instead of 159 cm. */
function mmToCm(mm: number): string {
  return String(Math.round(mm / 10));
}

function AssignmentStatusChip({ status }: { status: AssignmentStatus }) {
  const cls = {
    unassigned: "assign-status-unassigned",
    assigned: "assign-status-assigned",
    no_match: "assign-status-nomatch",
    too_small: "assign-status-toosmall",
    duplicate: "assign-status-duplicate",
  }[status];
  return (
    <span className={`piece-match-chip ${cls}`} title={status}>
      {assignmentStatusLabel(status)}
    </span>
  );
}

// ---------------------------------------------------------------------------
// Inventory source header — sits just under the pieces-panel title.
// Tells the designer which clean_slabs.json the matcher is reading
// from, how many slabs were valid, and how many were skipped due to
// bad dimensions. Renders compactly when info is healthy, prominently
// when there's an error (so a misconfigured server is visible).
// ---------------------------------------------------------------------------

function InventoryHeader({
  info, error,
}: { info: InventoryInfo | null; error: string | null }) {
  if (error) {
    return (
      <div className="inventory-header inventory-header-error">
        <span className="inventory-header-label">Inventory</span>
        <span className="inventory-header-error-text">{error}</span>
      </div>
    );
  }
  if (!info) {
    return (
      <div className="inventory-header inventory-header-loading">
        <span className="inventory-header-label">Inventory</span>
        <span className="inventory-header-loading-text">loading…</span>
      </div>
    );
  }
  const sourceClass = {
    env_override: "inventory-source-env",
    real_inventory: "inventory-source-real",
    demo_fallback: "inventory-source-demo",
  }[info.source_label] ?? "inventory-source-other";
  return (
    <div className="inventory-header">
      <span className="inventory-header-label">Inventory</span>
      <span
        className={`inventory-source-chip ${sourceClass}`}
        title={info.source_description}
      >
        {labelToChip(info.source_label)}
      </span>
      <span className="inventory-header-counts">
        {info.valid_count} valid
        {info.skipped_count > 0 && (
          <>
            {" · "}
            <span className="inventory-header-skipped">
              {info.skipped_count} invalid
            </span>
          </>
        )}
      </span>
      <span className="inventory-header-path" title={info.source_path}>
        {info.source_path}
      </span>
    </div>
  );
}

/** Friendly short label for the inventory source chip. Mirrors the
 *  backend's source_label values; an unknown label falls through to
 *  its raw string so any future server-side label still renders.*/
function labelToChip(source_label: string): string {
  return {
    env_override: "env override",
    real_inventory: "real",
    demo_fallback: "demo fallback",
  }[source_label] ?? source_label;
}
