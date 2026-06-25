// Thin fetch wrapper. The Vite dev server proxies /api/* to the
// FastAPI process on :8000 (see vite.config.ts), so we use relative
// URLs in both dev and prod — no hard-coded localhost references
// leaking into the bundle.

import type {
  DemoIndexResponse,
  DemoLayoutResponse,
  InventoryCurrentResponse,
  InventoryInfo,
  InventoryMatchResponse,
  InventoryUploadResponse,
  Piece,
  Plan,
  RegenerateLayoutResponse,
  ValidationResult,
} from "./types";

const BASE = "/api";

async function fetchJson<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`, { method: "GET" });
  if (!res.ok) {
    const detail = await res.text().catch(() => "");
    throw new Error(
      `GET ${path} failed: ${res.status} ${res.statusText}` +
        (detail ? ` — ${detail}` : ""),
    );
  }
  return (await res.json()) as T;
}

async function postJson<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const detail = await res.text().catch(() => "");
    throw new Error(
      `POST ${path} failed: ${res.status} ${res.statusText}` +
        (detail ? ` — ${detail}` : ""),
    );
  }
  return (await res.json()) as T;
}

export function listDemos(): Promise<DemoIndexResponse> {
  return fetchJson<DemoIndexResponse>("/demo-layouts");
}

export function fetchDemoLayout(demoId: string): Promise<DemoLayoutResponse> {
  return fetchJson<DemoLayoutResponse>(
    `/demo-layouts/${encodeURIComponent(demoId)}`,
  );
}

/**
 * Validate the editor's current piece set against the demo's
 * architectural plan. Returns the backend's serialised
 * RuleReport — see ``serializers.serialize_rule_report_for_editor``.
 *
 * The backend only needs the pieces; the target geometry and the
 * plan are pinned by ``demoId``. We strip the canvas-only fields
 * (``is_full_tile``, ``intersects_hole``) before sending so the
 * request body stays close to the wire schema.
 */
export function postValidateLayout(
  demoId: string, pieces: Piece[], plan?: Plan,
): Promise<ValidationResult> {
  const body: Record<string, unknown> = {
    pieces: pieces.map((p) => ({
      piece_id: p.piece_id,
      zone_id: p.zone_id,
      nominal_x_mm: p.nominal_x_mm,
      nominal_y_mm: p.nominal_y_mm,
      nominal_width_mm: p.nominal_width_mm,
      nominal_height_mm: p.nominal_height_mm,
      polygon: p.polygon,
      notes: p.notes,
    })),
  };
  if (plan) {
    // Strip empty-string fields the backend would treat as
    // overrides — keep the request body close to the wire schema.
    body.plan = {
      target_id: plan.target_id,
      spaces: plan.spaces,
      doorways: plan.doorways,
      columns: plan.columns,
      guide_lines: plan.guide_lines,
    };
  }
  return postJson<ValidationResult>(
    `/demo-layouts/${encodeURIComponent(demoId)}/validate`,
    body,
  );
}

/**
 * Ask the backend which inventory slabs can cover each piece in the
 * editor's current state. Read-only — no slab is reserved, the same
 * slab may match multiple pieces. The future assignment layer will
 * tighten this into one-slab-per-piece with global optimisation.
 *
 * The request only sends the nominal dimensions; the matcher
 * doesn't consume polygons (geometric clipping doesn't change what
 * size slab the piece needs).
 */
/**
 * Fetch the resolved inventory source (path, source label, valid /
 * skipped counts). The panel calls this on boot so designers can see
 * which inventory is active BEFORE running a match.
 */
export function fetchInventoryInfo(): Promise<InventoryInfo> {
  return fetchJson<InventoryInfo>("/inventory/info");
}

/**
 * Upload an Excel + (optional) slab photos. The server runs the
 * existing slab-intake pipeline (Excel parse → photo suffix match →
 * clean_slabs.json) and returns the same summary the Step-3 panel
 * renders. After this resolves, /api/inventory/info reports
 * ``source_label === "uploaded"`` and the matcher uses the uploaded
 * inventory transparently.
 */
export async function uploadInventory(
  excelFile: File, imageFiles: File[],
): Promise<InventoryUploadResponse> {
  const form = new FormData();
  form.append("excel", excelFile, excelFile.name);
  for (const f of imageFiles) {
    form.append("images", f, f.name);
  }
  const res = await fetch(`${BASE}/inventory/upload`, {
    method: "POST",
    body: form,
  });
  if (!res.ok) {
    const detail = await res.text().catch(() => "");
    throw new Error(
      `Upload failed: ${res.status} ${res.statusText}` +
      (detail ? ` — ${detail}` : ""),
    );
  }
  return (await res.json()) as InventoryUploadResponse;
}

/** Read the active uploaded inventory (if any). Used on boot so the
 *  Step-3 panel can restore its state after a refresh while the
 *  server is still up. */
export function fetchCurrentInventory(): Promise<InventoryCurrentResponse> {
  return fetchJson<InventoryCurrentResponse>("/inventory/current");
}

/**
 * Re-tile the demo's geometry with caller-supplied tile dimensions
 * (typically the uploaded inventory's median width × height). When
 * both ``tile_width_mm`` and ``tile_height_mm`` are omitted, the
 * backend uses inventory-median sizing — equivalent to the GET
 * /api/demo-layouts/{id} flow but reported with ``tile_choice``.
 */
export function regenerateLayout(
  demoId: string,
  tile?: { tile_width_mm: number; tile_height_mm: number },
): Promise<RegenerateLayoutResponse> {
  return postJson<RegenerateLayoutResponse>(
    `/demo-layouts/${encodeURIComponent(demoId)}/regenerate`,
    tile ?? {},
  );
}

/** Drop the active uploaded inventory and return to the fallback. */
export async function clearUploadedInventory(): Promise<void> {
  const res = await fetch(`${BASE}/inventory/current`, { method: "DELETE" });
  if (!res.ok) {
    throw new Error(`Clear failed: ${res.status} ${res.statusText}`);
  }
}

export function postMatchInventory(
  demoId: string,
  pieces: Piece[],
  allowRotation = true,
  /** Override the matcher's per-piece candidate cap. Defaults to
   *  the matcher's own default (3) when omitted — fine for the
   *  Step-2 preview. Step 4's auto-assignment needs a much higher
   *  value (e.g. inventory_count or 200) so the response actually
   *  exposes every slab in the inventory, not just the same three
   *  lowest-waste slabs that every tile-uniform piece sees. */
  topK?: number,
): Promise<InventoryMatchResponse> {
  const body: Record<string, unknown> = {
    pieces: pieces.map((p) => ({
      piece_id: p.piece_id,
      nominal_width_mm: p.nominal_width_mm,
      nominal_height_mm: p.nominal_height_mm,
    })),
    allow_rotation: allowRotation,
  };
  if (typeof topK === "number" && topK > 0) {
    body.top_k = topK;
  }
  return postJson<InventoryMatchResponse>(
    `/demo-layouts/${encodeURIComponent(demoId)}/match-inventory`,
    body,
  );
}
