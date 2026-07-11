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

import { cutDimsForPiece } from "./pieceGeom";
import type {
  Assignments, InventoryMatchResponse, Layout, Piece,
} from "./types";


const PNG_PADDING_PX = 48;
const PNG_TARGET_WIDTH_PX = 2400;
const PNG_TITLE_BAND_PX = 96;
/** Hard timeout for the per-slab decode step. Chosen well above
 *  the p99 backend response time (~2s for a big JPEG on a warm
 *  cache); a slab that takes longer is treated as a load failure
 *  so the export can't hang the whole UI. */
const IMAGE_LOAD_TIMEOUT_MS = 20_000;


/** ISO calendar date (YYYY-MM-DD) — the format the V1.1 filename
 *  spec uses. Consistent with what the backend produces. */
function isoToday(): string {
  const d = new Date();
  const pad = (n: number) => `${n}`.padStart(2, "0");
  return (
    `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
  );
}


/** Sanitize a project name for filenames. Mirrors the backend
 *  ``sanitize_filename_component`` — safe on Windows, macOS and
 *  Linux, no additional quoting required. */
export function sanitizeProjectName(
  raw: string | null | undefined, fallback = "project",
): string {
  if (!raw) return fallback;
  const safe = raw.replace(/[^a-zA-Z0-9\-_]+/g, "_");
  const trimmed = safe.replace(/^_+|_+$/g, "");
  return trimmed || fallback;
}


export function clientPngFilename(
  projectName: string | null | undefined,
): string {
  const project = sanitizeProjectName(projectName);
  return `${project}_ClientLayout_${isoToday()}.png`;
}


export function factoryOverviewFilename(
  projectName: string | null | undefined,
): string {
  const project = sanitizeProjectName(projectName);
  return `${project}_FactoryCutPlan_Overview_${isoToday()}.dxf`;
}


export function factoryPackageFilename(
  projectName: string | null | undefined,
): string {
  const project = sanitizeProjectName(projectName);
  return `${project}_FactoryPackage_${isoToday()}.zip`;
}


/** Fetch an image URL and convert it to a data: URL. Returns
 *  ``{ ok: false, error }`` on failure so the caller can refuse to
 *  export instead of silently shipping a PNG missing the slab.
 *
 *  The V1 export flow requires every slab image to be fully baked
 *  into the SVG as a data URL — otherwise the browser's SVG
 *  rasteriser fires ``onload`` before the ``<image>`` children
 *  have finished decoding, which is the source of the "blank
 *  slab" bug this module was audited to fix. */
export async function fetchImageAsDataUrl(
  href: string,
): Promise<
  { ok: true; dataUrl: string }
  | { ok: false; error: string }
> {
  try {
    const res = await fetch(href, { cache: "reload" });
    if (!res.ok) {
      return { ok: false, error: `HTTP ${res.status} ${res.statusText}` };
    }
    const blob = await res.blob();
    return await new Promise<
      { ok: true; dataUrl: string } | { ok: false; error: string }
    >((resolve) => {
      const reader = new FileReader();
      reader.onloadend = () => {
        const out = reader.result;
        if (typeof out !== "string" || !out.startsWith("data:")) {
          resolve({ ok: false, error: "FileReader did not return a data URL" });
        } else {
          resolve({ ok: true, dataUrl: out });
        }
      };
      reader.onerror = () => resolve({
        ok: false,
        error: reader.error?.message ?? "FileReader error",
      });
      reader.readAsDataURL(blob);
    });
  } catch (e) {
    return { ok: false, error: (e as Error).message };
  }
}


/** Snapshot of what a piece needs from the export pipeline: which
 *  slab was assigned, what URL to request, whether the pattern
 *  should be rotated when it lands in the SVG. Split into its own
 *  type so ``collectSlabImageEntries`` stays pure + testable. */
export interface SlabImageEntry {
  piece_id: string;
  slab_id: string;
  image_url: string;
  rotated: boolean;
}


/** Walk the finalisation snapshot and produce the export-pipeline
 *  work items. Only pieces with an assigned slab AND an on-disk
 *  image_path emit an entry — the piece polygon still renders
 *  otherwise, it just uses the neutral-tint fallback fill. */
export function collectSlabImageEntries(
  pieces: Piece[], assignments: Assignments,
  inventoryMatch: InventoryMatchResponse | null,
): SlabImageEntry[] {
  const out: SlabImageEntry[] = [];
  for (const p of pieces) {
    const slab_id = assignments[p.piece_id];
    if (!slab_id) continue;
    const candidate = assignedCandidate(inventoryMatch, p.piece_id, slab_id);
    if (!candidate?.image_path) continue;
    out.push({
      piece_id: p.piece_id,
      slab_id,
      // Use the safe-crop endpoint — that IS the full-resolution
      // processed image the on-screen canvas draws from, NOT a
      // thumbnail. It's what the operator has visually confirmed
      // during Step 4, so the PNG matches expectations.
      image_url: `/api/inventory/slab-image/${encodeURIComponent(slab_id)}?crop=safe-area`,
      rotated: !!candidate.rotation_needed,
    });
  }
  return out;
}


/** Race a promise against a timeout — used to make the image
 *  preloader time-bounded. Exposed for the unit tests. */
export function withTimeout<T>(
  p: Promise<T>, ms: number, msg: string,
): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error(msg)), ms);
    p.then(
      (v) => { clearTimeout(timer); resolve(v); },
      (e) => { clearTimeout(timer); reject(e); },
    );
  });
}


/** Inject-able decoder so the unit tests can simulate delayed /
 *  failing image loads without a real DOM. Production callers use
 *  ``defaultImageDecoder`` which wraps ``new Image()`` + ``decode()``. */
export type ImageDecoder = (url: string) => Promise<void>;


/** Load an image URL via ``new Image()`` and wait for ``decode()``
 *  to finish. Falls back to ``onload`` on the (rare) browsers
 *  without the ImageDecode API. */
export const defaultImageDecoder: ImageDecoder = (url) => new Promise(
  (resolve, reject) => {
    if (typeof Image === "undefined") {
      reject(new Error("Image() unavailable — non-browser environment?"));
      return;
    }
    const img = new Image();
    img.crossOrigin = "anonymous";
    img.onload = async () => {
      try {
        if (typeof (img as any).decode === "function") {
          await (img as any).decode();
        }
        resolve();
      } catch (e) {
        reject(e as Error);
      }
    };
    img.onerror = () => reject(new Error(`network error loading ${url}`));
    img.src = url;
  },
);


/** Preload + decode every URL. Returns the loaded set and a per-URL
 *  failure list; the caller decides whether to abort. Runs in
 *  parallel and uses ``withTimeout`` so a hung fetch can't stall
 *  the export forever. */
export async function preloadAndDecodeImages(
  urls: readonly string[],
  timeoutMs: number = IMAGE_LOAD_TIMEOUT_MS,
  decoder: ImageDecoder = defaultImageDecoder,
): Promise<{
  loaded: string[];
  failed: { url: string; error: string }[];
}> {
  const results = await Promise.all(urls.map(async (url) => {
    try {
      await withTimeout(
        decoder(url), timeoutMs,
        `timed out waiting for image ${url}`,
      );
      return { ok: true as const, url };
    } catch (e) {
      return {
        ok: false as const,
        url,
        error: (e as Error).message,
      };
    }
  }));
  const loaded: string[] = [];
  const failed: { url: string; error: string }[] = [];
  for (const r of results) {
    if (r.ok) loaded.push(r.url);
    else failed.push({ url: r.url, error: r.error });
  }
  return { loaded, failed };
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


/** Escape XML text content for embedding in the export SVG. */
function xmlEscape(s: string): string {
  return s.replace(/[&<>"']/g, (ch) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;",
    '"': "&quot;", "'": "&apos;",
  }[ch] ?? ch));
}


/** Compute the tight bounding box for the finalised pieces AND the
 *  target boundary. Used so the export frames the FULL floor even
 *  if the designer has panned/zoomed away in the live canvas. */
function computeFloorBbox(
  layout: Layout, pieces: Piece[],
): { x: number; y: number; w: number; h: number } {
  let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
  const acc = (x: number, y: number) => {
    if (x < x0) x0 = x; if (x > x1) x1 = x;
    if (y < y0) y0 = y; if (y > y1) y1 = y;
  };
  for (const [x, y] of layout.target.boundary) acc(x, y);
  for (const p of pieces) {
    for (const [x, y] of p.polygon) acc(x, y);
  }
  if (!Number.isFinite(x0)) return { x: 0, y: 0, w: 1, h: 1 };
  return { x: x0, y: y0, w: x1 - x0, h: y1 - y0 };
}


/** Look up the per-piece candidate that matches its assigned slab.
 *  Needed so the exporter knows whether to rotate the pattern. */
function assignedCandidate(
  match: InventoryMatchResponse | null,
  pieceId: string,
  slabId: string,
) {
  if (!match) return null;
  const pm = match.pieces.find((p) => p.piece_id === pieceId);
  if (!pm) return null;
  return pm.candidates.find((c) => c.slab_id === slabId) ?? null;
}


/** Build the export SVG string from scratch.
 *
 *  Key differences from serialising the live canvas:
 *    * Uses the TIGHT floor bbox so pan/zoom state is irrelevant.
 *    * Renders only the elements the client should see: target
 *      boundary, holes, piece polygons (filled with slab photos
 *      when present) and subtle interior joint lines.
 *    * Skips seams outside the boundary, guide lines, doorway
 *      chips, seam-drag handles, and every other editor affordance.
 *    * Adds a white margin around the drawing and a title band
 *      above it with the project name.
 */
function buildClientExportSvg(
  layout: Layout, pieces: Piece[],
  entries: readonly SlabImageEntry[],
  slabDataUrls: Readonly<Record<string, string>>,
  projectName: string,
): { svg: string; width: number; height: number } {
  const bbox = computeFloorBbox(layout, pieces);
  const aspect = bbox.w / bbox.h;
  const drawW = PNG_TARGET_WIDTH_PX;
  const drawH = Math.max(200, Math.round(drawW / aspect));
  const outerW = drawW + PNG_PADDING_PX * 2;
  const outerH = drawH + PNG_PADDING_PX * 2 + PNG_TITLE_BAND_PX;

  const boundaryPts = layout.target.boundary
    .map(([x, y]) => `${x},${y}`).join(" ");
  const holes = (layout.target.holes ?? []).map((h) =>
    h.map(([x, y]) => `${x},${y}`).join(" "),
  );

  // Every entry MUST have a baked-in data URL — the pipeline that
  // called us already verified this. Any missing slab is a
  // programmer error, not a runtime state we render around.
  const pieceById = new Map(pieces.map((p) => [p.piece_id, p]));
  const imageEntries = entries.map((e) => {
    const piece = pieceById.get(e.piece_id);
    const dataUrl = slabDataUrls[e.slab_id];
    if (!piece) {
      throw new Error(`piece ${e.piece_id} missing from finalisation`);
    }
    if (!dataUrl) {
      throw new Error(`slab ${e.slab_id} missing baked-in data URL`);
    }
    return { piece, slabId: e.slab_id, dataUrl, rotated: e.rotated };
  });

  // Build <defs> — one pattern per piece with a slab photo. Uses
  // the polygon-derived cut dims so a clipped strip fills its
  // actual area (not the working slab tile).
  const patterns = imageEntries.map(({ piece: p, dataUrl, rotated }) => {
    const cut = cutDimsForPiece(p);
    // Anchor the pattern at the polygon's real bbox origin.
    let ox = Infinity, oy = Infinity;
    for (const [x, y] of p.polygon) {
      if (x < ox) ox = x; if (y < oy) oy = y;
    }
    const w = cut.width_mm;
    const h = cut.height_mm;
    const cssId = `p_${p.piece_id.replace(/[^a-zA-Z0-9]/g, "_")}`;
    const pad = 0.005;
    const iw = w * (1 + pad * 2);
    const ih = h * (1 + pad * 2);
    if (rotated) {
      // Rotate 90° around the piece centre and swap image w/h so
      // the aspect ratio matches the rotated coverage.
      return `<pattern id="slab-pat-${cssId}" patternUnits="userSpaceOnUse" x="${ox}" y="${oy}" width="${w}" height="${h}">
        <g transform="translate(0 ${h}) scale(1 -1)">
          <g transform="rotate(90 ${w / 2} ${h / 2})">
            <image href="${dataUrl}" x="${(w - h) / 2 - h * pad}" y="${(h - w) / 2 - w * pad}" width="${h * (1 + pad * 2)}" height="${w * (1 + pad * 2)}" preserveAspectRatio="xMidYMid slice"/>
          </g>
        </g>
      </pattern>`;
    }
    return `<pattern id="slab-pat-${cssId}" patternUnits="userSpaceOnUse" x="${ox}" y="${oy}" width="${w}" height="${h}">
      <g transform="translate(0 ${h}) scale(1 -1)">
        <image href="${dataUrl}" x="${-w * pad}" y="${-h * pad}" width="${iw}" height="${ih}" preserveAspectRatio="xMidYMid slice"/>
      </g>
    </pattern>`;
  }).join("");

  // Piece polygons. Slab-fill takes priority; otherwise a light
  // neutral tint so unmapped pieces still read as marble.
  const patternIds = new Set(imageEntries.map(({ piece }) =>
    `p_${piece.piece_id.replace(/[^a-zA-Z0-9]/g, "_")}`));
  const piecePolys = pieces.map((p) => {
    const cssId = `p_${p.piece_id.replace(/[^a-zA-Z0-9]/g, "_")}`;
    const fill = patternIds.has(cssId)
      ? `url(#slab-pat-${cssId})` : "#f4f2ec";
    const pts = p.polygon.map(([x, y]) => `${x},${y}`).join(" ");
    // Subtle interior joint — thin stroke in a muted colour so
    // adjacent pieces read as one continuous floor instead of
    // shouting the grid. Non-scaling-stroke keeps the line thin
    // regardless of viewport size.
    return `<polygon points="${pts}" fill="${fill}" stroke="rgba(80,80,80,0.28)" stroke-width="1" vector-effect="non-scaling-stroke"/>`;
  }).join("");

  const holesXml = holes.map((h) =>
    `<polygon points="${h}" fill="#eaeaea" stroke="#8a8a8a" stroke-width="2" vector-effect="non-scaling-stroke"/>`,
  ).join("");

  const boundaryXml =
    `<polygon points="${boundaryPts}" fill="none" stroke="#202020" stroke-width="3" vector-effect="non-scaling-stroke"/>`;

  // Everything above is in mm. Wrap in a <g> that translates to
  // the export origin + scales to the target pixel dimensions.
  // The <g scale(1 -1)> matches the live canvas' y-flip so images
  // and polygons come out right-side up.
  const scale = drawW / bbox.w;
  const drawX = PNG_PADDING_PX;
  const drawY = PNG_PADDING_PX + PNG_TITLE_BAND_PX;

  // Title band contents.
  const title = xmlEscape(projectName);
  const subtitle = xmlEscape(
    `Client layout · ${isoToday()} · ${pieces.length} pieces`,
  );
  const titleY = PNG_PADDING_PX + 42;
  const subtitleY = PNG_PADDING_PX + 74;

  const svg =
    `<svg xmlns="http://www.w3.org/2000/svg" width="${outerW}" height="${outerH}" viewBox="0 0 ${outerW} ${outerH}">
      <rect x="0" y="0" width="${outerW}" height="${outerH}" fill="#ffffff"/>
      <text x="${PNG_PADDING_PX}" y="${titleY}" font-family="'Inter', -apple-system, 'Segoe UI', sans-serif" font-size="30" font-weight="700" fill="#111827">${title}</text>
      <text x="${PNG_PADDING_PX}" y="${subtitleY}" font-family="'Inter', -apple-system, 'Segoe UI', sans-serif" font-size="14" fill="#6b7280">${subtitle}</text>
      <defs>${patterns}</defs>
      <g transform="translate(${drawX} ${drawY + drawH}) scale(${scale} ${-scale}) translate(${-bbox.x} ${-bbox.y})">
        ${holesXml}
        ${piecePolys}
        ${boundaryXml}
      </g>
    </svg>`;

  return { svg, width: outerW, height: outerH };
}


