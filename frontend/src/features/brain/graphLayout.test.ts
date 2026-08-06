import { describe, expect, it } from "vitest";

import { clamp, computeGraphNeighbors, layerStyle, layoutGraphNodes, polarPoint } from "./graphLayout";
import type { KnowledgeConcept, RelationshipThread } from "./types";

const concept = (id: string): KnowledgeConcept => ({
  id,
  label: `개념 ${id}`,
  type: "topic",
  summary: "",
  importance: 1,
});

const edge = (id: string, source: string, target: string): RelationshipThread => ({
  id,
  source,
  target,
  label: "relates",
  weight: 1,
});

describe("polarPoint", () => {
  it.each([
    // index, total, radiusX, radiusY, offset, expected x, expected y
    [0, 4, 40, 20, 0, 90, 50], // angle 0° → pure cosine on x
    [1, 4, 40, 20, 0, 50, 70], // angle 90° → pure sine on y
    [2, 4, 40, 20, 0, 10, 50], // angle 180°
    [0, 1, 10, 10, 90, 50, 60], // single item honors offset
  ])(
    "places index %i of %i at (%i, %i) radius with offset %i°",
    (index, total, radiusX, radiusY, offset, x, y) => {
      const point = polarPoint(index, total, radiusX, radiusY, offset);
      expect(point.x).toBeCloseTo(x, 5);
      expect(point.y).toBeCloseTo(y, 5);
    },
  );

  it("defaults the offset to -90° (first item straight up)", () => {
    const point = polarPoint(0, 4, 40, 20);
    expect(point.x).toBeCloseTo(50, 5);
    expect(point.y).toBeCloseTo(30, 5);
  });

  it("treats a zero total as a single slot instead of dividing by zero", () => {
    const point = polarPoint(0, 0, 40, 20, 0);
    expect(point.x).toBeCloseTo(90, 5);
    expect(point.y).toBeCloseTo(50, 5);
    expect(Number.isFinite(point.x)).toBe(true);
  });
});

describe("layoutGraphNodes", () => {
  it("returns one positioned entry per node with the -88° offset", () => {
    const nodes = [concept("a"), concept("b"), concept("c")];
    const layout = layoutGraphNodes(nodes, 30, 20);
    expect(layout).toHaveLength(3);
    expect(layout[0].node).toBe(nodes[0]);
    const expected = polarPoint(0, 3, 30, 20, -88);
    expect(layout[0].x).toBeCloseTo(expected.x, 5);
    expect(layout[0].y).toBeCloseTo(expected.y, 5);
    // Entries are distinct positions around the ellipse.
    expect(layout[1].x).not.toBeCloseTo(layout[2].x, 5);
  });

  it("maps an empty list to an empty layout", () => {
    expect(layoutGraphNodes([], 30, 20)).toEqual([]);
  });
});

describe("computeGraphNeighbors", () => {
  it("collects 1-hop neighbors from both edge directions, excluding the node itself", () => {
    const edges = [
      edge("e1", "me", "out"), // source side
      edge("e2", "in", "me"), // target side
      edge("e3", "x", "y"), // unrelated
    ];
    const neighbors = computeGraphNeighbors("me", edges);
    expect(neighbors).toEqual(new Set(["out", "in"]));
    expect(neighbors.has("me")).toBe(false);
  });

  it("returns an empty set when no edge touches the node", () => {
    expect(computeGraphNeighbors("lonely", [edge("e1", "a", "b")])).toEqual(new Set());
    expect(computeGraphNeighbors("lonely", [])).toEqual(new Set());
  });
});

describe("layerStyle", () => {
  it("passes CSS custom property records through unchanged", () => {
    const style = layerStyle({ "--x": "10%", "--y": "20%" });
    expect(style).toEqual({ "--x": "10%", "--y": "20%" });
  });
});

describe("clamp re-export", () => {
  it("keeps the shared clamp reachable from the graph layout module", () => {
    expect(clamp(5, 0, 3)).toBe(3);
    expect(clamp(-2, 0, 3)).toBe(0);
    expect(clamp(1.5, 0, 3)).toBe(1.5);
  });
});
