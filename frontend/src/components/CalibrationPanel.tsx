// Calibration workflow view for Step 3 (M4).
//
// Every slab the calibration pipeline touched lands in exactly one
// of four groups the operator must resolve before Step 4 unlocks:
// Approved, Needs Review, Missing Photo, Rejected. Dimensions shown
// are read verbatim off the backend's ``CalibrationRecord`` — this
// component never re-derives the 20 mm/side usable-dimension math
// itself, keeping the backend the single source of truth for slab
// geometry (see ``placement_engine/calibration/policy.py``).

import { useRef, useState } from "react";

import PanelCard from "./ui/PanelCard";
import StatusPill, { type StatusTone } from "./ui/StatusPill";
import {
  calibrationImageUrl, replaceSlabImage, setCalibrationStatus,
} from "../lib/api";
import type {
  CalibrationCounts,
  CalibrationRecord,
  CalibrationStatus as CalibStatus,
  SourceType,
} from "../lib/types";

interface Props {
  records: CalibrationRecord[];
  counts: CalibrationCounts;
  /** Called with the full, updated records list after any quick
   *  action (approve / reject / add photo) resolves. */
  onRecordsChange: (records: CalibrationRecord[]) => void;
  /** Opens the manual-review modal for this slab. */
  onReview: (record: CalibrationRecord) => void;
}

const GROUP_ORDER: {
  status: CalibStatus; label: string; icon: string; tone: StatusTone;
}[] = [
  { status: "approved", label: "Approved", icon: "✅", tone: "green" },
  { status: "needs_review", label: "Needs Review", icon: "\u{1F7E1}", tone: "amber" },
  { status: "missing_photo", label: "Missing Photo", icon: "\u{1F534}", tone: "red" },
  { status: "rejected", label: "Rejected", icon: "⚫", tone: "grey" },
];

const STATUS_LABELS: Record<CalibStatus, string> = {
  approved: "Approved",
  needs_review: "Needs review",
  missing_photo: "Missing photo",
  rejected: "Rejected",
};

const SOURCE_LABELS: Record<SourceType, string> = {
  green_boundary: "Existing green boundary",
  scanned_crop: "Already scanned crop",
  raw_photo: "Raw photograph",
  no_photo: "No photo",
};

function blockerClauses(counts: CalibrationCounts): string[] {
  const clauses: string[] = [];
  if (counts.needs_review > 0) {
    clauses.push(
      `${counts.needs_review} need${counts.needs_review === 1 ? "s" : ""} review`,
    );
  }
  if (counts.missing_photo > 0) {
    clauses.push(`${counts.missing_photo} missing a photo`);
  }
  return clauses;
}

export default function CalibrationPanel({
  records, counts, onRecordsChange, onReview,
}: Props) {
  const total = records.length;
  const blockers = counts.needs_review + counts.missing_photo;
  const ready = blockers === 0 && counts.approved > 0;

  const [busySlabId, setBusySlabId] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  const updateRecord = (updated: CalibrationRecord) => {
    onRecordsChange(
      records.map((r) => (r.slab_id === updated.slab_id ? updated : r)),
    );
  };

  const runAction = async (
    slabId: string, action: () => Promise<{ record: CalibrationRecord }>,
  ) => {
    setActionError(null);
    setBusySlabId(slabId);
    try {
      const { record } = await action();
      updateRecord(record);
    } catch (e) {
      setActionError((e as Error).message);
    } finally {
      setBusySlabId(null);
    }
  };

  const groups = GROUP_ORDER.map((g) => ({
    ...g,
    rows: records.filter((r) => r.calibration_status === g.status),
  }));

  return (
    <PanelCard
      title="Calibration"
      headerAction={
        <StatusPill tone={ready ? "green" : "amber"}>
          {counts.approved}/{total} approved
        </StatusPill>
      }
    >
      <div className="calibration-summary">
        {ready ? (
          <p className="calibration-summary-line calibration-summary-ready">
            All slabs resolved — ready for Step 4.
          </p>
        ) : (
          <p className="calibration-summary-line">
            <strong>{blockers}</strong> slab{blockers === 1 ? "" : "s"}{" "}
            still blocking Step 4
            {blockerClauses(counts).map((clause, i) => (
              <span key={clause}>{i === 0 ? " — " : ", "}{clause}</span>
            ))}.
          </p>
        )}
      </div>

      {actionError && (
        <div className="calibration-error" role="alert">{actionError}</div>
      )}

      {groups.map((g) => g.rows.length > 0 && (
        <div className="calibration-group" key={g.status}>
          <div className="calibration-group-head">
            <span aria-hidden="true">{g.icon}</span>
            <span className="calibration-group-title">{g.label}</span>
            <span className="calibration-group-count">{g.rows.length}</span>
          </div>
          <div className="calibration-rows">
            {g.rows.map((r) => (
              <CalibrationRow
                key={r.slab_id}
                record={r}
                tone={g.tone}
                busy={busySlabId === r.slab_id}
                onApprove={() => runAction(
                  r.slab_id, () => setCalibrationStatus(r.slab_id, "approved"),
                )}
                onReject={() => runAction(
                  r.slab_id, () => setCalibrationStatus(r.slab_id, "rejected"),
                )}
                onReview={() => onReview(r)}
                onAddPhoto={(file) => runAction(
                  r.slab_id, () => replaceSlabImage(r.slab_id, file),
                )}
              />
            ))}
          </div>
        </div>
      ))}
    </PanelCard>
  );
}


