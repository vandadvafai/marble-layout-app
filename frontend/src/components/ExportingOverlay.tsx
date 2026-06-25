// Blocking "Generating client image…" overlay for the Step-4 PNG
// export. Pure presentational — App owns the open/close state so
// it can keep the export-button busy state in sync.
//
// Why a full-screen overlay rather than inline busy text:
//   1. Repeated clicks on the export button are physically blocked
//      (the backdrop catches them) — matches the milestone brief.
//   2. The user gets an unmissable "wait" signal even if the SVG
//      serialisation takes a second or two on a complex layout.
//   3. Failure path closes the overlay and pushes the message to
//      the Step-4 status row, which keeps it visible after the
//      modal disappears.

import { memo } from "react";


interface Props {
  open: boolean;
  /** Free-text body. Localisation belongs to the caller. */
  message: string;
}


function ExportingOverlayImpl({ open, message }: Props) {
  if (!open) return null;
  return (
    <div
      className="exporting-overlay"
      role="alertdialog"
      aria-modal="true"
      aria-label={message}
    >
      <div className="exporting-overlay-card">
        <div className="exporting-overlay-spinner" aria-hidden="true" />
        <div className="exporting-overlay-msg">{message}</div>
      </div>
    </div>
  );
}


export default memo(ExportingOverlayImpl);
