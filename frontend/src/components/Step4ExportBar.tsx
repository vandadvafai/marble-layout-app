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

import type {
  FactoryFitResponse, FactoryFitResult, ManufacturingPolicy,
} from "../lib/exportLayout";

interface Props {
  total: number;
  assigned: number;
  unassigned: number;
  noMatch: number;
  /** 0.1.53 — assignments where the assigned slab is too small to
   *  cover the piece (typical after a manual swap). The export bar
   *  surfaces the count as its own pill and refuses to export until
   *  the conflicts are resolved. */
  tooSmall: number;
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
  /** 0.1.53 — manual-swap toggle. When ON the canvas piece pointer
   *  handlers switch from "select" to "drag-to-swap"; the button
   *  also turns into an active indicator in the bar. */
  swapMode: boolean;
  onToggleSwapMode: () => void;
  /** 1.1 — manufacturing policy + preflight fit result. Both are
   *  owned by App so a policy change re-runs the check without a
   *  round-trip through this component's state. */
  manufacturingPolicy: ManufacturingPolicy;
  onPolicyChange: (next: ManufacturingPolicy) => void;
  fitResponse: FactoryFitResponse | null;
  fitChecking: boolean;
  fitError: string | null;
  failingFit: FactoryFitResult[];
}

function Step4ExportBarImpl({
  total, assigned, unassigned, noMatch, tooSmall, duplicate,
  canAutoAssign, onAutoAssign, onClearAssignments,
  inventoryValidCount, inventoryUnusedCount,
  onExportPng, onExportDxf,
  imageReadiness,
  swapMode, onToggleSwapMode,
  manufacturingPolicy, onPolicyChange,
  fitResponse, fitChecking, fitError, failingFit,
}: Props) {
  // "Ready to export" requires every piece assigned, with no
  // duplicate-slab conflicts AND no too-small-slab conflicts. The
  // too-small check (0.1.53) is what catches a manual swap that
  // dropped a smaller slab on a larger piece.
  const allAssigned = total > 0
    && assigned === total
    && duplicate === 0
    && tooSmall === 0;
  // Manufacturing fit — the last preflight response must say every
  // piece is factory_ready, otherwise the DXF export is blocked. A
  // missing response (still loading, or upstream conflict blocking
  // the request) keeps the button disabled too.
  const factoryFitReady = allAssigned
    && fitResponse !== null
    && fitResponse.factory_ready
    && failingFit.length === 0;
  // 0.1.50 — disable export buttons until every piece has a slab
  // assigned AND no duplicate conflicts remain. ``allAssigned`` is
  // the same predicate that lights up the "ready to export" pill.
  //
  // 0.1.52 — the PNG export ALSO waits until every assigned slab
  // image has finished loading. The DXF doesn't need images, so it
  // only gates on ``allAssigned``.
  const pngExportReady = allAssigned && imageReadiness.isReady;
  const dxfExportReady = factoryFitReady;
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
        {tooSmall > 0 && (
          <span
            className="step4-export-pill step4-export-pill-critical"
            title="Assigned slab is too small for the piece (resolve before exporting)"
          >
            {tooSmall} slab too small
          </span>
        )}
        {duplicate > 0 && (
          <span className="step4-export-pill step4-export-pill-critical">
            {duplicate} duplicate
          </span>
        )}
        {allAssigned && factoryFitReady && (
          <span className="step4-export-pill step4-export-pill-ok">
            ready to export
          </span>
        )}
        {allAssigned && !factoryFitReady && failingFit.length > 0 && (
          <span className="step4-export-pill step4-export-pill-critical">
            {failingFit.length} fit issue{failingFit.length === 1 ? "" : "s"}
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
          className={
            "step4-export-btn step4-export-btn-swap"
            + (swapMode ? " step4-export-btn-swap-on" : "")
          }
          onClick={onToggleSwapMode}
          aria-pressed={swapMode}
          title={
            swapMode
              ? "Stop swapping — return to normal selection"
              : "Manually swap slabs between pieces by dragging one piece onto another"
          }
        >
          {swapMode ? "Swap slabs: ON" : "Swap slabs"}
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
            tooSmall > 0
              ? "Resolve the too-small slab conflicts first"
              : duplicate > 0
                ? "Resolve the duplicate-slab conflicts first"
                : !allAssigned
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
            tooSmall > 0
              ? "Resolve the too-small slab conflicts first"
              : duplicate > 0
                ? "Resolve the duplicate-slab conflicts first"
                : !allAssigned
                  ? "Assign every piece first"
                  : failingFit.length > 0
                    ? `Fix ${failingFit.length} manufacturing-fit issue(s) first`
                    : !fitResponse
                      ? "Running the manufacturing-fit check…"
                      : "Download a factory DXF cut plan"
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

      {/* Manufacturing tolerances — controls the blade kerf, edge
          trim, and dimensional tolerance the factory-fit check +
          DXF writer apply. Editing any value re-runs the preflight
          check on the next App tick. */}
      <div className="step4-mfg">
        <div className="step4-mfg-head">
          <span className="step4-mfg-title">Manufacturing tolerances</span>
          <span className={
            "step4-mfg-badge "
            + (fitChecking
              ? "step4-mfg-badge-checking"
              : fitResponse === null
                ? "step4-mfg-badge-pending"
                : fitResponse.factory_ready
                  ? "step4-mfg-badge-ready"
                  : "step4-mfg-badge-blocked")
          }>
            {fitChecking
              ? "Checking…"
              : fitResponse === null
                ? "Idle"
                : fitResponse.factory_ready
                  ? "Factory-ready"
                  : `${failingFit.length} blocked`}
          </span>
        </div>
        <div className="step4-mfg-inputs">
          <PolicyInput
            label="Blade kerf"
            value={manufacturingPolicy.blade_kerf_mm}
            onChange={(v) => onPolicyChange({
              ...manufacturingPolicy, blade_kerf_mm: v,
            })}
          />
          <PolicyInput
            label="Edge trim"
            value={manufacturingPolicy.edge_trim_mm}
            onChange={(v) => onPolicyChange({
              ...manufacturingPolicy, edge_trim_mm: v,
            })}
          />
          <PolicyInput
            label="Tolerance"
            value={manufacturingPolicy.tolerance_mm}
            onChange={(v) => onPolicyChange({
              ...manufacturingPolicy, tolerance_mm: v,
            })}
          />
        </div>
        {fitError && (
          <div className="step4-mfg-error">
            Fit check failed: {fitError}
          </div>
        )}
        {failingFit.length > 0 && (
          <ul className="step4-mfg-issues">
            {failingFit.slice(0, 6).map((r) => (
              <li
                key={`${r.piece_id}:${r.slab_id}`}
                className={`step4-mfg-issue step4-mfg-issue-${r.verdict}`}
                title={r.reason}
              >
                <span className="step4-mfg-issue-piece">{r.piece_id}</span>
                <span className="step4-mfg-issue-verdict">{r.verdict}</span>
                <span className="step4-mfg-issue-margin">
                  margin {r.margin_width_mm.toFixed(1)}
                  {" × "}
                  {r.margin_height_mm.toFixed(1)} mm
                </span>
              </li>
            ))}
            {failingFit.length > 6 && (
              <li className="step4-mfg-issue-more">
                +{failingFit.length - 6} more…
              </li>
            )}
          </ul>
        )}
      </div>
    </div>
  );
}


function PolicyInput({
  label, value, onChange,
}: {
  label: string;
  value: number;
  onChange: (next: number) => void;
}) {
  return (
    <label className="step4-mfg-input">
      <span className="step4-mfg-input-label">{label}</span>
      <span className="step4-mfg-input-field">
        <input
          type="number"
          min={0}
          step={0.5}
          value={value}
          onChange={(e) => {
            const parsed = Number.parseFloat(e.currentTarget.value);
            onChange(Number.isFinite(parsed) ? Math.max(0, parsed) : 0);
          }}
        />
        <span className="step4-mfg-input-unit">mm</span>
      </span>
    </label>
  );
}


// 0.1.42 perf: memoised. Assignment counts only change on
// explicit user action so this skips work on every App tick.
const Step4ExportBar = memo(Step4ExportBarImpl);
export default Step4ExportBar;