function CalibrationRow({
  record, tone, busy, onApprove, onReject, onReview, onAddPhoto,
}: {
  record: CalibrationRecord;
  tone: StatusTone;
  busy: boolean;
  onApprove: () => void;
  onReject: () => void;
  onReview: () => void;
  onAddPhoto: (file: File) => void;
}) {
  const fileRef = useRef<HTMLInputElement>(null);
  const hasPhoto = !!record.calibrated_image_path || !!record.original_image_path;

  return (
    <div className="calibration-row">
      <div className="calibration-row-thumb">
        {hasPhoto ? (
          <img
            src={calibrationImageUrl(record.slab_id, record.approved_at)}
            alt={`Slab ${record.slab_id}`}
            loading="lazy"
          />
        ) : (
          <div className="calibration-row-thumb-empty">No photo</div>
        )}
      </div>

      <div className="calibration-row-info">
        <div className="calibration-row-id" title={record.slab_id}>
          {record.slab_id}
        </div>
        <div className="calibration-row-dims">
          <span>
            Excel {record.excel_width_mm.toFixed(0)}
            {" × "}{record.excel_height_mm.toFixed(0)} mm
          </span>
          <span>
            Usable {record.usable_width_mm.toFixed(0)}
            {" × "}{record.usable_height_mm.toFixed(0)} mm
          </span>
        </div>
        <div className="calibration-row-meta">
          {record.source_type !== "no_photo" && (
            <span className="calibration-row-source">
              {SOURCE_LABELS[record.source_type]}
            </span>
          )}
          {record.calibration_confidence != null && (
            <span className="calibration-row-confidence">
              {Math.round(record.calibration_confidence * 100)}% confidence
            </span>
          )}
        </div>
      </div>

      <div className="calibration-row-status">
        <StatusPill tone={tone}>{STATUS_LABELS[record.calibration_status]}</StatusPill>
      </div>

      <div className="calibration-row-actions">
        {record.calibration_status === "needs_review" && (
          <>
            <ActionBtn label="Review" disabled={busy} onClick={onReview} />
            <ActionBtn label="Approve" disabled={busy} onClick={onApprove} />
            <ActionBtn label="Reject" disabled={busy} onClick={onReject} />
          </>
        )}
        {record.calibration_status === "approved" && (
          <>
            <ActionBtn label="Review" disabled={busy} onClick={onReview} />
            <ActionBtn label="Reject" disabled={busy} onClick={onReject} />
          </>
        )}
        {record.calibration_status === "rejected" && (
          <>
            <ActionBtn label="Review" disabled={busy} onClick={onReview} />
            <ActionBtn label="Approve" disabled={busy} onClick={onApprove} />
          </>
        )}
        {record.calibration_status === "missing_photo" && (
          <>
            <input
              ref={fileRef}
              type="file"
              accept="image/*"
              className="step3-hidden-input"
              onChange={(e) => {
                const f = e.target.files?.[0];
                if (f) onAddPhoto(f);
                e.target.value = "";
              }}
            />
            <ActionBtn
              label="Add photo" disabled={busy}
              onClick={() => fileRef.current?.click()}
            />
          </>
        )}
      </div>
    </div>
  );
}


function ActionBtn({
  label, disabled, onClick,
}: { label: string; disabled: boolean; onClick: () => void }) {
  return (
    <button
      type="button"
      className="calibration-action-btn"
      disabled={disabled}
      onClick={onClick}
    >
      {label}
    </button>
  );
}
