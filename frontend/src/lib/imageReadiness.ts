// Slab-image readiness tracking for the Step-4 PNG export.
//
// The PNG export pipeline serialises the live SVG and inlines every
// <image href> as a data URL before rasterising — that part already
// works. The MISSING piece (0.1.52) is the readiness gate: before
// the export milestone we'd let the user click Export the instant a
// slab was assigned, even though the canvas was still loading the
// slab photos. The result was a PNG with blank or half-painted
// pieces.
//
// This module fixes that by tracking which assigned-slab photos
// have actually finished loading in the browser, exposing the count
// for the Step-4 helper text, and letting the Export button stay
// disabled until everything is settled.
//
// Implementation note: we preload off-canvas via ``new Image()``
// rather than peeking at the live SVG's image elements. That keeps
// the readiness state decoupled from any canvas rendering details
// — the same hook would work if a future renderer used a different
// SVG layout — and the browser image cache means the eventual
// ``<image href>`` in the canvas hits a warm cache.

import { useEffect, useRef, useState } from "react";

import type {
  Assignments, InventoryMatchResponse, Piece,
} from "./types";


export interface AssignedImageRef {
  piece_id: string;
  slab_id: string;
  /** Fully-qualified URL the canvas WILL render. Identical string
   *  as what ``LayoutCanvas`` uses for its SVG <image> hrefs so the
   *  browser cache + the readiness probe stay in sync. */
  url: string;
}


/** Resolve every assigned piece that has an on-disk slab photo
 *  into a {piece_id, slab_id, url} ref. Pieces with no assignment
 *  OR with an assigned slab that has no image_path are excluded
 *  (the canvas wouldn't render an image for those anyway). */
export function getAssignedImageRefs(
  pieces: Piece[],
  assignments: Assignments,
  inventoryMatch: InventoryMatchResponse | null,
): AssignedImageRef[] {
  if (!inventoryMatch) return [];
  const out: AssignedImageRef[] = [];
  for (const p of pieces) {
    const slab_id = assignments[p.piece_id];
    if (!slab_id) continue;
    const pm = inventoryMatch.pieces.find(
      (x) => x.piece_id === p.piece_id,
    );
    const cand = pm?.candidates.find((c) => c.slab_id === slab_id);
    if (!cand?.image_path) continue;
    out.push({
      piece_id: p.piece_id,
      slab_id,
      url:
        `/api/inventory/slab-image/${encodeURIComponent(slab_id)}`
        + `?crop=safe-area`,
    });
  }
  return out;
}


export interface ReadinessState {
  total: number;
  loaded: number;
  failed: number;
  pending: number;
  /** True only when every expected image has settled (loaded or
   *  failed). The export button is gated on this. */
  isReady: boolean;
  /** True iff every image successfully loaded — no failures. */
  allLoaded: boolean;
}


/** Resolve a promise once every URL has either loaded or failed.
 *  Useful as a standalone helper (e.g. from an export pipeline);
 *  the hook below uses the same probe but exposes incremental
 *  state. Same-URL probes share the browser's image cache so this
 *  is cheap to re-run. */
export async function preloadSlabImages(
  urls: readonly string[],
): Promise<{ loaded: string[]; failed: string[] }> {
  const loaded: string[] = [];
  const failed: string[] = [];
  await Promise.all(urls.map((url) => new Promise<void>((resolve) => {
    const img = new Image();
    img.onload = () => { loaded.push(url); resolve(); };
    img.onerror = () => { failed.push(url); resolve(); };
    img.src = url;
  })));
  return { loaded, failed };
}


/** React hook: probe each ref's URL via off-canvas ``new Image()``
 *  and report incremental counts. Re-runs when the set of URLs
 *  changes (we key the effect on a joined string so identity
 *  doesn't matter — the caller can rebuild the ref array on every
 *  render). Repeated probes for the same URL are cheap thanks to
 *  the browser's HTTP cache. */
export function useSlabImagesReady(
  refs: readonly AssignedImageRef[],
): ReadinessState {
  // Persistent map across renders so already-loaded URLs don't
  // flicker back to "pending" when the refs array is rebuilt.
  const statusRef = useRef<Map<string, "loaded" | "failed" | "pending">>(
    new Map(),
  );
  const [tick, setTick] = useState(0);  // forces a re-render after each load
  const urlsKey = refs.map((r) => r.url).join("|");

  useEffect(() => {
    const urls = refs.map((r) => r.url);
    const status = statusRef.current;
    // GC URLs no longer referenced — keeps the map bounded across
    // many assignment changes.
    const active = new Set(urls);
    for (const k of Array.from(status.keys())) {
      if (!active.has(k)) status.delete(k);
    }
    // Start probes for any URL we haven't seen yet.
    let started = false;
    for (const url of urls) {
      if (status.has(url)) continue;
      status.set(url, "pending");
      started = true;
      const img = new Image();
      img.onload = () => {
        status.set(url, "loaded");
        setTick((t) => t + 1);
      };
      img.onerror = () => {
        status.set(url, "failed");
        setTick((t) => t + 1);
      };
      img.src = url;
    }
    if (started) setTick((t) => t + 1);
    // No teardown — the Image objects are GC'd when their handlers
    // are no longer reachable; we don't carry references.
  }, [urlsKey, refs]);

  const status = statusRef.current;
  let loaded = 0, failed = 0, pending = 0;
  for (const r of refs) {
    const s = status.get(r.url);
    if (s === "loaded") loaded += 1;
    else if (s === "failed") failed += 1;
    else pending += 1;
  }
  const total = refs.length;
  // ``tick`` is read so React's exhaustive-deps lint stays happy AND
  // so the count actually re-renders when handlers update statusRef.
  void tick;
  return {
    total,
    loaded,
    failed,
    pending,
    isReady: total === 0 || pending === 0,
    allLoaded: total > 0 && loaded === total,
  };
}
