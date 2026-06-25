// Step-4 export helpers.
//
// PNG: serialise the active LayoutCanvas SVG, inline every external
// <image href> as a data URL (otherwise the browser taints the
// canvas and toDataURL throws), then rasterise via a hidden
// <canvas>. The result is a client-facing PNG with the slab images
// already clipped into the piece polygons — same as what's on
// screen, minus UI chrome.
//
// DXF: POSTs the editor's pieces + assignments to the backend
// endpoint and triggers a download from the response. The backend
// owns DXF generation (uses ezdxf) so this side stays simple.

import { preloadSlabImages } from "./imageReadiness";
import type { Assignments, Piece } from "./types";


const PNG_PADDING_PX = 24;
const PNG_TARGET_WIDTH_PX = 2048;


/** Format a UTC timestamp for export filenames. ``YYYYMMDD-HHMMSS``
 *  — short, sortable, no characters that break filesystems. */
function timestamp(): string {
  const d = new Date();
  const pad = (n: number) => `${n}`.padStart(2, "0");
  return (
    `${d.getFullYear()}${pad(d.getMonth() + 1)}${pad(d.getDate())}-`
    + `${pad(d.getHours())}${pad(d.getMinutes())}${pad(d.getSeconds())}`
  );
}


/** Fetch an image URL and convert it to a data: URL. The PNG export
 *  needs this because `<image href="/api/inventory/slab-image/…">`
 *  refs would otherwise either fail to render synchronously OR
 *  taint the canvas, depending on browser. Returns the original
 *  href on failure so the export still produces a (slightly less
 *  complete) PNG. */
async function inlineImageHrefAsDataUrl(href: string): Promise<string> {
  try {
    const res = await fetch(href);
    if (!res.ok) return href;
    const blob = await res.blob();
    return await new Promise<string>((resolve, reject) => {
      const reader = new FileReader();
      reader.onloadend = () => resolve(String(reader.result ?? href));
      reader.onerror = () => reject(reader.error);
      reader.readAsDataURL(blob);
    });
  } catch {
    return href;
  }
}


/** Trigger a browser download for arbitrary bytes. */
function downloadBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  // Revoke after a tick so Safari has time to start the download.
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}


/** Find the active LayoutCanvas SVG. Single canvas per app right
 *  now, so a class query is enough; if we ever have multiple we
 *  switch to a ref. */
function findCanvasSvg(): SVGSVGElement | null {
  return document.querySelector(".canvas-svg") as SVGSVGElement | null;
}


/** Serialise the LayoutCanvas SVG to a PNG and download it.
 *
 *  Steps:
 *   1. Clone the SVG out of the DOM so we can mutate without
 *      breaking the live editor.
 *   2. For every ``<image href="/api/inventory/slab-image/…">``
 *      inside the clone, fetch the bytes and replace the href with
 *      a ``data:`` URL. SVG patterns work either way; the data-URL
 *      form is what prevents canvas taint.
 *   3. Set explicit width / height on the clone (the live SVG uses
 *      flex sizing so the serialised version is dimensionless).
 *   4. Wrap in a Blob URL, load via Image(), draw to a canvas at
 *      a fixed target width, then toBlob('image/png') → download.
 */
