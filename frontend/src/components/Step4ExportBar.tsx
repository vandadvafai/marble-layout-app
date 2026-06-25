// Step 4 — export action bar. Sits above the assignment surface
// and shows:
//   * "N / M pieces assigned" counter
//   * a "K conflicts" pill when there are duplicate-slab assignments
//   * the two production export buttons (PNG + DXF), DISABLED in
//     this milestone since the export pipeline isn't wired in yet
//
// Keeping the bar separate from PiecesPanel lets Step 2 reuse the
// panel without dragging in the export controls.

import { memo, useState } from "react";

interface Props {
  total: number;
  assigned: number;
  unassigned: number;
  noMatch: number;
  duplicate: number;
  /** True when the matcher hasn't returned yet, or no slabs have
   *  any candidates — used to disable Auto-assign before it can do
   *  anything useful. */
  canAutoAssign: boolean;
  onAutoAssign: () => void;
  onClearAssignments: () => void;
  /** Debug summary so the designer can see WHY pieces stay
   *  unassigned (no matching slab, all candidates already taken,
   *  duplicate prevention, etc.). */
  inventoryValidCount: number | null;
  inventoryUnusedCount: number;
  /** Export handlers — owned by App. Return a Promise so the bar
   *  can show a "Exporting…" state while the export runs. Both
   *  resolve with ``{ok, error?}`` so we can surface failures in
   *  the bar's status text. */
  onExportPng: () => Promise<{ ok: boolean; error?: string }>;
  onExportDxf: () => Promise<{ ok: boolean; error?: string }>;
  /** 0.1.52 — slab-image readiness. The PNG export must not fire
   *  until every assigned slab image has finished loading in the
   *  browser; otherwise the SVG serialisation captures pending
   *  <image> elements and the resulting PNG has blank tiles. */
  imageReadiness: {
    total: number;
    loaded: number;
    failed: number;
    pending: number;
    isReady: boolean;
  };
}

