import { describe, expect, it } from "vitest";

import { renderPage } from "@/test/renderPage";
import { DepthEmergence } from "./DepthEmergence";
import type { BrainDepth, KnowledgeConcept, MemoryFragment, RelationshipThread } from "./types";

const memories: MemoryFragment[] = [
  { id: "m1", title: "회의 메모", kind: "Note", tags: [], agentGenerated: false },
];

const concepts: KnowledgeConcept[] = [
  { id: "c1", label: "여행", type: "topic", summary: "", importance: 2 },
  { id: "c2", label: "예산", type: "topic", summary: "", importance: 1 },
];

const relationships: RelationshipThread[] = [
  { id: "r1", source: "c1", target: "c2", label: "relates", weight: 1 },
];

function renderDepth(depth: BrainDepth) {
  return renderPage(
    <DepthEmergence
      depth={depth}
      memories={memories}
      concepts={concepts}
      relationships={relationships}
      graphModel={{ nodes: concepts, edges: relationships }}
      graphSearch=""
      selectedGraphId={null}
      onGraphSearch={() => {}}
      onSelectGraphNode={() => {}}
      onRecallMemory={() => {}}
    />,
  );
}

const layers = (container: HTMLElement) => ({
  memory: container.querySelector(".memory-fragment"),
  knowledge: container.querySelector(".concept-signal"),
  weave: container.querySelector(".relationship-weave"),
  graph: container.querySelector(".mind-core-graph"),
});

describe("DepthEmergence", () => {
  it("renders nothing at depth 1 — the surface belongs to the orb alone", () => {
    const { container } = renderDepth(1);
    expect(container.firstChild).toBeNull();
  });

  it("reveals only the memory layer at depth 2", () => {
    const { container } = renderDepth(2);
    const seen = layers(container);
    expect(seen.memory).toBeTruthy();
    expect(seen.knowledge).toBeNull();
    expect(seen.weave).toBeNull();
    expect(seen.graph).toBeNull();
  });

  it("adds concepts at depth 3", () => {
    const { container } = renderDepth(3);
    const seen = layers(container);
    expect(seen.memory).toBeTruthy();
    expect(seen.knowledge).toBeTruthy();
    expect(seen.weave).toBeNull();
    expect(seen.graph).toBeNull();
  });

  it("weaves relationships at depth 4", () => {
    const { container } = renderDepth(4);
    const seen = layers(container);
    expect(seen.memory).toBeTruthy();
    expect(seen.knowledge).toBeTruthy();
    expect(seen.weave).toBeTruthy();
    expect(seen.graph).toBeNull();
  });

  it("swaps the mid layers for the full graph at depth 5", () => {
    const { container } = renderDepth(5);
    const seen = layers(container);
    expect(seen.memory).toBeTruthy();
    expect(seen.graph).toBeTruthy();
    // The knowledge/relationship sketches yield to the real graph view.
    expect(seen.weave).toBeNull();
    expect(seen.knowledge).toBeNull();
  });

  it("stays blank for an out-of-range depth instead of guessing a layer", () => {
    // The prop is typed 1–5, but depth ultimately derives from runtime state;
    // a defensive 0 must render no layer rather than a broken one.
    const { container } = renderDepth(0 as BrainDepth);
    const seen = layers(container);
    expect(seen.memory).toBeNull();
    expect(seen.knowledge).toBeNull();
    expect(seen.weave).toBeNull();
    expect(seen.graph).toBeNull();
  });
});
