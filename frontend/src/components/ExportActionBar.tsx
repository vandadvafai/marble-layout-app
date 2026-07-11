// Fixed bottom-right export action bar (V1.1). Replaces the PNG /
// DXF buttons that used to live inside the sidebar Step4ExportBar.
//
// Two large primary CTAs:
//   * "Export Client Image" — rasterises the finalised layout into
//     a PNG the designer can send to the client.
//   * "Export Factory Package" — hits the backend ZIP endpoint and
//     downloads the overview + per-slab DXFs bundled together.
//
// A collapsible "blockers" chip surfaces every reason the export is
// currently disabled (unassigned pieces, duplicates, too-small
// slabs, failing manufacturing fit, missing slab photos, etc.)
// so the designer never wonders "why won't this download?".

import { memo, useState } from "react";


export interface ExportBlocker {
  id: string;
  label: string;
  detail?: string;
  severity: "critical" | "warn";
}


interface Props {
  /** True when the sidebar deems the plan safe to hand off. Both
   *  export buttons are disabled unless this is true. */
  canExport: boolean;
  /** Concrete list of the reasons ``canExport`` is false. Rendered
   *  as a collapsible checklist so the designer sees exactly what
   *  to fix. Empty when there's nothing blocking. */
  blockers: ExportBlocker[];
  /** True when the assigned slab images are still loading — gates
   *  the client PNG (which needs the photos) but not the DXF. */
  imagesReady: boolean;
  onExportClientImage: () => Promise<{ ok: boolean; error?: string }>;
  onExportFactoryPackage: () => Promise<{ ok: boolean; error?: string }>;
}


function ExportActionBarImpl({
  canExport, blockers, imagesReady,
  onExportClientImage, onExportFactoryPackage,
}: Props) {
  const [pngBusy, setPngBusy] = useState(false);
  const [zipBusy, setZipBusy] = useState(false);
  const [message, setMessage] = useState<
    { kind: "ok" | "err"; text: string } | null
  >(null);
  const [openBlockers, setOpenBlockers] = useState(false);

  const pngReady = canExport && imagesReady;
  const zipReady = canExport;

  const runExport = async (
    fn: () => Promise<{ ok: boolean; error?: string }>,
    labelOnSuccess: string,
    setBusy: (b: boolean) => void,
  ) => {
    setBusy(true);
    setMessage(null);
    try {
      const res = await fn();
      if (res.ok) {
        setMessage({ kind: "ok", text: labelOnSuccess });
      } else {
        setMessage({
          kind: "err",
          text: res.error ?? "Export failed.",
        });
      }
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="export-action-bar" role="region" aria-label="Exports">
      {blockers.length > 0 && (
        <div className="export-action-blockers">
          <button
            type="button"
            className="export-action-blockers-summary"
            onClick={() => setOpenBlockers((v) => !v)}
            aria-expanded={openBlockers}
          >
            <span className="export-action-blockers-dot" />
            <span className="export-action-blockers-count">
              {blockers.length} blocker
              {blockers.length === 1 ? "" : "s"}
            </span>
            <span
              className="export-action-blockers-caret"
              aria-hidden="true"
            >
              {openBlockers ? "▾" : "▸"}
            </span>
          </button>
          {openBlockers && (
            <ul className="export-action-blockers-list">
              {blockers.map((b) => (
                <li
                  key={b.id}
                  className={
                    "export-action-blocker "
                    + `export-action-blocker-${b.severity}`
                  }
                >
                  <span className="export-action-blocker-label">
                    {b.label}
                  </span>
                  {b.detail && (
                    <span className="export-action-blocker-detail">
                      {b.detail}
                    </span>
                  )}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      {message && (
        <div
          className={
            "export-action-msg "
            + (message.kind === "ok"
              ? "export-action-msg-ok"
              : "export-action-msg-err")
          }
        >
          {message.text}
        </div>
      )}

      <div className="export-action-buttons">
        <button
          type="button"
          className="export-action-btn export-action-btn-secondary"
          disabled={!pngReady || pngBusy || zipBusy}
          onClick={() => runExport(
            onExportClientImage,
            "Client image downloaded.",
            setPngBusy,
          )}
          title={
            !canExport
              ? "Resolve export blockers first"
              : !imagesReady
                ? "Waiting for slab images to load"
                : "Render the client-facing floor image"
          }
        >
          <ExportGlyph kind="image" />
          <span>
            {pngBusy ? "Rendering…" : "Export Client Image"}
          </span>
        </button>
        <button
          type="button"
          className="export-action-btn export-action-btn-primary"
          disabled={!zipReady || pngBusy || zipBusy}
          onClick={() => runExport(
            onExportFactoryPackage,
            "Factory package downloaded.",
            setZipBusy,
          )}
          title={
            canExport
              ? "Download overview DXF + one DXF per slab (ZIP)"
              : "Resolve export blockers first"
          }
        >
          <ExportGlyph kind="package" />
          <span>
            {zipBusy ? "Bundling…" : "Export Factory Package"}
          </span>
        </button>
      </div>
    </div>
  );
}


const ExportActionBar = memo(ExportActionBarImpl);
export default ExportActionBar;


function ExportGlyph({ kind }: { kind: "image" | "package" }) {
  if (kind === "image") {
    return (
      <svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true">
        <rect
          x="2" y="3" width="12" height="10" rx="1.6"
          fill="none" stroke="currentColor" strokeWidth="1.4"
        />
        <circle cx="6" cy="7" r="1.4" fill="currentColor" />
        <path
          d="M2.6 13 L6.5 8.5 L9.5 11 L13.4 7.5 L13.4 13 Z"
          fill="currentColor"
        />
      </svg>
    );
  }
  return (
    <svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true">
      <path
        d="M8 2 L14 5 L8 8 L2 5 Z"
        fill="none" stroke="currentColor" strokeWidth="1.4"
        strokeLinejoin="round"
      />
      <path
        d="M2 5 L2 11 L8 14 L14 11 L14 5"
        fill="none" stroke="currentColor" strokeWidth="1.4"
        strokeLinejoin="round"
      />
      <line
        x1="8" y1="8" x2="8" y2="14"
        stroke="currentColor" strokeWidth="1.4"
      />
    </svg>
  );
}