/** Rasterise the export SVG and download it as a PNG. Renders the
 *  FULL floor bounding box with a white margin and title band —
 *  independent of what the designer has panned/zoomed on screen.
 *
 *  Reliability contract (V1.3):
 *
 *    1. Every assigned-slab image is preloaded AND decoded via
 *       ``img.decode()`` before we start building the SVG. If any
 *       fail (network, timeout, decode error), we bail with a
 *       clear message listing the affected slab ids — the PNG is
 *       NEVER exported from a partially-decoded state.
 *    2. Each image URL is then fetched a second time and baked
 *       into the SVG as a ``data:`` URL. This must succeed for
 *       every entry; a failure here also aborts.
 *    3. The final SVG image element is also awaited via
 *       ``img.decode()`` so the ``ctx.drawImage`` sees a fully
 *       ready bitmap.
 *
 *  ``callbacks`` is optional; the client-image action bar wires
 *  ``onProgress`` up so the "Preparing client image…" overlay can
 *  show which stage the export is in. */
export async function exportClientPng(
  demoId: string,
  layout: Layout,
  pieces: Piece[],
  assignments: Assignments,
  inventoryMatch: InventoryMatchResponse | null,
  projectName: string | null | undefined,
  callbacks?: {
    onProgress?: (msg: string) => void;
    decoder?: ImageDecoder;
  },
): Promise<
  { ok: true; filename: string }
  | { ok: false; error: string; failedSlabs?: string[] }
