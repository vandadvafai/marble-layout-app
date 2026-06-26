// Step 1 — "Upload Plan" panel (V1 redesign).
//
// Two stacked cards:
//
//   1. "Choose a plan" — DXF upload shell + sample-plan picker (the
//      backend upload endpoint is still scaffolded; the sample
//      picker is the working path today).
//   2. "Project summary" — once a plan is loaded, shows the project
//      name, piece count, and the working slab size. When a Step-3
//      inventory is active, the designer can regenerate the layout
//      against the inventory's median slab size right here (this
//      card replaces the working-slab section that used to live in
//      Step 3).

import { useRef, useState } from "react";

import PanelCard from "./ui/PanelCard";
import StatusPill from "./ui/StatusPill";
import type {
  DemoIndexEntry, InventoryStats,
} from "../lib/types";


interface Props {
  demos: DemoIndexEntry[];
  selectedDemoId: string | null;
  selectedLabel: string | null;
  pieceCount: number | null;
  /** Current tile choice (set when the designer regenerated via the
   *  inventory). Null until then; the panel then falls back to the
   *  layout's raw tile size for display. */
  tileChoice: {
    tile_width_mm: number;
    tile_height_mm: number;
    basis: "explicit_override" | "inventory_median";
  } | null;
  tileWidthMm: number | null;
  tileHeightMm: number | null;
  /** Dimension stats from the active inventory, when one is loaded.
   *  Drives the optional "Regenerate from inventory" affordance. */
  inventoryStats: InventoryStats | null;
  onRegenerateLayout: (
    tile?: { tile_width_mm: number; tile_height_mm: number },
  ) => Promise<void>;
  loading: boolean;
  onPickSample: (demo_id: string) => void;
  onContinue: () => void;
  canContinue: boolean;
}

