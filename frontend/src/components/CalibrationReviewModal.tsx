// Manual-review modal for a single calibration record (M4).
//
// Left pane: the original photo with a draggable 4-corner overlay
// (SVG, viewBox = the image's natural pixel dimensions — the exact
// frame ``detected_corners``/``confirmed_corners`` are stored in, per
// ``placement_engine/calibration/models.py``). Right pane: the
// perspective-corrected preview the backend produces from those
// corners.
//
// Corner math lives ENTIRELY on the backend (OpenCV, in
// ``placement_engine/calibration/corners.py``). This modal only
// tracks where the operator dragged each handle to, in the image's
// own pixel space, and hands that off — it never re-implements the
// warp. Dragging a corner updates the overlay instantly (cheap,
// client-only); releasing the pointer submits the new corners, which
// the backend re-warps AND approves (adjusting corners is the
// operator vouching for them — see ``apply_manual_corners``).

import { useEffect, useRef, useState } from "react";

import {
  calibrationImageUrl, calibrationOriginalImageUrl, replaceSlabImage,
  setCalibrationStatus, submitManualCorners,
} from "../lib/api";
import type { CalibrationRecord, Point } from "../lib/types";

const WARNING_EXPLANATIONS: Record<string, string> = {
  aspect_ratio_mismatch:
    "The photo's proportions don't match the Excel width/height "
    + "closely enough to trust automatically.",
  aspect_ratio_review:
    "The photo's proportions are close to the Excel dimensions but "
    + "not close enough to auto-approve.",
  low_confidence:
    "The boundary/corner detector wasn't confident about where the "
    + "slab edges are.",
  low_rectangularity:
    "The detected outline isn't rectangular enough — this can happen "
    + "with an irregular or broken slab edge.",
  corner_detection_failed:
    "No slab outline could be detected in this photo — place the "
    + "corners manually.",
  image_unreadable:
    "The photo file couldn't be opened — try replacing it.",
  missing_excel_dimensions:
    "The Excel row is missing a usable width/height.",
  usable_dimensions_non_positive:
    "This slab is too small to survive the 20 mm/side edge "
    + "deduction.",
};

type Rotation = 0 | 90 | 180 | 270;

const CORNER_LABELS = ["top-left", "top-right", "bottom-right", "bottom-left"];

interface Props {
  record: CalibrationRecord;
  onClose: () => void;
  onRecordChange: (record: CalibrationRecord) => void;
}

