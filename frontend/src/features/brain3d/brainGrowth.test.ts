import { describe, expect, it } from "vitest";
import {
  classifyHemisphere,
  computeBrainGrowth,
  colorForNodeType,
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

describe("brainGrowth edge cases", () => {
  const base = { id: "x", label: "X", type: "Document" };

  it("reads a superseded marker from every place the graph can put it", () => {
    const superseded = [
      { status: "Superseded" },
      { temporal: { status: "superseded" } },
      { temporal: { superseded_by: "n2" } },
      { metadata: { lifecycle_status: "superseded" } },
      { metadata: { status: "SUPERSEDED" } },
    ];
    for (const raw of superseded) expect(isSupersededNode({ ...base, raw })).toBe(true);
    const current = [
      {},
      { status: "active", temporal: { status: "active", superseded_by: " " }, metadata: { superseded_by: "" } },
      { status: 1, temporal: "soon", metadata: "none" },
      { temporal: { status: 2, superseded_by: 3 }, metadata: { status: 4 } },
    ];
    for (const raw of current) expect(isSupersededNode({ ...base, raw })).toBe(false);
    expect(isSupersededNode({ ...base, raw: "text" as never })).toBe(false);
  });

  it("finds a recent timestamp in node, raw or metadata fields", () => {
    const now = Date.parse("2026-11-26T12:00:00Z");
    const fresh = "2026-11-25T12:00:00Z";
    const stale = Date.parse("2025-01-01T00:00:00Z");
    expect(isRecentNode({ ...base, raw: { updated_at: now - 1000 } }, now)).toBe(true);
    expect(isRecentNode({ ...base, raw: { created_at: fresh } }, now)).toBe(true);
    expect(isRecentNode({ ...base, raw: { metadata: { captured_at: fresh } } }, now)).toBe(true);
    expect(isRecentNode({ ...base, updatedAt: "  ", raw: { updated_at: stale, created_at: "never", metadata: "x" } }, now)).toBe(false);
    expect(isRecentNode({ ...base, raw: { updated_at: Number.NaN } }, now)).toBe(false);
    expect(isRecentNode({ ...base, raw: "text" as never })).toBe(false);
  });

  it("keeps a node in place when the list around it is filtered", () => {
    const growth = computeBrainGrowth(10, 10);
    const first = { id: "a", label: "A", type: "Note" };
    const second = { id: "b", label: "B", type: "Note" };
    const [, full] = layoutBrainHemispheres([first, second], growth, 0);
    const [filtered] = layoutBrainHemispheres([second], growth, 0);
    expect(filtered).toEqual(full);
  });

  it("lays out unknown types with defaults and shrinks superseded nodes", () => {
    const growth = computeBrainGrowth(-3, -1);
    expect(growth).toMatchObject({ nodeCount: 0, edgeCount: 0, stage: "seed", progress: 0 });
    const [plain, old, custom] = layoutBrainHemispheres(
      [
        { id: "p", label: "P", type: "Mystery" },
        { id: "p", label: "P", type: "Mystery", status: "superseded", updatedAt: new Date().toISOString() },
        { id: "q", label: "Q", type: "Code", group: "source", degree: 40, importance: 9 },
      ],
      growth,
    );
    expect(plain).toMatchObject({ hemisphere: "right", group: "knowledge", importance: 0.45, colorHex: colorForNodeType("unknown") });
    expect(plain!.x).toBeGreaterThan(0);
    expect(old).toMatchObject({ superseded: true, recent: false });
    expect(old!.radius).toBeCloseTo(plain!.radius * 0.72, 3);
    expect(custom).toMatchObject({ hemisphere: "left", group: "source", importance: 1 });
    expect(custom!.x).toBeLessThan(0);
  });

  it("has no highlight without a selection", () => {
    expect(computeOneHopHighlight(null, [{ source: "a", target: "b" }]).size).toBe(0);
  });

  it("collects fired ids from every trace spelling and skips junk", () => {
    for (const payload of [null, "trace", { graph_nodes: ["  ", 7, null, {}, { id: " " }] }]) {
      expect(extractFiringTrace(payload)).toBeNull();
    }
    const trace = extractFiringTrace({
      matched_nodes: [{ node_id: "m1" }],
      node_ids: ["m2"],
      nodeIds: [" m3 "],
      edges: [null, "edge", { source_id: "s", target_id: "t" }, { source: "s" }, { from: "", to: "t" }, { from: 1, to: 2 }],
    });
    expect(trace!.nodeIds).toEqual(["m1", "m2", "m3", "s", "t"]);
    expect(trace!.edges).toEqual([{ source: "s", target: "t" }]);
  });
});
