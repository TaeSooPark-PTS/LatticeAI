import { describe, expect, it } from "vitest";
import {
  classifyHemisphere,
  computeBrainGrowth,
  computeBrainStage,
  computeOneHopHighlight,
  extractFiringTrace,
  isRecentNode,
  isSupersededNode,
  layoutBrainHemispheres,
} from "./brainGrowth";

describe("brainGrowth", () => {
  it("progresses through seed, cortex, neural_web, and lattice stages", () => {
    expect(computeBrainStage(0)).toBe("seed");
    expect(computeBrainStage(14)).toBe("seed");
    expect(computeBrainStage(15)).toBe("cortex");
    expect(computeBrainStage(80)).toBe("neural_web");
    expect(computeBrainStage(300)).toBe("lattice");

    const seed = computeBrainGrowth(3, 2);
    const lattice = computeBrainGrowth(450, 900);
    expect(lattice.scale).toBeGreaterThan(seed.scale);
    expect(lattice.hemisphereVolume).toBeGreaterThan(seed.hemisphereVolume);
    expect(lattice.density).toBeGreaterThan(seed.density);
    expect(lattice.luminosity).toBeGreaterThan(seed.luminosity);
    expect(lattice.dustCount).toBeGreaterThan(seed.dustCount);
  });

  it("clusters analytical types to left hemisphere and semantic types to right hemisphere", () => {
    expect(classifyHemisphere("Decision")).toBe("left");
    expect(classifyHemisphere("Document")).toBe("left");
    expect(classifyHemisphere("Concept")).toBe("right");
    expect(classifyHemisphere("Note")).toBe("right");

    const growth = computeBrainGrowth(40, 60);
    const points = layoutBrainHemispheres(
      [
        { id: "d1", label: "Spec", type: "Document", importance: 0.8 },
        { id: "c1", label: "Idea", type: "Concept", importance: 0.7 },
      ],
      growth,
    );
    expect(points[0]!.hemisphere).toBe("left");
    expect(points[0]!.x).toBeLessThan(0);
    expect(points[1]!.hemisphere).toBe("right");
    expect(points[1]!.x).toBeGreaterThan(0);
  });

  it("detects superseded and recent nodes and computes 1-hop neighborhood", () => {
    const now = Date.parse("2026-11-26T12:00:00Z");
    expect(isSupersededNode({ id: "a", label: "Old", type: "Document", status: "superseded" })).toBe(true);
    expect(
      isSupersededNode({
        id: "b",
        label: "Replaced",
        type: "Document",
        raw: { metadata: { superseded_by: "c" } },
      }),
    ).toBe(true);
    expect(isSupersededNode({ id: "c", label: "Current", type: "Document", status: "active" })).toBe(false);
    expect(isRecentNode({ id: "c", label: "Current", type: "Document", updatedAt: "2026-11-25T10:00:00Z" }, now)).toBe(true);
    expect(isRecentNode({ id: "old", label: "Old", type: "Document", updatedAt: "2025-01-01T00:00:00Z" }, now)).toBe(false);

    const hop = computeOneHopHighlight("n1", [
      { source: "n1", target: "n2" },
      { source: "n3", target: "n1" },
      { source: "n2", target: "n4" },
    ]);
    expect(Array.from(hop).sort()).toEqual(["n1", "n2", "n3"]);
  });

  it("extracts matched nodes and edges from build_graph_trace payloads", () => {
    const trace = extractFiringTrace({
      graph_nodes: ["n1", { id: "n2" }],
      graph_edges: [{ from: "n1", to: "n2" }, { source: "n2", target: "n3" }],
    });
    expect(trace).not.toBeNull();
    expect(trace!.nodeIds).toContain("n1");
    expect(trace!.nodeIds).toContain("n2");
    expect(trace!.nodeIds).toContain("n3");
    expect(trace!.edges).toHaveLength(2);
    expect(extractFiringTrace({})).toBeNull();
  });
});
