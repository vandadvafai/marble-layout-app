// Regression tests for the M4 calibration-gating rules — Step 4 must
// block on any needs_review / missing_photo record and unblock once
// every slab is resolved (approved or rejected).

import { describe, expect, test } from "vitest";

import {
  calibrationCounts, inventorySummaryDisplay, isInventoryReady,
} from "./workflow";
import type {
  CalibrationRecord, CalibrationRecordsResponse, InventoryInfo,
} from "./types";

function record(
  slab_id: string, status: CalibrationRecord["calibration_status"],
): CalibrationRecord {
  return {
    slab_id,
    source_type: "green_boundary",
    excel_width_mm: 1600,
    excel_height_mm: 1600,
    usable_width_mm: 1560,
    usable_height_mm: 1560,
    calibration_status: status,
    factory_policy_version: "1.0",
    original_image_path: "/tmp/o.jpg",
    calibrated_image_path: "/tmp/c.jpg",
    detected_corners: null,
    confirmed_corners: null,
    crop_coordinates: null,
    calibration_confidence: 0.9,
    aspect_delta: 0.01,
    approved_at: null,
    approved_by: null,
    warnings: [],
    notes: null,
  };
}

function calibrationResponse(
  records: CalibrationRecord[], active = true,
): CalibrationRecordsResponse {
  return { active, records, counts: calibrationCounts(records) };
}

const EMPTY_INVENTORY_INFO: InventoryInfo = {
  source_label: "empty",
  source_description: "",
  source_path: null,
  valid_count: 0,
  skipped_count: 0,
  total_records: 0,
  stats: null,
};

describe("calibrationCounts", () => {
  test("tallies each status independently", () => {
    const counts = calibrationCounts([
      record("a", "approved"),
      record("b", "approved"),
      record("c", "needs_review"),
      record("d", "missing_photo"),
      record("e", "rejected"),
    ]);
    expect(counts).toEqual({
      approved: 2, needs_review: 1, missing_photo: 1, rejected: 1,
    });
  });

  test("empty list yields all-zero counts", () => {
    expect(calibrationCounts([])).toEqual({
      approved: 0, needs_review: 0, missing_photo: 0, rejected: 0,
    });
  });
});

describe("isInventoryReady", () => {
  test("blocks when any slab needs review", () => {
    const calibration = calibrationResponse([
      record("a", "approved"), record("b", "needs_review"),
    ]);
    expect(isInventoryReady(calibration, null)).toBe(false);
  });

  test("blocks when any slab is missing a photo", () => {
    const calibration = calibrationResponse([
      record("a", "approved"), record("b", "missing_photo"),
    ]);
    expect(isInventoryReady(calibration, null)).toBe(false);
  });

  test("blocks when every slab was rejected (nothing to assign)", () => {
    const calibration = calibrationResponse([
      record("a", "rejected"), record("b", "rejected"),
    ]);
    expect(isInventoryReady(calibration, null)).toBe(false);
  });

  test("ready once every slab is approved or rejected, with >=1 approved", () => {
    const calibration = calibrationResponse([
      record("a", "approved"), record("b", "rejected"),
    ]);
    expect(isInventoryReady(calibration, null)).toBe(true);
  });

  test("falls back to valid_count for a non-calibration (demo) source", () => {
    const inactive = calibrationResponse([], false);
    expect(isInventoryReady(inactive, EMPTY_INVENTORY_INFO)).toBe(false);
    expect(isInventoryReady(inactive, { ...EMPTY_INVENTORY_INFO, valid_count: 3 }))
      .toBe(true);
  });

  test("no calibration fetched yet falls back to inventoryInfo", () => {
    expect(isInventoryReady(null, { ...EMPTY_INVENTORY_INFO, valid_count: 1 }))
      .toBe(true);
    expect(isInventoryReady(null, null)).toBe(false);
  });
});

describe("inventorySummaryDisplay", () => {
  const uploadSnapshot = {
    valid_slabs: 0, invalid_slabs: 1, slabs_without_photos: ["STALE-1"],
  };

  test("reflects live calibration counts once a slab is approved after upload", () => {
    // Regression: the Inventory Summary card used to keep showing the
    // `/upload` response's snapshot forever, so approving the only
    // slab in the M4 manual pass left "0 valid / 1 invalid" on screen
    // while the Calibration panel and Step 4 both said "ready".
    const calibration = calibrationResponse([record("a", "approved")]);
    const display = inventorySummaryDisplay(calibration, uploadSnapshot);
    expect(display).toEqual({
      validSlabs: 1, invalidSlabs: 0, slabsWithoutPhotos: [],
    });
  });

  test("invalidSlabs sums rejected + needs_review + missing_photo", () => {
    const calibration = calibrationResponse([
      record("a", "rejected"),
      record("b", "needs_review"),
      record("c", "missing_photo"),
      record("d", "approved"),
    ]);
    const display = inventorySummaryDisplay(calibration, uploadSnapshot);
    expect(display.validSlabs).toBe(1);
    expect(display.invalidSlabs).toBe(3);
  });

  test("slabsWithoutPhotos comes from live records, not the stale list", () => {
    const calibration = calibrationResponse([
      record("a", "approved"), record("b", "missing_photo"),
    ]);
    const display = inventorySummaryDisplay(calibration, uploadSnapshot);
    expect(display.slabsWithoutPhotos).toEqual(["b"]);
  });

  test("falls back to the upload snapshot for a non-calibration source", () => {
    const inactive = calibrationResponse([], false);
    expect(inventorySummaryDisplay(inactive, uploadSnapshot)).toEqual({
      validSlabs: 0, invalidSlabs: 1, slabsWithoutPhotos: ["STALE-1"],
    });
  });

  test("handles no upload and no calibration without throwing", () => {
    expect(inventorySummaryDisplay(null, null)).toEqual({
      validSlabs: 0, invalidSlabs: 0, slabsWithoutPhotos: [],
    });
  });
});
