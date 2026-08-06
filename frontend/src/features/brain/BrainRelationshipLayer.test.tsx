import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { BrainRelationshipLayer } from "./BrainRelationshipLayer";
import type { KnowledgeConcept, RelationshipThread } from "./types";

const concept = (id: string): KnowledgeConcept => ({
  id,
  label: `개념 ${id}`,
  type: "topic",
  summary: "",
  importance: 1,
});

const thread = (id: string, source: string, target: string): RelationshipThread => ({
  id,
  source,
  target,
  label: "relates",
  weight: 1,
});

describe("BrainRelationshipLayer", () => {
  it("renders nothing when no relationship connects two placed concepts", () => {
    const { container } = render(
      <BrainRelationshipLayer concepts={[concept("a"), concept("b")]} relationships={[]} />,
    );
    expect(container.firstChild).toBeNull();
  });

  it("draws only relationships whose endpoints are distinct, placed concepts", () => {
    const concepts = [concept("a"), concept("b"), concept("c")];
    const relationships = [
      thread("ok", "a", "b"), // both endpoints placed → drawn
      thread("missing-target", "a", "ghost"), // target not placed → dropped
      thread("missing-source", "ghost", "b"), // source not placed → dropped
      thread("self", "c", "c"), // self loop → dropped
    ];
    const { container } = render(
      <BrainRelationshipLayer concepts={concepts} relationships={relationships} />,
    );
    const lines = container.querySelectorAll("svg.relationship-weave line");
    expect(lines).toHaveLength(1);
    const line = lines[0] as SVGLineElement;
    expect(line.getAttribute("x1")).not.toBeNull();
    expect(line.getAttribute("y2")).not.toBeNull();
    expect(line.style.animationDelay).toBe("0ms");
  });

  it("caps the weave at 8 threads over the first 10 concepts", () => {
    const concepts = Array.from({ length: 12 }, (_, index) => concept(`c${index + 1}`));
    const relationships = Array.from({ length: 11 }, (_, index) =>
      thread(`r${index + 1}`, `c${(index % 9) + 1}`, `c${(index % 9) + 2}`),
    );
    const { container } = render(
      <BrainRelationshipLayer concepts={concepts} relationships={relationships} />,
    );
    expect(container.querySelectorAll("line")).toHaveLength(8);
  });
});
