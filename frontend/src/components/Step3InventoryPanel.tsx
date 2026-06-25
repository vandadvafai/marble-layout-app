// Step 3 — "Upload Slab Data" panel.
//
// Real upload flow (0.1.43): the user picks an Excel file + slab
// photos, the frontend POSTs them as multipart/form-data to
// /api/inventory/upload, and the backend runs the existing slab-
// intake pipeline (Excel parse → suffix-based photo match →
// clean_slabs.json). The active inventory then transparently becomes
// the upload — Step 4's matcher reads it directly.
//
// Falls back to the demo / real-export inventory when no upload has
// happened yet. Either way the "Active inventory" card at the
// bottom shows what's currently live so the designer knows which
// data set Step 4 will use.

import { useRef, useState } from "react";

import {
  clearUploadedInventory, fetchInventoryInfo, uploadInventory,
} from "../lib/api";
import type {
  InventoryInfo, InventoryPreviewRow, InventoryUploadSummary,
} from "../lib/types";


interface Props {
  info: InventoryInfo | null;
  infoError: string | null;
  /** Set by App after a successful upload so we can refresh the
   *  info chip + Step 4 match without a full reload. */
  onInfoChange: (info: InventoryInfo | null) => void;
  /** Lifted state — App owns the upload summary so it survives
   *  Step 3 ↔ Step 4 round-trips (the panel remounts but the
   *  summary persists). */
  uploadSummary: InventoryUploadSummary | null;
  uploadExcelName: string | null;
  uploadImageCount: number;
  onUploadResult: (
    summary: InventoryUploadSummary | null,
    excelName: string | null,
    imageCount: number,
  ) => void;
  /** Regenerate the editable layout using the supplied tile
   *  dimensions (typically the active inventory's median size).
   *  No-arg call lets the backend choose inventory-median. After
   *  the call resolves, App navigates the wizard to Step 2. */
  onRegenerateLayout: (
    tile?: { tile_width_mm: number; tile_height_mm: number },
  ) => Promise<void>;
  onContinue: () => void;
  canContinue: boolean;
}