export async function exportClientPng(
  demoId: string,
): Promise<{ ok: true; filename: string } | { ok: false; error: string }> {
  const svgEl = findCanvasSvg();
  if (!svgEl) return { ok: false, error: "Canvas not found." };

  // Use the live viewBox so we crop tightly to the floor layout
  // (the live element fills its container with white margin we
  // don't want in the export).
  const viewBox = svgEl.getAttribute("viewBox");
  if (!viewBox) return { ok: false, error: "Canvas viewBox missing." };
  const [vbX, vbY, vbW, vbH] = viewBox.split(/\s+/).map(Number);
  if ([vbX, vbY, vbW, vbH].some((n) => !Number.isFinite(n))) {
    return { ok: false, error: "Canvas viewBox unreadable." };
  }

  // 0.1.52 — belt-and-braces: even though the Step-4 export button
  // is gated on ``imageReadiness.isReady``, the user could (in
  // theory) click Export the instant after assignment in a race
  // we don't fully cover with React state. Preload every image
  // referenced by the live SVG one more time before serialising;
  // this is a HTTP-cache hit when the readiness probe already ran,
  // so the cost is essentially zero.
  {
    const liveImages = Array.from(svgEl.querySelectorAll("image"));
    const liveUrls = liveImages
      .map((n) => n.getAttribute("href")
        ?? n.getAttributeNS("http://www.w3.org/1999/xlink", "href")
        ?? "")
      .filter((u) => u && !u.startsWith("data:"));
    if (liveUrls.length > 0) {
      await preloadSlabImages(liveUrls);
    }
  }

  // Clone + inline images.
  const clone = svgEl.cloneNode(true) as SVGSVGElement;
  const images = Array.from(clone.querySelectorAll("image"));
  await Promise.all(
    images.map(async (img) => {
      const href = img.getAttribute("href") ?? img.getAttributeNS(
        "http://www.w3.org/1999/xlink", "href",
      );
      if (!href || href.startsWith("data:")) return;
      const dataUrl = await inlineImageHrefAsDataUrl(href);
      img.setAttribute("href", dataUrl);
    }),
  );

  // Explicit width/height — SVG must have absolute dimensions for
  // <img src> rasterisation to work.
  const aspect = vbW / vbH;
  const targetW = PNG_TARGET_WIDTH_PX;
  const targetH = Math.round(targetW / aspect);
  clone.setAttribute("width", String(targetW));
  clone.setAttribute("height", String(targetH));
  // Drop hover/selection styles by removing inline cursor classes;
  // class-based styling won't apply since the SVG is rendered
  // standalone with no stylesheet attached.
  clone.removeAttribute("class");

  const xml = new XMLSerializer().serializeToString(clone);
  const svgBlob = new Blob(
    ['<?xml version="1.0" encoding="UTF-8"?>\n', xml],
    { type: "image/svg+xml" },
  );
  const svgUrl = URL.createObjectURL(svgBlob);

  try {
    const img = await new Promise<HTMLImageElement>((resolve, reject) => {
      const i = new Image();
      i.onload = () => resolve(i);
      i.onerror = () => reject(new Error("svg rasterisation failed"));
      i.src = svgUrl;
    });

    const canvas = document.createElement("canvas");
    canvas.width = targetW + PNG_PADDING_PX * 2;
    canvas.height = targetH + PNG_PADDING_PX * 2;
    const ctx = canvas.getContext("2d");
    if (!ctx) return { ok: false, error: "Canvas 2d context missing." };
    ctx.fillStyle = "#ffffff";
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    ctx.drawImage(img, PNG_PADDING_PX, PNG_PADDING_PX, targetW, targetH);

    const pngBlob = await new Promise<Blob | null>((resolve) =>
      canvas.toBlob((b) => resolve(b), "image/png"),
    );
    if (!pngBlob) return { ok: false, error: "PNG encoding failed." };

    const filename = `client_layout_${demoId}_${timestamp()}.png`;
    downloadBlob(pngBlob, filename);
    return { ok: true, filename };
  } catch (e) {
    return { ok: false, error: (e as Error).message };
  } finally {
    URL.revokeObjectURL(svgUrl);
  }
}


/** POST the current finalisation + assignments to the backend's
 *  ``export-dxf`` endpoint and download the response. The backend
 *  owns DXF generation (ezdxf) and returns a Content-Disposition
 *  filename we honour client-side. */
export async function exportFactoryDxf(
  demoId: string,
  pieces: Piece[],
  assignments: Assignments,
  doorways: Array<[[number, number], [number, number]]> = [],
  seams: Array<[[number, number], [number, number]]> = [],
): Promise<{ ok: true; filename: string } | { ok: false; error: string }> {
  const body = {
    pieces: pieces.map((p) => ({
      piece_id: p.piece_id,
      polygon: p.polygon,
      nominal_width_mm: p.nominal_width_mm,
      nominal_height_mm: p.nominal_height_mm,
    })),
    assignments,
    doorways,
    seams,
  };
  let res: Response;
  try {
    res = await fetch(
      `/api/demo-layouts/${encodeURIComponent(demoId)}/export-dxf`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      },
    );
  } catch (e) {
    return { ok: false, error: (e as Error).message };
  }
  if (!res.ok) {
    let detail = "";
    try {
      const j = await res.json();
      detail = j?.detail ?? "";
    } catch {
      detail = await res.text().catch(() => "");
    }
    return {
      ok: false,
      error: `DXF export failed (${res.status}): ${detail || res.statusText}`,
    };
  }
  const blob = await res.blob();
  const cd = res.headers.get("content-disposition") ?? "";
  // Extract filename from Content-Disposition; fall back to a
  // generated name if the header is missing or malformed.
  const m = /filename="([^"]+)"/.exec(cd);
  const filename = m?.[1] ?? `factory_cut_plan_${demoId}_${timestamp()}.dxf`;
  downloadBlob(blob, filename);
  return { ok: true, filename };
}