> {
  void demoId; // filename now comes from project name; kept for API stability
  const onProgress = callbacks?.onProgress ?? (() => {});
  const decoder = callbacks?.decoder ?? defaultImageDecoder;

  if (!pieces.length) {
    return { ok: false, error: "No pieces to export." };
  }
  const displayProject = projectName || demoId;

  // -----------------------------------------------------------------
  // Stage 1 — enumerate every slab image the export needs.
  // -----------------------------------------------------------------
  const entries = collectSlabImageEntries(pieces, assignments, inventoryMatch);

  // -----------------------------------------------------------------
  // Stage 2 — preload + decode every URL. Blocking hard because a
  // partially-decoded image is the root cause of the "blank slab"
  // bug this rewrite is fixing.
  // -----------------------------------------------------------------
  onProgress(`Preloading ${entries.length} slab image${entries.length === 1 ? "" : "s"}…`);
  const preload = await preloadAndDecodeImages(
    entries.map((e) => e.image_url), IMAGE_LOAD_TIMEOUT_MS, decoder,
  );
  if (preload.failed.length > 0) {
    const failedSlabs = preload.failed.map((f) => {
      const entry = entries.find((e) => e.image_url === f.url);
      return entry?.slab_id ?? f.url;
    });
    return {
      ok: false,
      error: (
        `Failed to load ${failedSlabs.length} slab photo`
        + `${failedSlabs.length === 1 ? "" : "s"}: `
        + `${failedSlabs.slice(0, 3).join(", ")}`
        + (failedSlabs.length > 3 ? "…" : "")
        + ". Re-upload the missing photo(s) before exporting."
      ),
      failedSlabs,
    };
  }

  // -----------------------------------------------------------------
  // Stage 3 — bake every image into the SVG as a data URL. A
  // failure here is treated the same as a load failure so the
  // resulting PNG can never render an <image> against a network
  // URL that hasn't finished coming in.
  // -----------------------------------------------------------------
  onProgress("Embedding slab images…");
  const dataUrlPairs = await Promise.all(entries.map(async (e) => ({
    slab_id: e.slab_id,
    image_url: e.image_url,
    result: await fetchImageAsDataUrl(e.image_url),
  })));
  const embedFailures = dataUrlPairs.filter((r) => !r.result.ok);
  if (embedFailures.length > 0) {
    const failedSlabs = embedFailures.map((r) => r.slab_id);
    return {
      ok: false,
      error: (
        `Failed to embed ${failedSlabs.length} slab photo`
        + `${failedSlabs.length === 1 ? "" : "s"}: `
        + `${failedSlabs.slice(0, 3).join(", ")}`
        + (failedSlabs.length > 3 ? "…" : "")
      ),
      failedSlabs,
    };
  }
  const slabDataUrls: Record<string, string> = {};
  for (const pair of dataUrlPairs) {
    if (pair.result.ok) slabDataUrls[pair.slab_id] = pair.result.dataUrl;
  }

  // -----------------------------------------------------------------
  // Stage 4 — build the SVG string with data URLs baked in and
  // rasterise via a hidden canvas.
  // -----------------------------------------------------------------
  onProgress("Preparing client image…");
  const built = buildClientExportSvg(
    layout, pieces, entries, slabDataUrls, displayProject,
  );
  const svgBlob = new Blob(
    ['<?xml version="1.0" encoding="UTF-8"?>\n', built.svg],
    { type: "image/svg+xml" },
  );
  const svgUrl = URL.createObjectURL(svgBlob);

  try {
    const img = await new Promise<HTMLImageElement>((resolve, reject) => {
      const i = new Image();
      i.onload = async () => {
        try {
          if (typeof (i as any).decode === "function") {
            await (i as any).decode();
          }
          resolve(i);
        } catch (e) { reject(e as Error); }
      };
      i.onerror = () => reject(new Error("SVG rasterisation failed"));
      i.src = svgUrl;
    });

    const canvas = document.createElement("canvas");
    canvas.width = built.width;
    canvas.height = built.height;
    const ctx = canvas.getContext("2d");
    if (!ctx) return { ok: false, error: "Canvas 2d context missing." };
    ctx.fillStyle = "#ffffff";
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    ctx.drawImage(img, 0, 0, built.width, built.height);

    const pngBlob = await new Promise<Blob | null>((resolve) =>
      canvas.toBlob((b) => resolve(b), "image/png"),
    );
    if (!pngBlob) return { ok: false, error: "PNG encoding failed." };

    const filename = clientPngFilename(projectName);
    downloadBlob(pngBlob, filename);
    return { ok: true, filename };
  } catch (e) {
    return { ok: false, error: (e as Error).message };
  } finally {
    URL.revokeObjectURL(svgUrl);
  }
}


