// Regression tests for the client PNG export pipeline.
//
// The historical bug this covers: the export used to fire before
// every assigned slab image had finished loading + decoding, so
// intermittently a piece would ship as blank. The V1.3 rewrite
// gates the rasterise step behind a preload + decode of every URL
// and refuses to run when any URL fails. These tests exercise the
// gate directly (via injected mocks) so a future refactor that
// re-introduces the race is caught by CI, not the field.

import { describe, expect, test } from "vitest";

import {
  collectSlabImageEntries,
  DEFAULT_MANUFACTURING_POLICY,
  exportClientPng,
  preloadAndDecodeImages,
  withTimeout,
} from "./exportLayout";
import type { Assignments, Layout, Piece } from "./types";

void DEFAULT_MANUFACTURING_POLICY;


// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

const SQUARE_LAYOUT: Layout = {
  target: {
    target_id: "t1",
    name: "test",
    bbox: [0, 0, 2000, 2000],
    boundary: [[0, 0], [2000, 0], [2000, 2000], [0, 2000]],
    holes: [],
  },
  grid: {
    tile_width_mm: 1000,
    tile_height_mm: 1000,
    origin: [0, 0],
    anchor_mode: null,
  },
  pieces: [],
  piece_count: 2,
};

function piece(id: string, x: number, y: number): Piece {
  return {
    piece_id: id,
    zone_id: "z0",
    nominal_x_mm: x,
    nominal_y_mm: y,
    nominal_width_mm: 1000,
    nominal_height_mm: 1000,
    polygon: [
      [x, y], [x + 1000, y],
      [x + 1000, y + 1000], [x, y + 1000], [x, y],
    ],
    is_full_tile: true,
    is_edge_piece: false,
    intersects_hole: false,
    notes: [],
  };
}

const PIECES: Piece[] = [piece("p1", 0, 0), piece("p2", 1000, 0)];
const ASSIGNMENTS: Assignments = { p1: "slab-a", p2: "slab-b" };

const MATCH = {
  demo_id: "demo",
  inventory: {
    source_label: "test",
    source_description: "test",
    source_path: "test",
    valid_count: 2,
    skipped_count: 0,
    total_records: 2,
    stats: null,
  },
  inventory_path: "test",
  inventory_count: 2,
  allow_rotation: true,
  pieces: [
    {
      piece_id: "p1",
      required_width_mm: 1000, required_height_mm: 1000,
      required_area_m2: 1.0,
      status: "matched" as const,
      candidates: [{
        slab_id: "slab-a",
        serial_number: null,
        width_mm: 1000, height_mm: 1000,
        cut_width_mm: 1000, cut_height_mm: 1000,
        waste_mm2: 0, waste_fraction: 0,
        rotation_needed: false,
        image_path: "/fake/slab-a.jpg",
      }],
    },
    {
      piece_id: "p2",
      required_width_mm: 1000, required_height_mm: 1000,
      required_area_m2: 1.0,
      status: "matched" as const,
      candidates: [{
        slab_id: "slab-b",
        serial_number: null,
        width_mm: 1000, height_mm: 1000,
        cut_width_mm: 1000, cut_height_mm: 1000,
        waste_mm2: 0, waste_fraction: 0,
        rotation_needed: false,
        image_path: "/fake/slab-b.jpg",
      }],
    },
  ],
  summary: {
    exact_fit: 0, multiple_options: 0, matched: 2, no_match: 0,
    total_pieces: 2,
  },
};


// ---------------------------------------------------------------------------
// collectSlabImageEntries — pure enumeration.
// ---------------------------------------------------------------------------

describe("collectSlabImageEntries", () => {
  test("emits one entry per assigned piece with an image", () => {
    const entries = collectSlabImageEntries(PIECES, ASSIGNMENTS, MATCH as any);
    expect(entries).toHaveLength(2);
    expect(entries[0]).toMatchObject({ piece_id: "p1", slab_id: "slab-a" });
    expect(entries[0].image_url).toContain("slab-a");
    expect(entries[0].image_url).toContain("crop=safe-area");
  });

  test("skips pieces without an assignment", () => {
    const entries = collectSlabImageEntries(
      PIECES, { p1: "slab-a", p2: null } as Assignments, MATCH as any,
    );
    expect(entries).toHaveLength(1);
    expect(entries[0].piece_id).toBe("p1");
  });

  test("skips assignments whose slab has no image_path", () => {
    const matchNoImages = {
      ...MATCH,
      pieces: MATCH.pieces.map((pm) => ({
        ...pm,
        candidates: pm.candidates.map((c) => ({ ...c, image_path: null })),
      })),
    };
    const entries = collectSlabImageEntries(
      PIECES, ASSIGNMENTS, matchNoImages as any,
    );
    expect(entries).toHaveLength(0);
  });
});


// ---------------------------------------------------------------------------
// preloadAndDecodeImages — the gate the export pipeline sits behind.
// ---------------------------------------------------------------------------