function Step4ExportBarImpl({
  total, assigned, unassigned, noMatch, duplicate,
  canAutoAssign, onAutoAssign, onClearAssignments,
  inventoryValidCount, inventoryUnusedCount,
  onExportPng, onExportDxf,
  imageReadiness,
}: Props) {
  const allAssigned = total > 0 && assigned === total && duplicate === 0;
  // 0.1.50 — disable export buttons until every piece has a slab
  // assigned AND no duplicate conflicts remain. ``allAssigned`` is
  // the same predicate that lights up the "ready to export" pill.
  //
  // 0.1.52 — the PNG export ALSO waits until every assigned slab
  // image has finished loading. The DXF doesn't need images, so it
  // only gates on ``allAssigned``.
  const pngExportReady = allAssigned && imageReadiness.isReady;
  const dxfExportReady = allAssigned;
  const [pngBusy, setPngBusy] = useState(false);
  const [dxfBusy, setDxfBusy] = useState(false);
  const [exportMessage, setExportMessage] = useState<
    { kind: "ok" | "err"; text: string } | null
  >(null);

  const handleExportPng = async () => {
    setPngBusy(true);
    setExportMessage(null);
    const res = await onExportPng();
    setPngBusy(false);
    if (res.ok) {
      setExportMessage({ kind: "ok", text: "Client PNG downloaded." });
    } else {
      setExportMessage({
        kind: "err",
        text: `PNG export failed: ${res.error ?? "unknown error"}`,
      });
    }
  };

  const handleExportDxf = async () => {
    setDxfBusy(true);
    setExportMessage(null);
    const res = await onExportDxf();
    setDxfBusy(false);
    if (res.ok) {
      setExportMessage({ kind: "ok", text: "Factory DXF downloaded." });
    } else {
      setExportMessage({
        kind: "err",
        text: `DXF export failed: ${res.error ?? "unknown error"}`,
      });
    }
  };
  return (
    <div className="step4-export-bar">
      <div className="step4-export-status">
        <span className="step4-export-counts">
          <strong>{assigned}</strong> / {total} assigned
        </span>
        {unassigned > 0 && (
          <span className="step4-export-pill step4-export-pill-warn">
            {unassigned} unassigned
          </span>
        )}
        {noMatch > 0 && (
          <span className="step4-export-pill step4-export-pill-critical">
            {noMatch} no match
          </span>
        )}
        {duplicate > 0 && (
          <span className="step4-export-pill step4-export-pill-critical">
            {duplicate} duplicate
          </span>
        )}
        {allAssigned && (
          <span className="step4-export-pill step4-export-pill-ok">
            ready to export
          </span>
        )}
      </div>

      {/* 0.1.48 — inventory utilisation debug. Surfaces "you have X
          slabs in inventory, Y are still free" so unassigned-piece
          mysteries are obvious. */}
      {inventoryValidCount !== null && (
        <div className="step4-export-debug">
          <span>
            Inventory <strong>{inventoryValidCount}</strong> valid ·{" "}
            <strong>{inventoryUnusedCount}</strong> unused
          </span>
          {unassigned > 0 && inventoryUnusedCount > 0 && (
            <span className="step4-export-debug-hint">
              {unassigned} piece{unassigned === 1 ? "" : "s"} unassigned but
              {" "}{inventoryUnusedCount} slab{inventoryUnusedCount === 1 ? "" : "s"} still free —
              click <em>Auto assign best slabs</em>.
            </span>
          )}
          {unassigned > 0 && inventoryUnusedCount === 0 && noMatch === 0 && (
            <span className="step4-export-debug-hint">
              No free slabs left. Enable <em>allow same slab on multiple
              pieces</em> to reuse, or upload more inventory.
            </span>
          )}
        </div>
      )}

      {/* Visual legend — keys the canvas piece colours to assignment
          status so the designer can decode the layout at a glance. */}
      <div className="step4-legend">
        <span className="step4-legend-item">
          <span className="step4-legend-swatch step4-legend-photo" />
          assigned with photo
        </span>
        <span className="step4-legend-item">
          <span className="step4-legend-swatch step4-legend-nophoto" />
          assigned without photo
        </span>
        <span className="step4-legend-item">
          <span className="step4-legend-swatch step4-legend-unassigned" />
          unassigned
        </span>
        <span className="step4-legend-item">
          <span className="step4-legend-swatch step4-legend-nomatch" />
          no slab match
        </span>
        <span className="step4-legend-item">
          <span className="step4-legend-swatch step4-legend-duplicate" />
          duplicate
        </span>
      </div>

      <div className="step4-export-actions">
        <button
          type="button"
          className="step4-export-btn step4-export-btn-primary"
          onClick={onAutoAssign}
          disabled={!canAutoAssign}
          title={
            canAutoAssign
              ? "Pick the best matching slab for every unassigned piece"
              : "No matchable inventory yet"
          }
        >
          Auto assign best slabs
        </button>
        <button
          type="button"
          className="step4-export-btn"
          onClick={onClearAssignments}
          disabled={assigned === 0 && duplicate === 0}
          title="Clear every assignment for these pieces"
        >
          Clear assignments
        </button>
        <button
          type="button"
          className="step4-export-btn"
          onClick={handleExportPng}
          disabled={!pngExportReady || pngBusy || dxfBusy}
          title={
            !allAssigned
              ? "Assign every piece first"
              : !imageReadiness.isReady
                ? "Waiting for slab images to load"
                : "Download a PNG of the current layout"
          }
        >
          {pngBusy ? "Exporting…" : "Export client PNG"}
        </button>
        <button
          type="button"
          className="step4-export-btn"
          onClick={handleExportDxf}
          disabled={!dxfExportReady || pngBusy || dxfBusy}
          title={
            dxfExportReady
              ? "Download a DXF cut plan for the factory"
              : "Assign every piece first"
          }
        >
          {dxfBusy ? "Exporting…" : "Export factory DXF"}
        </button>
      </div>

      {/* 0.1.52 — image-readiness helper text. Only relevant once
          every piece is assigned; before that, the "assign every
          piece" gate is the louder signal. */}
      {allAssigned && imageReadiness.total > 0 && !imageReadiness.isReady && (
        <div className="step4-readiness step4-readiness-pending">
          Preparing slab images…{" "}
          {imageReadiness.loaded} / {imageReadiness.total} loaded
        </div>
      )}
      {allAssigned && imageReadiness.failed > 0 && imageReadiness.isReady && (
        <div className="step4-readiness step4-readiness-err">
          {imageReadiness.failed} of {imageReadiness.total} slab images
          failed to load. Re-upload photos or continue without missing
          images.
        </div>
      )}

      {exportMessage && (
        <div className={
          "step4-export-msg "
          + (exportMessage.kind === "ok"
            ? "step4-export-msg-ok"
            : "step4-export-msg-err")
        }>
          {exportMessage.text}
        </div>
      )}
    </div>
  );
}


// 0.1.42 perf: memoised. Assignment counts only change on
// explicit user action so this skips work on every App tick.
const Step4ExportBar = memo(Step4ExportBarImpl);
export default Step4ExportBar;