/** Manufacturing tolerances the factory writer + fit checker honour.
 *  Defaults mirror ``placement_engine.api.factory_layout.MarginPolicy``
 *  so a caller that doesn't override anything gets the same numbers
 *  the backend would have used. All values are in millimetres. */
export type ManufacturingProfile = "strict" | "standard" | "exact";
export type ExactEdgeAction = "allow" | "warn" | "block";

export interface ManufacturingPolicy {
  blade_kerf_mm: number;
  edge_trim_mm: number;
  tolerance_mm: number;
  /** Which fit gate to run — see the backend ``MarginPolicy`` for
   *  the semantics. Defaults to ``"standard"``. */
  profile: ManufacturingProfile;
  /** How to score an exact-edge fit (piece flush with the slab
   *  boundary). ``"warn"`` (the default) still lets the export
   *  proceed with a ``verdict = "exact_edge"`` flag. */
  exact_edge_action: ExactEdgeAction;
  /** Millimetre band the checker treats as "exact" — 0.5 mm keeps
   *  a 1610 mm slab / 1610 mm cut on the exact-edge branch even
   *  after rounding. */
  exact_edge_epsilon_mm: number;
}

/** V1 default policy. Slab dimensions imported into Layout Helper
 *  are already preprocessed by the factory (safe-crop inside the
 *  green boundary), so the usable cutting area IS the slab. The
 *  ``exact`` profile skips the kerf / trim / tolerance checks and
 *  ``allow`` treats a flush fit as ``ready`` — the effect is that
 *  the only fit failure the default emits is "piece bigger than
 *  the slab" (``does_not_fit``), which is a real geometric
 *  problem the designer must resolve.
 *
 *  When the designer opens Advanced Factory Settings, ``App.tsx``
 *  swaps this out for the user-edited policy. */