describe("preloadAndDecodeImages", () => {
  test("resolves once every URL is decoded", async () => {
    const decoder = (url: string) => new Promise<void>((resolve) => {
      // Simulate a small but non-zero decode delay so the test
      // catches "fire before decode" regressions.
      setTimeout(() => resolve(), 20);
      void url;
    });
    const res = await preloadAndDecodeImages(["/a.jpg", "/b.jpg"], 1000, decoder);
    expect(res.failed).toEqual([]);
    expect(res.loaded).toEqual(["/a.jpg", "/b.jpg"]);
  });

  test("reports the failing URLs when a load throws", async () => {
    const decoder = (url: string) =>
      url.endsWith("bad.jpg")
        ? Promise.reject(new Error("404"))
        : Promise.resolve();
    const res = await preloadAndDecodeImages(
      ["/ok.jpg", "/bad.jpg"], 1000, decoder,
    );
    expect(res.loaded).toEqual(["/ok.jpg"]);
    expect(res.failed).toHaveLength(1);
    expect(res.failed[0]).toMatchObject({
      url: "/bad.jpg",
      error: "404",
    });
  });

  test("times out a slow decode instead of hanging", async () => {
    const decoder = () => new Promise<void>(() => { /* never resolves */ });
    const res = await preloadAndDecodeImages(["/slow.jpg"], 25, decoder);
    expect(res.loaded).toEqual([]);
    expect(res.failed).toHaveLength(1);
    expect(res.failed[0].error).toMatch(/timed out/i);
  });
});


// ---------------------------------------------------------------------------
// withTimeout — small but critical helper the preloader leans on.
// ---------------------------------------------------------------------------

describe("withTimeout", () => {
  test("resolves when the inner promise resolves first", async () => {
    const p = new Promise<number>((r) => setTimeout(() => r(42), 10));
    await expect(withTimeout(p, 100, "should not fire")).resolves.toBe(42);
  });

  test("rejects with the timeout message when the inner is slow", async () => {
    const p = new Promise<number>((r) => setTimeout(() => r(1), 100));
    await expect(withTimeout(p, 20, "boom")).rejects.toThrow("boom");
  });
});


// ---------------------------------------------------------------------------
// exportClientPng — end-to-end gate.
// ---------------------------------------------------------------------------

describe("exportClientPng", () => {
  test(
    "refuses to render when a slab image fails to preload",
    async () => {
      // Even though the SVG rasteriser + toBlob will never be
      // reached in this flow, spy the injected decoder to prove
      // the export bails BEFORE it hits any browser API. If a
      // future regression removes the gate, the test would either
      // pass with a truthy ``ok`` or crash inside jsdom's Image
      // implementation — both are loud failures.
      const decoder = (url: string) =>
        url.includes("slab-b")
          ? Promise.reject(new Error("network error"))
          : Promise.resolve();

      const res = await exportClientPng(
        "demo", SQUARE_LAYOUT, PIECES, ASSIGNMENTS, MATCH as any, "Project",
        { decoder },
      );
      expect(res.ok).toBe(false);
      if (res.ok) throw new Error("unreachable");
      expect(res.failedSlabs).toContain("slab-b");
      expect(res.error).toMatch(/slab-b/);
    },
  );

  test(
    "surfaces every failing slab, not just the first",
    async () => {
      const decoder = () => Promise.reject(new Error("everything's down"));
      const res = await exportClientPng(
        "demo", SQUARE_LAYOUT, PIECES, ASSIGNMENTS, MATCH as any, "Project",
        { decoder },
      );
      expect(res.ok).toBe(false);
      if (res.ok) throw new Error("unreachable");
      expect(res.failedSlabs).toEqual(
        expect.arrayContaining(["slab-a", "slab-b"]),
      );
    },
  );

  test(
    "still waits for the slower slab before continuing",
    async () => {
      // Delayed-load simulation — the point of the whole rewrite.
      // If the export races ahead before the slower decoder
      // finishes, it will hit stage 3 (data-url embed) which
      // uses ``fetch`` — and jsdom's fetch has no ``/fake/*``
      // mount, so it throws. In that case the assertion below
      // would flip to ``embed`` failures, or the test would time
      // out. As-is, the load-decode gate rejects the export at
      // stage 2 (with a load-failure verdict) which is what the
      // test asserts.
      const decoder = (url: string) => new Promise<void>((resolve, reject) => {
        if (url.includes("slab-b")) {
          setTimeout(() => reject(new Error("slow-fail")), 40);
        } else {
          setTimeout(() => resolve(), 5);
        }
      });
      const res = await exportClientPng(
        "demo", SQUARE_LAYOUT, PIECES, ASSIGNMENTS, MATCH as any, "Project",
        { decoder },
      );
      expect(res.ok).toBe(false);
      if (res.ok) throw new Error("unreachable");
      expect(res.failedSlabs).toEqual(["slab-b"]);
    },
  );

  test("refuses to run with no pieces", async () => {
    const res = await exportClientPng(
      "demo", SQUARE_LAYOUT, [], {}, null, "Project",
      { decoder: () => Promise.resolve() },
    );
    expect(res.ok).toBe(false);
    if (res.ok) throw new Error("unreachable");
    expect(res.error).toMatch(/no pieces/i);
  });

  test("progress callback fires with the current stage", async () => {
    const decoder = () => Promise.reject(new Error("stop early"));
    const progress: string[] = [];
    await exportClientPng(
      "demo", SQUARE_LAYOUT, PIECES, ASSIGNMENTS, MATCH as any, "Project",
      { decoder, onProgress: (m) => progress.push(m) },
    );
    // The very first stage is "Preloading N slab images…" — proves
    // the UI has a message to display before decode work starts.
    expect(progress[0]).toMatch(/preloading/i);
  });
});