export default function CalibrationReviewModal({
  record, onClose, onRecordChange,
}: Props) {
  const [naturalSize, setNaturalSize] =
    useState<{ w: number; h: number } | null>(null);
  const [corners, setCorners] = useState<Point[]>(
    record.confirmed_corners ?? record.detected_corners ?? [],
  );
  const [rotation, setRotation] = useState<Rotation>(0);
  const [zoom, setZoom] = useState(1);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Bumped on every successful mutation so the <img> tags bust the
  // browser's HTTP cache — both the original and calibrated image
  // files are overwritten IN PLACE on disk (same filename every
  // time), so the URL alone doesn't change when the bytes do.
  const [photoVersion, setPhotoVersion] = useState(0);

  const svgRef = useRef<SVGSVGElement>(null);
  const dragIndexRef = useRef<number | null>(null);
  const replaceFileRef = useRef<HTMLInputElement>(null);

  // Reset local editor state whenever a different slab opens.
  useEffect(() => {
    setCorners(record.confirmed_corners ?? record.detected_corners ?? []);
    setRotation(0);
    setZoom(1);
    setError(null);
    setNaturalSize(null);
  }, [record.slab_id]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  const submitCorners = async (next: Point[]) => {
    setBusy(true);
    setError(null);
    try {
      const { record: updated } = await submitManualCorners(
        record.slab_id, next,
      );
      onRecordChange(updated);
      setPhotoVersion((v) => v + 1);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const onCornerPointerDown = (
    i: number,
  ) => (e: React.PointerEvent<SVGCircleElement>) => {
    e.stopPropagation();
    e.currentTarget.setPointerCapture(e.pointerId);
    dragIndexRef.current = i;
  };

  const onCornerPointerMove = (e: React.PointerEvent<SVGCircleElement>) => {
    const i = dragIndexRef.current;
    const svg = svgRef.current;
    if (i === null || !svg) return;
    const p = svgPoint(svg, e.clientX, e.clientY);
    setCorners((prev) => {
      const next = [...prev];
      next[i] = p;
      return next;
    });
  };

  const onCornerPointerUp = (e: React.PointerEvent<SVGCircleElement>) => {
    const i = dragIndexRef.current;
    dragIndexRef.current = null;
    e.currentTarget.releasePointerCapture(e.pointerId);
    if (i === null) return;
    void submitCorners(corners);
  };

  const onResetCorners = () => {
    const reset = record.detected_corners ?? [];
    setCorners(reset);
    if (reset.length === 4) void submitCorners(reset);
  };

  const onApprove = async () => {
    setBusy(true);
    setError(null);
    try {
      const { record: updated } = await setCalibrationStatus(
        record.slab_id, "approved",
      );
      onRecordChange(updated);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const onReject = async () => {
    setBusy(true);
    setError(null);
    try {
      const { record: updated } = await setCalibrationStatus(
        record.slab_id, "rejected",
      );
      onRecordChange(updated);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const onReplaceImage = async (file: File) => {
    setBusy(true);
    setError(null);
    try {
      const { record: updated } = await replaceSlabImage(
        record.slab_id, file,
      );
      onRecordChange(updated);
      setCorners(updated.confirmed_corners ?? updated.detected_corners ?? []);
      setRotation(0);
      setZoom(1);
      setNaturalSize(null);
      setPhotoVersion((v) => v + 1);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const hasOriginal = !!record.original_image_path;
  const handleRadius = naturalSize
    ? Math.max(8, naturalSize.w / 80)
    : 12;

  return (
    <div className="calibration-modal-backdrop" onMouseDown={onClose}>
      <div
        className="calibration-modal"
        onMouseDown={(e) => e.stopPropagation()}
      >
        <header className="calibration-modal-head">
          <h3 className="calibration-modal-title">
            Review slab {record.slab_id}
          </h3>
          <button
            type="button"
            className="calibration-modal-close"
            onClick={onClose}
            aria-label="Close"
          >
            ×
          </button>
        </header>

        <div className="calibration-modal-body">
          <div className="calibration-modal-panes">
            <div className="calibration-modal-pane">
              <div className="calibration-modal-pane-label">Original</div>
              <div className="calibration-modal-image-scroll">
                {hasOriginal ? (
                  <div
                    className="calibration-modal-image-wrap"
                    style={{
                      transform: `rotate(${rotation}deg) scale(${zoom})`,
                    }}
                  >
                    <img
                      src={calibrationOriginalImageUrl(record.slab_id)
                        + `?v=${photoVersion}`}
                      alt={`Original photo for slab ${record.slab_id}`}
                      onLoad={(e) => {
                        const img = e.currentTarget;
                        setNaturalSize({
                          w: img.naturalWidth, h: img.naturalHeight,
                        });
                      }}
                    />
                    {naturalSize && corners.length === 4 && (
                      <svg
                        ref={svgRef}
                        className="calibration-corner-overlay"
                        viewBox={`0 0 ${naturalSize.w} ${naturalSize.h}`}
                      >
                        <polygon
                          points={corners.map((p) => p.join(",")).join(" ")}
                          className="calibration-corner-quad"
                        />
                        {corners.map((p, i) => (
                          <circle
                            key={CORNER_LABELS[i]}
                            cx={p[0]}
                            cy={p[1]}
                            r={handleRadius}
                            className="calibration-corner-handle"
                            onPointerDown={onCornerPointerDown(i)}
                            onPointerMove={onCornerPointerMove}
                            onPointerUp={onCornerPointerUp}
                          >
                            <title>{CORNER_LABELS[i]}</title>
                          </circle>
                        ))}
                      </svg>
                    )}
                  </div>
                ) : (
                  <div className="calibration-modal-no-image">
                    No photo uploaded for this slab.
                  </div>
                )}
              </div>
              <div className="calibration-modal-controls">
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => setRotation(
                    (r) => ((r + 90) % 360) as Rotation,
                  )}
                >
                  Rotate 90°
                </button>
                <button
                  type="button"
                  disabled={busy || !record.detected_corners}
                  onClick={onResetCorners}
                >
                  Reset corners
                </button>
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => setZoom((z) => Math.min(z * 1.25, 4))}
                >
                  Zoom in
                </button>
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => setZoom((z) => Math.max(z / 1.25, 1))}
                >
                  Zoom out
                </button>
              </div>
            </div>

            <div className="calibration-modal-pane">
              <div className="calibration-modal-pane-label">
                Calibrated preview
              </div>
              <div className="calibration-modal-image-scroll">
                {record.calibrated_image_path ? (
                  <img
                    className="calibration-modal-preview-img"
                    src={calibrationImageUrl(
                      record.slab_id,
                      `${record.approved_at ?? "unapproved"}-${photoVersion}`,
                    )}
                    alt={`Calibrated preview for slab ${record.slab_id}`}
                  />
                ) : (
                  <div className="calibration-modal-no-image">
                    No calibrated preview yet — adjust the corners to
                    generate one.
                  </div>
                )}
              </div>
            </div>
          </div>

          {record.warnings.length > 0 && (
            <div className="calibration-modal-warnings">
              <strong>Why this slab needs review:</strong>
              <ul>
                {record.warnings.map((w) => (
                  <li key={w}>{WARNING_EXPLANATIONS[w] ?? w}</li>
                ))}
              </ul>
            </div>
          )}

          {error && (
            <div className="calibration-error" role="alert">{error}</div>
          )}
        </div>

        <footer className="calibration-modal-foot">
          <input
            ref={replaceFileRef}
            type="file"
            accept="image/*"
            className="step3-hidden-input"
            onChange={(e) => {
              const f = e.target.files?.[0];
              if (f) void onReplaceImage(f);
              e.target.value = "";
            }}
          />
          <button
            type="button"
            disabled={busy}
            onClick={() => replaceFileRef.current?.click()}
          >
            Replace image
          </button>
          <div className="calibration-modal-foot-spacer" />
          <button type="button" disabled={busy} onClick={onReject}>
            Reject
          </button>
          <button
            type="button"
            className="calibration-modal-approve"
            disabled={busy}
            onClick={onApprove}
          >
            Approve
          </button>
        </footer>
      </div>
    </div>
  );
}

/** Screen point → the SVG's own viewBox coordinates, accounting for
 *  any CSS transform on the element or its ancestors (rotation,
 *  zoom) via ``getScreenCTM()`` — the same technique
 *  ``LayoutCanvas.tsx`` uses, so rotate/zoom need no separate
 *  coordinate math of their own. */
function svgPoint(svg: SVGSVGElement, clientX: number, clientY: number): Point {
  const ctm = svg.getScreenCTM();
  if (!ctm) return [0, 0];
  const pt = svg.createSVGPoint();
  pt.x = clientX;
  pt.y = clientY;
  const local = pt.matrixTransform(ctm.inverse());
  return [local.x, local.y];
}
