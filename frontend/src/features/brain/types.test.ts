import { describe, expect, it } from "vitest";

import { DEPTHS, INGESTION_STAGE_ORDER } from "./types";

describe("brain type runtime constants", () => {
  it("orders the ingestion pipeline from preparing to complete", () => {
    // The stage track renders progress by index in this array; a reorder
    // would silently show "indexing" before "parsing".
    expect(INGESTION_STAGE_ORDER).toEqual([
      "preparing",
      "parsing",
      "embedding",
      "indexing",
      "complete",
    ]);
  });

  it("maps every exploration depth to a label key and a brain state", () => {
    expect(DEPTHS.map((depth) => depth.level)).toEqual([1, 2, 3, 4, 5]);
    for (const depth of DEPTHS) {
      expect(depth.labelKey).toBe(`brain.depthLabel.${depth.level}`);
      expect(depth.state).toBeTruthy();
    }
  });
});
