import { describe, expect, it } from "vitest";

import {
  agentPayloadFiles,
  buildBrainProof,
  buildConversationSummaries,
  extractIngestionEvidence,
  parseAgentStepEvent,
  parseAgentTranscript,
  parseContextQuality,
  parseConversationMessages,
  parseIngestionJobs,
  parseIngestionWatchStatus,
  parseKnowledgeGraph,
  parseLoopSummary,
  parsePendingApprovals,
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

  it("parses live agent_step frames and ignores unknown extra fields", () => {
    expect(parseAgentStepEvent({
      phase: "execute",
      event: "tool",
      action: "write_file",
      path: "out/notes.html",
      step: 2,
      ok: true,
      totally_new_field: { nested: true },
    })).toEqual({
      phase: "execute",
      event: "tool",
      action: "write_file",
      path: "out/notes.html",
      step: 2,
      ok: true,
    });
    expect(parseAgentStepEvent({ phase: "verify", event: "verdict", verdict: "NEEDS_REVIEW" }))
      .toEqual({ phase: "verify", event: "verdict", verdict: "NEEDS_REVIEW" });
    expect(parseAgentStepEvent({ event: "tool" })).toBeNull();
    expect(parseAgentStepEvent("not a record")).toBeNull();
  });

  it("derives a post-hoc timeline from the payload steps transcript", () => {
    const steps = parseAgentTranscript([
      { state: "EXECUTING", action: "write_file", args: { path: "page.html" }, result: { bytes: 12 } },
      { state: "EXECUTING", action: "read_file", args: { path: "a.txt" }, error: "not found" },
      { state: "DONE" },
      "garbage",
      {},
    ]);

    expect(steps).toEqual([
      { phase: "execute", event: "tool", action: "write_file", path: "page.html", ok: true },
      { phase: "execute", event: "tool", action: "read_file", path: "a.txt", ok: false, detail: "not found" },
      { phase: "terminal", event: "state", state: "DONE" },
    ]);
  });

  it("summarizes payload.loop repairs and stays null when nothing was repaired", () => {
    expect(parseLoopSummary({
      repairs: { json_fence: 2, tool_name: 1, zero: 0 },
      parse_errors: 3,
      parse_recovered: 2,
    })).toEqual({
      repairs: { json_fence: 2, tool_name: 1 },
      parseErrors: 3,
      parseRecovered: 2,
      total: 5,
    });
    expect(parseLoopSummary({ repairs: {}, parse_errors: 0, parse_recovered: 0 })).toBeNull();
    expect(parseLoopSummary(undefined)).toBeNull();
  });

  it("joins the payload-level brain_ingest verdict onto created files", () => {
    // Single-file path: one dict, applied to the only file.
    const single = agentPayloadFiles({
      created_files: [{ path: "out/page.html", filename: "page.html", bytes: 10 }],
      brain_ingest: { status: "ok", node_id: "doc:1", chunk_count: 2, duplicate: false },
    });
    expect(single[0].brainIngest).toEqual({ status: "ok" });

    // Bundle path: list keyed by path; unknown statuses stay silent.
    const bundle = agentPayloadFiles({
      created_files: [
        { path: "site/index.html", filename: "index.html", bytes: 10 },
        { path: "site/style.css", filename: "style.css", bytes: 5 },
        { path: "site/app.js", filename: "app.js", bytes: 4 },
      ],
      brain_ingest: [
        { path: "site/index.html", status: "failed", detail: "chunker crashed" },
        { path: "site/style.css", status: "skipped" },
      ],
    });
    expect(bundle[0].brainIngest).toEqual({ status: "failed", detail: "chunker crashed" });
    expect(bundle[1].brainIngest).toBeUndefined();
    expect(bundle[2].brainIngest).toBeUndefined();

    // A single dict never fans out across multiple files.
    const ambiguous = agentPayloadFiles({
      created_files: [
        { path: "a.html", filename: "a.html", bytes: 1 },
        { path: "b.html", filename: "b.html", bytes: 1 },
      ],
      brain_ingest: { status: "ok" },
    });
    expect(ambiguous.every((file) => file.brainIngest === undefined)).toBe(true);

    // Absent field → unknown → nothing.
    expect(agentPayloadFiles({
      created_files: [{ path: "c.html", filename: "c.html", bytes: 1 }],
    })[0].brainIngest).toBeUndefined();
  });

  it("parses pending approvals and drops rows without a run id", () => {
    expect(parsePendingApprovals({ pending: [
      { run_id: "run-7", goal: "정리 보고서 만들기", expires_at: "2026-07-26T09:00:00+09:00" },
      { goal: "no id" },
    ] })).toEqual([
      { runId: "run-7", goal: "정리 보고서 만들기", expiresAt: "2026-07-26T09:00:00+09:00" },
    ]);
    expect(parsePendingApprovals(null)).toEqual([]);
  });

  it("parses watch status defensively, including absent last_result/last_errors", () => {
    const status = parseIngestionWatchStatus({
      enabled_count: 2,
      polling: false,
      interval_seconds: 60,
      watches: [
        {
          id: "watch_1",
          path: "/Users/me/Documents/notes",
          enabled: true,
          last_scan_at: "2026-07-26T08:00:00+09:00",
          last_result: { status: "ok", new: 2, ingested: 2, failed: 1 },
          tracked_files: 40,
          last_errors: [{ path: "/Users/me/Documents/notes/bad.pdf", detail: "parse failed" }, "loose error"],
        },
        { id: "watch_2", path: "/tmp/other", enabled: false },
        { bogus: true },
      ],
    });

    expect(status.enabledCount).toBe(2);
    expect(status.polling).toBe(false);
    expect(status.intervalSeconds).toBe(60);
    expect(status.watches).toHaveLength(2);
    expect(status.watches[0]).toMatchObject({
      id: "watch_1",
      enabled: true,
      lastResult: { status: "ok", ingested: 2, failed: 1 },
      trackedFiles: 40,
    });
    expect(status.watches[0].lastErrors).toEqual([
      { path: "/Users/me/Documents/notes/bad.pdf", detail: "parse failed" },
      { path: "", detail: "loose error" },
    ]);
    expect(status.watches[1].lastResult).toBeNull();
    expect(status.watches[1].lastErrors).toEqual([]);
  });
});