export const DEFAULT_MANUFACTURING_POLICY: ManufacturingPolicy = {
  blade_kerf_mm: 3.0,
  edge_trim_mm: 5.0,
  tolerance_mm: 2.0,
  profile: "exact",
  exact_edge_action: "allow",
  exact_edge_epsilon_mm: 0.5,
};

/** Starting values the Advanced Factory Settings card ships with
 *  when the designer first opens it — a conservative kerf +
 *  tolerance check with no auto-trim. Keeps the default V1 flow
 *  quiet while giving operators a sensible baseline the moment
 *  they opt in. */
export const ADVANCED_DEFAULT_MANUFACTURING_POLICY: ManufacturingPolicy = {
  blade_kerf_mm: 3.0,
  edge_trim_mm: 5.0,
  tolerance_mm: 2.0,
  profile: "standard",
  exact_edge_action: "warn",
  exact_edge_epsilon_mm: 0.5,
};

/** Per-piece verdict from the preflight fit endpoint. Mirrors the
 *  ``FactoryFitResult`` dataclass on the backend one field for one
 *  field. */
export interface FactoryFitResult {
  piece_id: string;
  slab_id: string;
  verdict: "ready" | "tight" | "insufficient_margin"
    | "does_not_fit" | "unknown_slab" | "exact_edge";
  factory_ready: boolean;
  reason: string;
  piece_width_mm: number;
  piece_height_mm: number;
  slab_width_mm: number;
  slab_height_mm: number;
  rotation_needed: boolean;
  usable_width_mm: number;
  usable_height_mm: number;
  margin_width_mm: number;
  margin_height_mm: number;
  geometric_margin_width_mm: number;
  geometric_margin_height_mm: number;
  manufacturing_margin_width_mm: number;
  manufacturing_margin_height_mm: number;
  profile: ManufacturingProfile;
}