export default function Step3InventoryPanel({
  info, infoError, onInfoChange,
  uploadSummary, uploadExcelName, uploadImageCount, onUploadResult,
  onRegenerateLayout, onContinue, canContinue,
}: Props) {
  const [regenerating, setRegenerating] = useState(false);
  const [regenerateError, setRegenerateError] = useState<string | null>(null);
  const stats = info?.stats ?? null;
  const onRegenerate = async () => {
    if (!stats) return;
    setRegenerateError(null);
    setRegenerating(true);
    try {
      await onRegenerateLayout({
        tile_width_mm: stats.median_width_mm,
        tile_height_mm: stats.median_height_mm,
      });
    } catch (e) {
      setRegenerateError((e as Error).message);
    } finally {
      setRegenerating(false);
    }
  };
  const xlsxRef = useRef<HTMLInputElement>(null);
  const imagesRef = useRef<HTMLInputElement>(null);

  const [excelFile, setExcelFile] = useState<File | null>(null);
  const [imageFiles, setImageFiles] = useState<File[]>([]);
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const summary = uploadSummary;
  const excelName = uploadExcelName;
  const imageCount = uploadImageCount;

  const onSubmit = async () => {
    if (!excelFile) {
      setUploadError("Pick an Excel file first.");
      return;
    }
    setUploadError(null);
    setUploading(true);
    try {
      const res = await uploadInventory(excelFile, imageFiles);
      onUploadResult(res.summary, res.excel_filename, res.image_count);
      // Refresh the resolved-inventory-source chip so the rest of
      // the app sees the new upload immediately.
      const fresh = await fetchInventoryInfo();
      onInfoChange(fresh);
    } catch (e) {
      setUploadError((e as Error).message);
    } finally {
      setUploading(false);
    }
  };

  const onClearUpload = async () => {
    setUploading(true);
    try {
      await clearUploadedInventory();
      onUploadResult(null, null, 0);
      setExcelFile(null);
      setImageFiles([]);
      const fresh = await fetchInventoryInfo();
      onInfoChange(fresh);
    } catch (e) {
      setUploadError((e as Error).message);
    } finally {
      setUploading(false);
    }
  };

  const hasValidSlabs = summary !== null && summary.valid_slabs > 0;
  // Step 4 export now requires an explicit upload from the designer
  // (the fallback inventory is sample data and must NOT be used to
  // produce a factory cut plan). The gate above (``canContinue``) is
  // the same predicate; we keep this local check so the tooltip can
  // explain WHICH side is failing.
  const continueEnabled = canContinue && hasValidSlabs;

  return (
    <div className="step-panel step-panel-3">
      <div className="step-panel-intro">
        <h2 className="step-panel-title">Step 3 · Upload slab data</h2>
        <p className="step-panel-caption">
          Upload your slab inventory Excel and slab photos. The
          server matches photos to Excel rows using filename
          suffixes (same scheme the project's slab-intake pipeline
          uses). If you skip the upload, the matcher uses the
          fallback inventory shown below.
        </p>
      </div>

      <div className="step-panel-card">
        <div className="step-panel-card-title">1 · Slab inventory (Excel)</div>
        <input
          ref={xlsxRef}
          type="file"
          accept=".xlsx,.xls"
          className="step-panel-file-input"
          onChange={(e) => {
            const f = e.target.files?.[0] ?? null;
            setExcelFile(f);
          }}
        />
        <button
          type="button"
          className="step-panel-btn"
          onClick={() => xlsxRef.current?.click()}
          disabled={uploading}
        >
          {excelFile ? "Choose different Excel…" : "Choose Excel…"}
        </button>
        {excelFile && (
          <div className="step-panel-file-status">
            Selected: <strong>{excelFile.name}</strong>
            <span className="step-panel-file-note">
              {" "}({Math.round(excelFile.size / 1024)} KB)
            </span>
          </div>
        )}
      </div>

      <div className="step-panel-card">
        <div className="step-panel-card-title">2 · Slab photos</div>
        <input
          ref={imagesRef}
          type="file"
          accept="image/*"
          multiple
          className="step-panel-file-input"
          onChange={(e) => {
            setImageFiles(Array.from(e.target.files ?? []));
          }}
        />
        <button
          type="button"
          className="step-panel-btn"
          onClick={() => imagesRef.current?.click()}
          disabled={uploading}
        >
          {imageFiles.length > 0
            ? "Choose different photos…"
            : "Choose images…"}
        </button>
        {imageFiles.length > 0 && (
          <div className="step-panel-file-status">
            Selected: <strong>{imageFiles.length}</strong>{" "}
            image{imageFiles.length === 1 ? "" : "s"}
          </div>
        )}
      </div>

      <div className="step-panel-card">
        <div className="step-panel-card-title">3 · Upload</div>
        <div className="step-panel-row">
          <button
            type="button"
            className="step-panel-primary"
            onClick={onSubmit}
            disabled={uploading || !excelFile}
          >
            {uploading ? "Uploading…" : "Upload & parse"}
          </button>
          {summary && (
            <button
              type="button"
              className="step-panel-btn"
              onClick={onClearUpload}
              disabled={uploading}
              title="Discard the uploaded inventory and return to fallback"
            >
              Remove upload
            </button>
          )}
        </div>
        {uploadError && (
          <div className="step-panel-error">{uploadError}</div>
        )}
      </div>

      {summary && (
        <div className="step-panel-card">
          <div className="step-panel-card-title">
            Upload result {excelName ? `· ${excelName}` : ""}
          </div>
          <div className="step-panel-info-row">
            <span className="step-panel-info-label">Total rows</span>
            <span className="step-panel-info-value">{summary.total_rows}</span>
          </div>
          <div className="step-panel-info-row">
            <span className="step-panel-info-label">Valid slabs</span>
            <span className="step-panel-info-value">{summary.valid_slabs}</span>
          </div>
          <div className="step-panel-info-row">
            <span className="step-panel-info-label">Invalid / skipped</span>
            <span className={
              "step-panel-info-value"
              + (summary.invalid_slabs > 0 ? " step-panel-info-warn" : "")
            }>
              {summary.invalid_slabs}
            </span>
          </div>
          <div className="step-panel-info-row">
            <span className="step-panel-info-label">Photos uploaded</span>
            <span className="step-panel-info-value">{imageCount}</span>
          </div>
          <div className="step-panel-info-row">
            <span className="step-panel-info-label">Linked photos</span>
            <span className="step-panel-info-value">{summary.linked_photos}</span>
          </div>
          {summary.unmatched_photos.length > 0 && (
            <div className="step-panel-info-row step-panel-info-warn">
              <span className="step-panel-info-label">Unmatched photos</span>
              <span className="step-panel-info-value">
                {summary.unmatched_photos.length}{" "}
                <span className="step-panel-file-note">
                  ({summary.unmatched_photos.slice(0, 3).join(", ")}
                  {summary.unmatched_photos.length > 3 ? ", …" : ""})
                </span>
              </span>
            </div>
          )}
          {summary.slabs_without_photos.length > 0 && (
            <div className="step-panel-info-row">
              <span className="step-panel-info-label">Slabs without photos</span>
              <span className="step-panel-info-value">
                {summary.slabs_without_photos.length}
              </span>
            </div>
          )}
          {Object.keys(summary.mapped_columns).length > 0 && (
            <div className="step-panel-info-row">
              <span className="step-panel-info-label">Mapped columns</span>
              <span className="step-panel-info-value step-panel-info-mono">
                {Object.values(summary.mapped_columns).join(" · ")}
              </span>
            </div>
          )}

          {summary.preview.length > 0 && (
            <>
              <div className="step-panel-card-subtitle">
                Inventory preview ({summary.preview.length} of {summary.total_rows})
              </div>
              <InventoryPreviewTable rows={summary.preview} />
            </>
          )}
        </div>
      )}

      <div className="step-panel-card">
        <div className="step-panel-card-title">Active inventory</div>
        {infoError && (
          <div className="step-panel-error">{infoError}</div>
        )}
        {!infoError && !info && (
          <div className="step-panel-empty">Loading inventory…</div>
        )}
        {info && (
          <>
            <div className="step-panel-info-row">
              <span className="step-panel-info-label">Source</span>
              <span
                className={
                  "inventory-source-chip "
                  + sourceClassFor(info.source_label)
                }
                title={info.source_description}
              >
                {labelToChip(info.source_label)}
              </span>
            </div>
            <div className="step-panel-info-row">
              <span className="step-panel-info-label">Valid slabs</span>
              <span className="step-panel-info-value">{info.valid_count}</span>
            </div>
            <div className="step-panel-info-row">
              <span className="step-panel-info-label">Invalid / skipped</span>
              <span className={
                "step-panel-info-value"
                + (info.skipped_count > 0 ? " step-panel-info-warn" : "")
              }>
                {info.skipped_count}
              </span>
            </div>
            <div className="step-panel-info-row">
              <span className="step-panel-info-label">File</span>
              <span className="step-panel-info-path">{info.source_path}</span>
            </div>
          </>
        )}
      </div>

      {/* Working slab size — derived from the active inventory's
          dimension statistics. Drives the editable-layout grid in
          Step 2 once the designer clicks the regenerate button. */}
      {stats && (
        <div className="step-panel-card">
          <div className="step-panel-card-title">Working slab size</div>
          <div className="step-panel-info-row">
            <span className="step-panel-info-label">Median</span>
            <span className="step-panel-info-value">
              <strong>
                {(stats.median_width_mm / 10).toFixed(0)}
                {" × "}
                {(stats.median_height_mm / 10).toFixed(0)}
                {" cm"}
              </strong>
              <span className="step-panel-file-note">
                {" "}({stats.median_width_mm.toFixed(0)} ×{" "}
                {stats.median_height_mm.toFixed(0)} mm)
              </span>
            </span>
          </div>
          <div className="step-panel-info-row">
            <span className="step-panel-info-label">Average</span>
            <span className="step-panel-info-value">
              {(stats.mean_width_mm / 10).toFixed(0)}
              {" × "}
              {(stats.mean_height_mm / 10).toFixed(0)}
              {" cm"}
            </span>
          </div>
          <div className="step-panel-info-row">
            <span className="step-panel-info-label">Range (W × H)</span>
            <span className="step-panel-info-value">
              {(stats.min_width_mm / 10).toFixed(0)}–
              {(stats.max_width_mm / 10).toFixed(0)} cm
              {" × "}
              {(stats.min_height_mm / 10).toFixed(0)}–
              {(stats.max_height_mm / 10).toFixed(0)} cm
            </span>
          </div>
          <div className="step-panel-info-row">
            <span className="step-panel-info-label">Used slabs</span>
            <span className="step-panel-info-value">{stats.slab_count}</span>
          </div>
          {stats.is_inconsistent && (
            <div className="step-panel-info-row step-panel-info-warn">
              <span className="step-panel-info-label">⚠ inconsistent</span>
              <span className="step-panel-info-value">
                Slab sizes vary by more than the median in at least one axis —
                median-based layout may not fit every piece well.
              </span>
            </div>
          )}
          <div className="step-panel-row" style={{ marginTop: 8 }}>
            <button
              type="button"
              className="step-panel-primary"
              onClick={onRegenerate}
              disabled={regenerating}
              title="Re-tile the layout using the median slab size"
            >
              {regenerating
                ? "Regenerating…"
                : "Generate layout from inventory size"}
            </button>
            <span className="step-panel-file-note">
              Replaces any in-progress seam edits for this plan.
            </span>
          </div>
          {regenerateError && (
            <div className="step-panel-error">{regenerateError}</div>
          )}
        </div>
      )}

      <div className="step-panel-actions">
        <button
          type="button"
          className="step-panel-primary"
          onClick={onContinue}
          disabled={!continueEnabled}
          title={
            continueEnabled
              ? "Continue to slab assignment"
              : "Upload at least one valid slab to continue"
          }
        >
          Continue to assignment →
        </button>
      </div>
    </div>
  );
}


