// Step 3 — "Upload Slab Data" panel (V1 redesign).
//
// Designer flow:
//   1. Pick the Excel inventory file
//   2. Pick the slab photos
//   3. Click "Upload & parse"
//   4. Review the Inventory Summary card the backend returns
//   5. Continue to Step 4
//
// Working-slab regeneration lives on Step 1 now — Step 3 stays
// focused on the upload + parse experience. Filesystem paths and
// other operator-grade detail are tucked behind a collapsible
// "Developer details" disclosure so the main surface stays clean.

import { useRef, useState } from "react";

import PanelCard from "./ui/PanelCard";
import StatusPill from "./ui/StatusPill";
import {
  clearUploadedInventory, fetchInventoryInfo, uploadInventory,
} from "../lib/api";
import type {
  InventoryInfo, InventoryPreviewRow, InventoryUploadSummary,
} from "../lib/types";


interface Props {
  info: InventoryInfo | null;
  infoError: string | null;
  /** Set by App after a successful upload so the rest of the app
   *  refreshes (matcher, Step-4 chip) without a full reload. */
  onInfoChange: (info: InventoryInfo | null) => void;
  uploadSummary: InventoryUploadSummary | null;
  uploadExcelName: string | null;
  uploadImageCount: number;
  onUploadResult: (
    summary: InventoryUploadSummary | null,
    excelName: string | null,
    imageCount: number,
  ) => void;
  onContinue: () => void;
  canContinue: boolean;
}

