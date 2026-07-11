import { describe, expect, it } from "vitest";

import {
  buildBrainProof,
  buildConversationSummaries,
  parseConversationMessages,
  parseKnowledgeGraph,
} from "./brainData";

describe("brainData parsers", () => {
  it("normalizes and sorts conversation summaries while rejecting rows without ids", () => {
    expect(buildConversationSummaries([
      { id: "older", title: "Older", message_count: 2, updated_at: "2026-07-10T00:00:00Z" },
      { summary: "missing id" },
      { conversation_id: "newer", last_message: "Latest", messages: 4, updatedAt: 1_783_728_000_000 },
    ])).toEqual([
      expect.objectContaining({ id: "newer", title: "Latest", messageCount: 4 }),
      expect.objectContaining({ id: "older", title: "Older", messageCount: 2 }),
    ]);
  });

  it("keeps only renderable user and assistant messages", () => {
    expect(parseConversationMessages({ messages: [
      { role: "system", content: "hidden" },
      { role: "user", content: "Question" },
      { role: "assistant", content: "  " },
      { role: "assistant", content: "Answer" },
    ] })).toEqual([
      { role: "user", content: "Question" },
      { role: "assistant", content: "Answer" },
    ]);
  });

  it("drops graph edges that point to unavailable nodes", () => {
    const graph = parseKnowledgeGraph({
      nodes: [{ id: "a", title: "Alpha" }, { node_id: "b", label: "Beta" }],
      edges: [
        { source: "a", target: "b", type: "supports" },
        { source: "a", target: "missing", type: "leaks" },
      ],
    });

    expect(graph.nodes.map((node) => node.id)).toEqual(["a", "b"]);
    expect(graph.edges).toHaveLength(1);
    expect(graph.edges[0]).toMatchObject({ source: "a", target: "b", label: "Supports" });
  });

  it("models an ok:false proof response as unavailable instead of healthy quiet", () => {
    const proof = buildBrainProof({ ok: false, status: 503, data: {}, source: "unavailable" }, "local-model");

    expect(proof.status).toBe("unavailable");
    expect(proof.modelContinuity.capability).toBe(false);
    expect(proof.modelContinuity.activeModel).toBe("local-model");
    expect(proof.proofs.hasDurableEvidence).toBe(false);
    expect(proof.claims.keepsContextAcrossModels).toBe(false);
  });
});