export interface FactoryFitResponse {
  policy: ManufacturingPolicy;
  results: FactoryFitResult[];
  factory_ready: boolean;
  unassigned_count?: number;
}

/** Run the backend's preflight fit check and return per-piece
 *  verdicts. Called by the Step-4 export bar before enabling the
 *  Export DXF button — the same code path the export endpoint uses
 *  internally, so a passing preflight guarantees the download will
 *  succeed. */
export async function validateFactoryFit(
  demoId: string,
  pieces: Piece[],
  assignments: Assignments,
  policy: ManufacturingPolicy = DEFAULT_MANUFACTURING_POLICY,
): Promise<
  { ok: true; response: FactoryFitResponse }
  | { ok: false; error: string }
> {
  const body = {
    pieces: pieces.map((p) => {
      const cut = cutDimsForPiece(p);
      return {
        piece_id: p.piece_id,
        polygon: p.polygon,
        nominal_width_mm: cut.width_mm,
        nominal_height_mm: cut.height_mm,
      };
    }),
    assignments,
    manufacturing_policy: policy,
  };
  let res: Response;
  try {
    res = await fetch(
      `/api/demo-layouts/${encodeURIComponent(demoId)}/validate-factory-fit`,
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
    const detail = await res.text().catch(() => "");
    return {
      ok: false,
      error: `Fit check failed (${res.status}): ${detail || res.statusText}`,
    };
  }
  const response = (await res.json()) as FactoryFitResponse;
  return { ok: true, response };
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
  policy: ManufacturingPolicy = DEFAULT_MANUFACTURING_POLICY,
  projectName: string | null | undefined = undefined,
): Promise<
  { ok: true; filename: string }
  | { ok: false; error: string; failing?: FactoryFitResult[] }
> {
  // Send polygon-derived REAL cut dimensions in the request's
  // ``nominal_width_mm`` / ``nominal_height_mm`` fields. The field
  // names predate this clarification — semantically these are
  // "the size the cutter must produce" (== polygon bbox), not the
  // working-slab tile size. Without this fix, an edge-clipped strip
  // would land on the factory DXF labelled with the full tile size.
  const body = {
    pieces: pieces.map((p) => {
      const cut = cutDimsForPiece(p);
      return {
        piece_id: p.piece_id,
        polygon: p.polygon,
        nominal_width_mm: cut.width_mm,
        nominal_height_mm: cut.height_mm,
      };
    }),
    assignments,
    doorways,
    seams,
    manufacturing_policy: policy,
    project_name: projectName ?? null,
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
    let detailMsg = "";
    let failing: FactoryFitResult[] | undefined;
    try {
      const j = await res.json();
      const d = j?.detail;
      if (d && typeof d === "object" && d.error === "manufacturing_fit_failed") {
        detailMsg = d.message ?? "Manufacturing fit check failed.";
        failing = d.failing as FactoryFitResult[];
      } else if (typeof d === "string") {
        detailMsg = d;
      } else {
        detailMsg = JSON.stringify(d);
      }
    } catch {
      detailMsg = await res.text().catch(() => "");
    }
    return {
      ok: false,
      error: `DXF export failed (${res.status}): ${detailMsg || res.statusText}`,
      failing,
    };
  }
  const blob = await res.blob();
  const cd = res.headers.get("content-disposition") ?? "";
  const m = /filename="([^"]+)"/.exec(cd);
  const filename = m?.[1] ?? factoryOverviewFilename(projectName);
  downloadBlob(blob, filename);
  return { ok: true, filename };
}