export default function Step3InventoryPanel({
  info, infoError, onInfoChange,
  uploadSummary, uploadExcelName, uploadImageCount, onUploadResult,
  onContinue, canContinue,
}: Props) {
  const xlsxRef = useRef<HTMLInputElement>(null);
  const imagesRef = useRef<HTMLInputElement>(null);

  const [excelFile, setExcelFile] = useState<File | null>(null);
  const [imageFiles, setImageFiles] = useState<File[]>([]);
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [devOpen, setDevOpen] = useState(false);

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
  const continueEnabled = canContinue && hasValidSlabs;

  return (
    <div className="step3 step3-v1">
      <header className="step3-header">
        <h2 className="step3-title">Step 3 · Upload slab data</h2>
        <p className="step3-caption">
          Pick your Excel inventory and the slab photos. We match
          photos to rows by filename suffix and produce a clean
          inventory you can assign in Step 4.
        </p>
      </header>

      <div className="step3-grid">
        <PanelCard
          title="Slab inventory (Excel)"
          icon={<UploadGlyph kind="sheet" />}
          headerAction={excelFile
            ? <StatusPill tone="green">selected</StatusPill>
            : <StatusPill tone="grey">required</StatusPill>}
        >
          <div className="step3-upload-body">
            <input
              ref={xlsxRef}
              type="file"
              accept=".xlsx,.xls"
              className="step3-hidden-input"
              onChange={(e) => setExcelFile(e.target.files?.[0] ?? null)}
            />
            <button
              type="button"
              className="step3-choose-btn"
              onClick={() => xlsxRef.current?.click()}
              disabled={uploading}
            >
              <UploadGlyph kind="sheet" />
              <span>
                {excelFile ? "Choose a different file" : "Choose Excel file"}
              </span>
            </button>
            {excelFile ? (
              <div className="step3-file-line">
                <span className="step3-file-name" title={excelFile.name}>
                  {excelFile.name}
                </span>
                <span className="step3-file-size">
                  {(excelFile.size / 1024).toFixed(0)} KB
                </span>
              </div>
            ) : (
              <p className="step3-hint">
                <strong>.xlsx</strong> or <strong>.xls</strong> · one slab
                per row with width, height, and an ID column.
              </p>
            )}
          </div>
        </PanelCard>

        <PanelCard
          title="Slab photos"
          icon={<UploadGlyph kind="image" />}
          headerAction={imageFiles.length > 0
            ? <StatusPill tone="green">
                {imageFiles.length}{" "}selected
              </StatusPill>
            : <StatusPill tone="grey">optional</StatusPill>}
        >
          <div className="step3-upload-body">
            <input
              ref={imagesRef}
              type="file"
              accept="image/*"
              multiple
              className="step3-hidden-input"
              onChange={(e) => setImageFiles(Array.from(e.target.files ?? []))}
            />
            <button
              type="button"
              className="step3-choose-btn"
              onClick={() => imagesRef.current?.click()}
              disabled={uploading}
            >
              <UploadGlyph kind="image" />
              <span>
                {imageFiles.length > 0
                  ? "Choose different photos"
                  : "Choose slab photos"}
              </span>
            </button>
            {imageFiles.length > 0 ? (
              <div className="step3-file-line">
                <span className="step3-file-name">
                  {imageFiles.length}{" "}image
                  {imageFiles.length === 1 ? "" : "s"} ready to upload
                </span>
              </div>
            ) : (
              <p className="step3-hint">
                Filenames should end with the slab id or serial so we
                can match each photo to its row.
              </p>
            )}
          </div>
        </PanelCard>
      </div>

      <div className="step3-parse-bar">
        <div className="step3-parse-info">
          <strong>Ready when you are.</strong> Upload runs the parse +
          photo-match pipeline and produces the inventory you'll
          assign in Step 4.
        </div>
        <div className="step3-parse-actions">
          {summary && (
            <button
              type="button"
              className="step3-secondary-btn"
              onClick={onClearUpload}
              disabled={uploading}
              title="Discard the uploaded inventory"
            >
              Remove upload
            </button>
          )}
          <button
            type="button"
            className="step3-primary-btn"
            onClick={onSubmit}
            disabled={uploading || !excelFile}
          >
            {uploading ? "Uploading…" : "Upload & parse"}
          </button>
        </div>
      </div>
      {uploadError && (
        <div className="step3-error" role="alert">{uploadError}</div>
      )}

      {summary && (
        <PanelCard
          title="Inventory summary"
          icon={<DocGlyph />}
          headerAction={
            summary.valid_slabs > 0
              ? <StatusPill tone="green">validated</StatusPill>
              : <StatusPill tone="red">no valid slabs</StatusPill>
          }
        >
          <div className="step3-summary">
            {excelName && (
              <div className="step3-summary-source">
                <span className="step3-summary-source-label">Excel</span>
                <span
                  className="step3-summary-source-name"
                  title={excelName}
                >
                  {excelName}
                </span>
              </div>
            )}
            <div className="step3-summary-stats">
              <SummaryStat
                label="Slabs detected"
                value={summary.valid_slabs}
                tone={summary.valid_slabs > 0 ? "green" : "red"}
              />
              <SummaryStat
                label="Invalid rows"
                value={summary.invalid_slabs}
                tone={summary.invalid_slabs > 0 ? "amber" : "grey"}
              />
              <SummaryStat
                label="Photos uploaded"
                value={imageCount}
                tone="blue"
              />
              <SummaryStat
                label="Photos matched"
                value={summary.linked_photos}
                tone={
                  imageCount === 0
                    ? "grey"
                    : summary.linked_photos === imageCount
                      ? "green" : "amber"
                }
              />
            </div>

            {(summary.unmatched_photos.length > 0
              || summary.slabs_without_photos.length > 0) && (
              <div className="step3-issue-list">
                {summary.unmatched_photos.length > 0 && (
                  <div className="step3-issue">
                    <StatusPill tone="amber">
                      {summary.unmatched_photos.length}{" "}unmatched
                    </StatusPill>
                    <span className="step3-issue-text">
                      photo{summary.unmatched_photos.length === 1 ? "" : "s"}
                      {" "}couldn't be linked to a row
                      {summary.unmatched_photos.length <= 3
                        && ` (${summary.unmatched_photos.join(", ")})`}.
                    </span>
                  </div>
                )}
                {summary.slabs_without_photos.length > 0 && (
                  <div className="step3-issue">
                    <StatusPill tone="grey">
                      {summary.slabs_without_photos.length}{" "}no photo
                    </StatusPill>
                    <span className="step3-issue-text">
                      slab{summary.slabs_without_photos.length === 1 ? "" : "s"}
                      {" "}upload without an image — still assignable in Step 4.
                    </span>
                  </div>
                )}
              </div>
            )}

            {summary.preview.length > 0 && (
              <details className="step3-preview">
                <summary>
                  Preview · {summary.preview.length} of {summary.total_rows} rows
                </summary>
                <InventoryPreviewTable rows={summary.preview} />
              </details>
            )}
          </div>
        </PanelCard>
      )}

      <details
        className="step3-dev-details"
        open={devOpen}
        onToggle={(e) => setDevOpen((e.target as HTMLDetailsElement).open)}
      >
        <summary>Developer details</summary>
        <div className="step3-dev-body">
          {infoError && (
            <div className="step3-dev-row step3-dev-row-error">
              {infoError}
            </div>
          )}
          {info && (
            <>
              <div className="step3-dev-row">
                <span className="step3-dev-label">Active source</span>
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
              <div className="step3-dev-row">
                <span className="step3-dev-label">Valid / skipped</span>
                <span>
                  {info.valid_count} / {info.skipped_count}
                </span>
              </div>
              <div className="step3-dev-row">
                <span className="step3-dev-label">File path</span>
                <code className="step3-dev-path">{info.source_path}</code>
              </div>
              {summary && (
                <div className="step3-dev-row">
                  <span className="step3-dev-label">Mapped columns</span>
                  <code className="step3-dev-mono">
                    {Object.entries(summary.mapped_columns)
                      .map(([k, v]) => `${k}=${v}`)
                      .join(" · ") || "—"}
                  </code>
                </div>
              )}
            </>
          )}
        </div>
      </details>

      <div className="step3-footer">
        <button
          type="button"
          className="step3-primary-btn step3-continue-btn"
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
        {!continueEnabled && (
          <p className="step3-footer-hint">
            Upload at least one valid slab to unlock Step 4.
          </p>
        )}
      </div>
    </div>
  );
}


