import { describe, expect, it } from "vitest";

import {
  buildBrainProof,
  buildConversationSummaries,
  extractIngestionEvidence,
  parseContextQuality,
  parseConversationMessages,
  parseIngestionJobs,
  parseKnowledgeGraph,
  parseVectorFreshness,
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

  it("reads additive extraction_quality with warnings from ingest responses", () => {
    const evidence = extractIngestionEvidence({
      status: "ok",
      extraction_quality: { score: 0.21, level: "low", reasons: ["ocr_noise"] },
      warnings: ["표 내용 일부를 읽지 못했어요"],
    });

    expect(evidence.extraction).toEqual({
      score: 0.21,
      level: "low",
      reasons: ["ocr_noise"],
      warnings: ["표 내용 일부를 읽지 못했어요"],
    });
    expect(extractIngestionEvidence({ status: "ok" }).extraction).toBeUndefined();
  });

  it("parses context_quality from the trailer, the trace record, or not at all", () => {
    const expected = { mode: "lexical_only", nodes: 0, limited: true, reason: "vector index pending" };
    expect(parseContextQuality({ context_quality: expected })).toEqual(expected);
    expect(parseContextQuality({ trace: { context_quality: expected } })).toEqual(expected);
    expect(parseContextQuality({ mode: "hybrid", nodes: 4, limited: false, reason: null }))
      .toEqual({ mode: "hybrid", nodes: 4, limited: false, reason: null });
    expect(parseContextQuality({ trace: { sources: [] } })).toBeNull();
    expect(parseContextQuality(null)).toBeNull();
  });

  it("normalizes vector freshness and defaults to unavailable", () => {
    expect(parseVectorFreshness({ status: "pending", pending_items: 7, total_items: 40, detail: "reindexing" }))
      .toEqual({ status: "pending", pendingItems: 7, totalItems: 40, detail: "reindexing" });
    expect(parseVectorFreshness(null).status).toBe("unavailable");
  });

  it("parses ingestion jobs and drops rows without a job id", () => {
    const jobs = parseIngestionJobs({ jobs: [
      { job_id: "job-1", status: "running", total: 20, processed: 8, failed: 1, errors: ["a.pdf"] },
      { status: "queued" },
    ] });

    expect(jobs).toHaveLength(1);
    expect(jobs[0]).toMatchObject({ jobId: "job-1", status: "running", total: 20, processed: 8, failed: 1 });
  });
});