/** Bundle the overview DXF + every per-slab DXF into a single
 *  ZIP the backend produces. Same fit gate as ``exportFactoryDxf``
 *  — a failing fit returns ``ok: false`` with the ``failing`` list
 *  so the UI can surface it. */
export async function exportFactoryPackage(
  demoId: string,
  pieces: Piece[],
  assignments: Assignments,
  policy: ManufacturingPolicy = DEFAULT_MANUFACTURING_POLICY,
  projectName: string | null | undefined = undefined,
): Promise<
  { ok: true; filename: string }
  | { ok: false; error: string; failing?: FactoryFitResult[] }
> {
  const body = {
    pieces: pieces.map((p) => {
      const cut = cutDimsForPiece(p);
      return {
        piece_id: p.piece_id,
        polygon: p.polygon,
        nominal_width_mm: cut.width_mm,
        nominal_height_mm: cut.height_mm,
      };
    }),
    assignments,
    manufacturing_policy: policy,
    project_name: projectName ?? null,
  };
  let res: Response;
  try {
    res = await fetch(
      `/api/demo-layouts/${encodeURIComponent(demoId)}/export-factory-package`,
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
    let detailMsg = "";
    let failing: FactoryFitResult[] | undefined;
    try {
      const j = await res.json();
      const d = j?.detail;
      if (d && typeof d === "object" && d.error === "manufacturing_fit_failed") {
        detailMsg = d.message ?? "Manufacturing fit check failed.";
        failing = d.failing as FactoryFitResult[];
      } else if (typeof d === "string") {
        detailMsg = d;
      } else {
        detailMsg = JSON.stringify(d);
      }
    } catch {
      detailMsg = await res.text().catch(() => "");
    }
    return {
      ok: false,
      error: `Factory package failed (${res.status}): ${detailMsg || res.statusText}`,
      failing,
    };
  }
  const blob = await res.blob();
  const cd = res.headers.get("content-disposition") ?? "";
  const m = /filename="([^"]+)"/.exec(cd);
  const filename = m?.[1] ?? factoryPackageFilename(projectName);
  downloadBlob(blob, filename);
  return { ok: true, filename };
}