function SummaryStat({
  label, value, tone,
}: { label: string; value: number; tone: "green" | "amber" | "red" | "blue" | "grey" }) {
  return (
    <div className={`step3-stat step3-stat-${tone}`}>
      <span className="step3-stat-value">{value}</span>
      <span className="step3-stat-label">{label}</span>
    </div>
  );
}


function InventoryPreviewTable({ rows }: { rows: InventoryPreviewRow[] }) {
  return (
    <div className="step3-preview-wrap">
      <table className="step3-preview-table">
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
            const invalid = !(r.width_mm && r.height_mm);
            return (
              <tr key={`prev-${i}`}>
                <td className="step3-preview-mono" title={r.slab_id ?? ""}>
                  {r.slab_id ?? r.serial_number ?? r.item_code ?? "(no id)"}
                </td>
                <td>{dims}</td>
                <td>
                  {r.image_found
                    ? <StatusPill tone="blue">photo</StatusPill>
                    : <StatusPill tone="grey">none</StatusPill>}
                </td>
                <td>
                  {invalid
                    ? <StatusPill tone="red">invalid</StatusPill>
                    : !r.image_found
                      ? <StatusPill tone="amber">no photo</StatusPill>
                      : <StatusPill tone="green">ok</StatusPill>}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
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


function UploadGlyph({ kind }: { kind: "sheet" | "image" }) {
  if (kind === "sheet") {
    return (
      <svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true">
        <rect
          x="3" y="2" width="10" height="12" rx="1.4"
          fill="none" stroke="currentColor" strokeWidth="1.4"
        />
        <line x1="5.5" y1="6" x2="10.5" y2="6"
          stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
        <line x1="5.5" y1="9" x2="10.5" y2="9"
          stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
        <line x1="5.5" y1="12" x2="9" y2="12"
          stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
      </svg>
    );
  }
  return (
    <svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true">
      <rect
        x="2" y="3" width="12" height="10" rx="1.4"
        fill="none" stroke="currentColor" strokeWidth="1.4"
      />
      <circle cx="6" cy="7" r="1.2" fill="currentColor" />
      <path
        d="M2.6 13 L7 8.5 L10 11 L13.4 8 L13.4 13 Z"
        fill="currentColor" opacity="0.85"
      />
    </svg>
  );
}


function DocGlyph() {
  return (
    <svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true">
      <path
        d="M3 2 H9.5 L13 5.5 V14 H3 Z"
        fill="none" stroke="currentColor" strokeWidth="1.4"
        strokeLinejoin="round"
      />
      <path d="M9.5 2 V5.5 H13"
        fill="none" stroke="currentColor" strokeWidth="1.4" />
      <line x1="5.5" y1="9" x2="10.5" y2="9"
        stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
      <line x1="5.5" y1="11.5" x2="10.5" y2="11.5"
        stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
    </svg>
  );
}