function InventoryPreviewTable({ rows }: { rows: InventoryPreviewRow[] }) {
  return (
    <table className="step-panel-preview-table">
      <thead>
        <tr>
          <th>Slab</th>
          <th>Dimensions</th>
          <th>Photo</th>
          <th>Status</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((r, i) => {
          const dims = r.width_cm && r.height_cm
            ? `${r.width_cm} × ${r.height_cm} cm`
            : "—";
          const photo = r.image_found
            ? r.image_filename ?? "(linked)"
            : "—";
          const invalid = !(r.width_mm && r.height_mm);
          return (
            <tr key={`prev-${i}`}>
              <td className="step-panel-info-mono" title={r.slab_id ?? ""}>
                {r.slab_id ?? r.serial_number ?? r.item_code ?? "(no id)"}
              </td>
              <td>{dims}</td>
              <td className="step-panel-info-mono">{photo}</td>
              <td>
                {invalid ? (
                  <span className="step-panel-info-warn">invalid</span>
                ) : !r.image_found ? (
                  <span className="step-panel-info-warn-soft">no photo</span>
                ) : (
                  <span className="step-panel-info-ok">ok</span>
                )}
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}


function sourceClassFor(label: string): string {
  return {
    uploaded: "inventory-source-uploaded",
    env_override: "inventory-source-env",
    real_inventory: "inventory-source-real",
    demo_fallback: "inventory-source-demo",
  }[label] ?? "inventory-source-other";
}

function labelToChip(label: string): string {
  return {
    uploaded: "uploaded",
    env_override: "env override",
    real_inventory: "real",
    demo_fallback: "demo fallback",
  }[label] ?? label;
}