export default function Step1PlanPanel({
  demos, selectedDemoId, selectedLabel, pieceCount,
  tileChoice, tileWidthMm, tileHeightMm,
  inventoryStats,
  onRegenerateLayout,
  loading,
  onPickSample, onContinue, canContinue,
}: Props) {
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [pickedFileName, setPickedFileName] = useState<string | null>(null);
  const [regenerating, setRegenerating] = useState(false);
  const [regenerateError, setRegenerateError] = useState<string | null>(null);

  const selectedDemo = demos.find((d) => d.demo_id === selectedDemoId) ?? null;
  const planLoaded = selectedDemo !== null;

  const onRegenerate = async () => {
    if (!inventoryStats) return;
    setRegenerateError(null);
    setRegenerating(true);
    try {
      await onRegenerateLayout({
        tile_width_mm: inventoryStats.median_width_mm,
        tile_height_mm: inventoryStats.median_height_mm,
      });
    } catch (e) {
      setRegenerateError((e as Error).message);
    } finally {
      setRegenerating(false);
    }
  };

  return (
    <div className="step1 step1-v1">
      <header className="step1-header">
        <h2 className="step1-title">Step 1 · Upload plan</h2>
        <p className="step1-caption">
          Pick a DXF/CAD plan or start from a sample. Once a plan is
          loaded you'll see a project summary and can move to the
          editor.
        </p>
      </header>

      <PanelCard
        title="Choose a plan"
        icon={<PlanGlyph />}
        headerAction={planLoaded
          ? <StatusPill tone="green">loaded</StatusPill>
          : <StatusPill tone="grey">no plan yet</StatusPill>}
      >
        <div className="step1-upload">
          <input
            ref={fileInputRef}
            type="file"
            accept=".dxf,.dwg"
            className="step1-hidden-input"
            onChange={(e) => {
              const f = e.target.files?.[0];
              if (f) setPickedFileName(f.name);
            }}
          />
          <button
            type="button"
            className="step1-choose-btn"
            onClick={() => fileInputRef.current?.click()}
          >
            <PlanGlyph />
            <span>{pickedFileName ? "Choose a different DXF" : "Choose DXF…"}</span>
          </button>
          {pickedFileName && (
            <p className="step1-hint">
              <strong>{pickedFileName}</strong> · DXF upload isn't
              wired yet — pick a sample below to continue.
            </p>
          )}
        </div>

        <div className="step1-sample-section">
          <div className="step1-sample-section-label">
            Or start from a sample plan
          </div>
          <div className="step1-samples">
            {demos.length === 0 && (
              <div className="step1-empty">Loading sample plans…</div>
            )}
            {demos.map((d) => (
              <button
                key={d.demo_id}
                type="button"
                className={
                  "step1-sample"
                  + (d.demo_id === selectedDemoId
                    ? " step1-sample-active" : "")
                }
                onClick={() => onPickSample(d.demo_id)}
                disabled={loading}
              >
                <span className="step1-sample-label">{d.label}</span>
                <span className="step1-sample-id">{d.demo_id}</span>
              </button>
            ))}
          </div>
        </div>
      </PanelCard>

      {planLoaded && (
        <PanelCard
          title="Project summary"
          icon={<SummaryGlyph />}
          headerAction={
            <StatusPill tone="blue">
              {selectedLabel ?? selectedDemo?.label ?? "loaded"}
            </StatusPill>
          }
        >
          <div className="step1-summary">
            <div className="step1-summary-grid">
              <SummaryCell
                label="Pieces"
                value={pieceCount != null ? String(pieceCount) : "—"}
              />
              <SummaryCell
                label="Working slab"
                value={
                  tileWidthMm != null && tileHeightMm != null
                    ? `${(tileWidthMm / 10).toFixed(0)} × ${(tileHeightMm / 10).toFixed(0)} cm`
                    : "—"
                }
                aside={
                  tileChoice?.basis === "inventory_median"
                    ? "from inventory median"
                    : tileChoice?.basis === "explicit_override"
                      ? "custom"
                      : "plan default"
                }
              />
              {inventoryStats && (
                <>
                  <SummaryCell
                    label="Inventory size"
                    value={`${(inventoryStats.median_width_mm / 10).toFixed(0)} × ${(inventoryStats.median_height_mm / 10).toFixed(0)} cm`}
                    aside={`median of ${inventoryStats.slab_count} slabs`}
                  />
                  <SummaryCell
                    label="Inventory range"
                    value={
                      `${(inventoryStats.min_width_mm / 10).toFixed(0)}–`
                      + `${(inventoryStats.max_width_mm / 10).toFixed(0)} cm × `
                      + `${(inventoryStats.min_height_mm / 10).toFixed(0)}–`
                      + `${(inventoryStats.max_height_mm / 10).toFixed(0)} cm`
                    }
                  />
                </>
              )}
            </div>

            {inventoryStats?.is_inconsistent && (
              <div className="step1-summary-warn">
                Slab sizes vary by more than the median in at least one
                axis — a median-based layout may not fit every piece
                well. Consider a custom tile size.
              </div>
            )}

            {inventoryStats && (
              <div className="step1-summary-action">
                <button
                  type="button"
                  className="step1-secondary-btn"
                  onClick={onRegenerate}
                  disabled={regenerating}
                  title="Re-tile the layout using the inventory's median slab size"
                >
                  {regenerating
                    ? "Regenerating…"
                    : "Generate layout from inventory size"}
                </button>
                <p className="step1-summary-action-hint">
                  Replaces any in-progress seam edits for this plan.
                </p>
                {regenerateError && (
                  <div className="step1-error">{regenerateError}</div>
                )}
              </div>
            )}
          </div>
        </PanelCard>
      )}

      <div className="step1-footer">
        <button
          type="button"
          className="step1-primary-btn"
          onClick={onContinue}
          disabled={!canContinue}
          title={
            canContinue
              ? "Continue to plan editor"
              : "Pick a sample plan or upload a DXF first"
          }
        >
          Continue to editor →
        </button>
      </div>
    </div>
  );
}


function SummaryCell({
  label, value, aside,
}: { label: string; value: string; aside?: string }) {
  return (
    <div className="step1-summary-cell">
      <div className="step1-summary-cell-label">{label}</div>
      <div className="step1-summary-cell-value">{value}</div>
      {aside && (
        <div className="step1-summary-cell-aside">{aside}</div>
      )}
    </div>
  );
}


function PlanGlyph() {
  return (
    <svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true">
      <rect
        x="2.5" y="2.5" width="11" height="11" rx="1.6"
        fill="none" stroke="currentColor" strokeWidth="1.4"
      />
      <path
        d="M2.5 6 H8 V13.5 M11 2.5 V8 H13.5"
        fill="none" stroke="currentColor" strokeWidth="1.4"
        strokeLinecap="round"
      />
    </svg>
  );
}


function SummaryGlyph() {
  return (
    <svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true">
      <rect
        x="2.5" y="3" width="11" height="10" rx="1.6"
        fill="none" stroke="currentColor" strokeWidth="1.4"
      />
      <line x1="2.5" y1="6" x2="13.5" y2="6"
        stroke="currentColor" strokeWidth="1.4" />
      <line x1="5" y1="9" x2="11" y2="9"
        stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
      <line x1="5" y1="11" x2="9" y2="11"
        stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
    </svg>
  );
}
