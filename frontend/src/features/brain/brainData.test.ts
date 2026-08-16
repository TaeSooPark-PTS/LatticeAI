import { describe, expect, it } from "vitest";

import {
  agentPayloadFiles,
  currentModelName,
  hasLoadedModel,
  humanizeModelId,
  buildBrainBrief,
  buildBrainProof,
  buildBrainReadiness,
  buildConversationSummaries,
  buildMemoryFragments,
  extractIngestionEvidence,
  parseAgentStepEvent,
  parseAgentTranscript,
  cloudAnswerFromDone,
  parseContextQuality,
  parseConversationMessages,
  parseExtractionQuality,
  parseGrounding,
  parseHybridContext,
  parseHybridDone,
  parseKgExpansion,
  parseIngestionJobs,
  parseIngestionWatchStatus,
  parseKnowledgeGraph,
  parseLoopSummary,
  parsePendingApprovals,
  parseRunExplanation,
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

describe("hybrid cloud frame parsers", () => {
  it("keeps hybrid_context node ids and keywords, and rejects other types", () => {
    expect(parseHybridContext({
      type: "hybrid_context", node_ids: ["n1", "", 3], keywords: ["release", ""],
    })).toEqual({ nodeIds: ["n1"], keywords: ["release"] });
    expect(parseHybridContext({ node_ids: ["n2"] })).toEqual({ nodeIds: ["n2"], keywords: [] });
    expect(parseHybridContext({ type: "hybrid_done", node_ids: ["n1"] })).toBeNull();
    expect(parseHybridContext(null)).toBeNull();
    expect(parseHybridContext({ type: "hybrid_context" })).toEqual({ nodeIds: [], keywords: [] });
    expect(parseHybridContext({ keywords: ["only"] })).toBeNull();
    expect(parseHybridContext({ nodeIds: ["n3"] })).toEqual({ nodeIds: ["n3"], keywords: [] });
  });

  it("reads hybrid_done answer, identity, and a staged expansion summary", () => {
    const done = parseHybridDone({
      type: "hybrid_done",
      answer: "클라우드 답",
      provider: "Antigravity",
      model: "gemini-3.7-flash",
      sent_node_ids: ["n1", "n2"],
      kg_expansion: {
        status: "queued_for_review",
        review_item_id: "rev-1",
        plan: { provenance: { candidate_count: 3 }, new_nodes: [{}, {}] },
      },
    });
    expect(done).toMatchObject({
      answer: "클라우드 답",
      provider: "Antigravity",
      model: "gemini-3.7-flash",
      sentNodeIds: ["n1", "n2"],
      expansion: { status: "queued_for_review", candidateCount: 3, stagedForReview: true },
    });
    expect(cloudAnswerFromDone(done!, 0)).toMatchObject({
      sentNodeCount: 2,
      model: "gemini-3.7-flash",
    });
  });

  it("falls back to plan node count and sent-node count from the in-flight context", () => {
    expect(parseKgExpansion({ status: "staged", plan: { new_nodes: [{}, {}, {}] } }))
      .toEqual({ status: "staged", candidateCount: 3, stagedForReview: true });
    expect(parseKgExpansion({ review_item_id: "x" }))
      .toMatchObject({ status: "staged", stagedForReview: true, candidateCount: 0 });
    expect(parseKgExpansion(null)).toBeNull();
    expect(parseHybridDone({ type: "hybrid_context" })).toBeNull();
    expect(parseHybridDone(null)).toBeNull();
    expect(parseHybridDone({ answer: "only" })?.answer).toBe("only");
    expect(parseHybridDone({ kgExpansion: { status: "staged" } })?.expansion?.status).toBe("staged");
    expect(parseHybridDone({ model: "x" })).toBeNull();
    expect(cloudAnswerFromDone({
      answer: "", provider: "", model: "", sentNodeIds: [], expansion: null,
    }, 4).sentNodeCount).toBe(4);
  });
});

describe("currentModelName", () => {
  it("prefers the catalog display name for the loaded model", () => {
    const payload = {
      current_model: "mlx-community/gemma-4-26b-a4b-it-4bit",
      recommended: [
        { id: "mlx-community/gemma-4-26b-a4b-it-4bit", display_name: "Gemma 4 26B Instruct" },
      ],
    };
    expect(currentModelName(payload)).toBe("Gemma 4 26B Instruct");
  });

  it("tidies the raw id when the catalog has no entry", () => {
    expect(currentModelName({ current_model: "mlx-community/gemma-4-26b-a4b-it-4bit" }))
      .toBe("Gemma 4 26b A4b It");
  });

  it("falls back to a placeholder when nothing is loaded", () => {
    expect(currentModelName({})).toBe("local mind");
  });

  it("never shows a package coordinate", () => {
    expect(humanizeModelId("mlx-community/Qwen3-VL-8B-Instruct-4bit")).not.toContain("/");
    expect(humanizeModelId("mlx-community/Qwen3-VL-8B-Instruct-4bit")).not.toContain("4bit");
  });

  it("skips catalog rows for other models and rows whose label repeats the id", () => {
    expect(currentModelName({
      current: "target-model",
      recommended: [{ id: "other-model", display_name: "Other" }],
      cloud: [{ id: "target-model", display_name: "target-model" }],
      loaded: [{ id: "target-model", display_name: "Target Pro" }],
    })).toBe("Target Pro");
  });

  it("names the brain from the loaded pool when nothing is current", () => {
    // model_id-only row → tidied id.
    expect(currentModelName({ loaded: [{ model_id: "mlx/loaded-x-4bit" }] })).toBe("Loaded X");
    // Display name straight from the row.
    expect(currentModelName({ loaded_models: [{ name: "친근한 모델" }] })).toBe("친근한 모델");
    // Loaded id resolved through the catalog pools.
    expect(currentModelName({
      loaded: [{ id: "pretty-id" }],
      models: [{ id: "pretty-id", display_name: "Pretty Model" }],
    })).toBe("Pretty Model");
    // Label repeating the id falls through to the tidied id.
    expect(currentModelName({ loaded: [{ id: "same", display_name: "same" }] })).toBe("Same");
    // Rows with no identity at all cannot name anything.
    expect(currentModelName({ loaded: [{}] })).toBe("local mind");
    expect(currentModelName({ loaded: [{ name: "   " }] })).toBe("local mind");
    expect(currentModelName(null)).toBe("local mind");
  });
});

describe("hasLoadedModel", () => {
  it("checks the current model first, then any identifiable loaded row", () => {
    expect(hasLoadedModel({ current_model: "m" })).toBe(true);
    expect(hasLoadedModel({ loaded: [{ nothing: 1 }, { model_id: "x" }] })).toBe(true);
    expect(hasLoadedModel({ loaded_models: [{ name: "n" }] })).toBe(true);
    expect(hasLoadedModel({ loaded: [{}] })).toBe(false);
    expect(hasLoadedModel({})).toBe(false);
    expect(hasLoadedModel(null)).toBe(false);
  });
});

describe("buildMemoryFragments", () => {
  it("blends recent memories, sources and conversations, deduplicated", () => {
    const fragments = buildMemoryFragments({
      sources: [
        { id: "workspace", label: "Workspace Memory", type: "sqlite" },
        { id: "mystery", title: "Custom Source", health: "healthy" },
        { path: "/tmp/notes" },
      ],
      recent_memories: [
        { id: "m1", content: "긴   내용  줄바꿈", tags: ["agent-synthesis", "", 3], kind: "note" },
        { id: "m2", summary: "요약", metadata: { source: "agent_runtime" } },
        { kind: "distillation", metadata: { source: "agent_runtime_synthesis" } },
        { id: "m4", detail: "세부" },
      ],
    }, [
      { id: "c1", title: "대화 제목" },
      { conversation_id: "c2" },
      { id: "m1", title: "같은 아이디" },
    ]);

    expect(fragments.map((fragment) => fragment.id)).toEqual([
      "m1", "m2", "recent-memory-2", "m4", "workspace", "mystery", "memory-2", "c1", "c2",
    ]);
    // Whitespace collapses in derived titles; the tier id translates.
    expect(fragments[0]).toMatchObject({ title: "긴 내용 줄바꿈", kind: "Note", tags: ["agent-synthesis"], agentGenerated: true });
    expect(fragments[1].agentGenerated).toBe(true);
    expect(fragments[2]).toMatchObject({ title: "Distillation", agentGenerated: true });
    expect(fragments[3].agentGenerated).toBe(false);
    expect(fragments[4].title).toBe("작업공간 기억");
    expect(fragments[5]).toMatchObject({ title: "Custom Source", kind: "Healthy" });
    expect(fragments[6].title).toBe("/tmp/notes");
    expect(fragments[8].title).toBe("Conversation");
  });

  it("falls back to tiers and the requested language, and survives junk", () => {
    const fragments = buildMemoryFragments({ tiers: [{ id: "workspace" }], recentMemories: [{ id: "rm" }] }, undefined, "en");
    expect(fragments.map((fragment) => fragment.id)).toEqual(["rm", "workspace"]);
    expect(fragments[1].title).toBe("Workspace memory");
    expect(buildMemoryFragments(null, undefined, "en")).toEqual([]);
  });
});

describe("brainData parser edges", () => {
  it("summaries: numeric ids, blank titles and rows without timestamps", () => {
    const summaries = buildConversationSummaries([
      { id: 7, title: "   " },
      { id: Number.NaN, conversation_id: "real", title: "실제" },
      { id: "b", title: "B" },
    ]);
    expect(summaries.map((summary) => [summary.id, summary.title])).toEqual(
      expect.arrayContaining([["7", "7"], ["real", "실제"], ["b", "B"]]),
    );
    expect(summaries.every((summary) => summary.updatedAt === undefined)).toBe(true);
  });

  it("summaries: timestamps in seconds, milliseconds, digit strings and junk", () => {
    const byId = new Map(buildConversationSummaries([
      { id: "sec", updated_at: "1700000000" },
      { id: "msec", updated_at: "1700000000123" },
      { id: "hex", updated_at: "0x10", created_at: 1_700_000_000 },
      { id: "never", updated_at: "언젠가" },
    ]).map((summary) => [summary.id, summary.updatedAt]));
    expect(byId.get("sec")).toBe(1_700_000_000_000);
    expect(byId.get("msec")).toBe(1_700_000_000_123);
    // The unparseable primary key falls through to the next timestamp key.
    expect(byId.get("hex")).toBe(1_700_000_000_000);
    expect(byId.get("never")).toBeUndefined();
  });

  it("conversation messages reject junk containers and non-text content", () => {
    expect(parseConversationMessages(null)).toEqual([]);
    expect(parseConversationMessages({ messages: [{ role: "user", content: 5 }] })).toEqual([]);
  });

  it("graph: from/to edge keys survive and a zero score is not dropped", () => {
    const graph = parseKnowledgeGraph({
      nodes: [
        { id: "a", label: "알파", importance: 0, metadata: { summary: "메타 요약", created_at: 1_700_000_000 } },
        { id: "b", name: "베타", importance_norm: 2, created_at: "2026-08-01T00:00:00Z" },
        { id: "prefix:c" },
        { bogus: true },
      ],
      edges: [
        { from: "a", to: "b", score: 0 },
        { from: "a", to: "prefix:c", weight: 0.4, id: "e-7", relationship: "supports" },
      ],
    });

    expect(graph.nodes.map((node) => node.id)).toEqual(["b", "a", "prefix:c"]);
    expect(graph.nodes[0]).toMatchObject({ label: "베타", importance: 1, createdAt: Date.parse("2026-08-01T00:00:00Z") });
    // Zero importance takes the midpoint; metadata supplies summary and time.
    expect(graph.nodes[1]).toMatchObject({ importance: 0.5, summary: "메타 요약", createdAt: 1_700_000_000_000 });
    expect(graph.nodes[2]).toMatchObject({ label: "c", type: "Concept" });
    expect(graph.nodes[2].createdAt).toBeUndefined();
    // The zero-score edge survives with the default weight — never dropped.
    expect(graph.edges).toEqual([
      { id: "edge-0", source: "a", target: "b", label: "Relates", weight: 1 },
      { id: "e-7", source: "a", target: "prefix:c", label: "Supports", weight: 0.4 },
    ]);
    expect(parseKnowledgeGraph(null)).toEqual({ nodes: [], edges: [] });
  });

  it("evidence: collects node ids, chunks and provenance across nested containers", () => {
    const evidence = extractIngestionEvidence({
      ingestion: { provenance_id: "prov-1", chunk_count: 2 },
      result: { node_id: "doc:1", duplicate: true, indexed_nodes: ["doc:2", "  ", 7, { node_id: "doc:3" }, { title: "x" }] },
      knowledge_graph: { indexed_nodes: [{ id: "doc:4" }] },
    });
    expect(evidence.nodeIds.sort()).toEqual(["doc:1", "doc:2", "doc:3", "doc:4"]);
    expect(evidence).toMatchObject({ chunkCount: 2, duplicate: true, provenanceId: "prov-1" });
    // duplicate:false must persist — a falsy verdict is still a verdict.
    expect(extractIngestionEvidence({ duplicate: false }).duplicate).toBe(false);
    expect(extractIngestionEvidence(null)).toEqual({ nodeIds: [], chunkCount: 0 });
  });

  it("evidence: extraction quality from a nested container, warnings on the quality object", () => {
    const nested = extractIngestionEvidence({
      result: { extraction_quality: { score: 0.6, level: "medium", reasons: ["r1", "", 2] } },
    });
    expect(nested.extraction).toEqual({ score: 0.6, level: "medium", reasons: ["r1"], warnings: [] });
    expect(parseExtractionQuality({ extraction_quality: { level: "high", warnings: ["w1"] } }))
      .toMatchObject({ level: "high", warnings: ["w1"] });
    expect(parseExtractionQuality({ extraction_quality: { level: "extreme" } })).toBeNull();
    expect(parseExtractionQuality(null)).toBeNull();
  });

  it("context quality without a mode stays silent", () => {
    expect(parseContextQuality({ context_quality: { limited: true } })).toBeNull();
  });

  it("grounding: root-shaped payloads, invalid statuses and blank reasons", () => {
    expect(parseGrounding({ status: "supported", label: "근거 있음" })).toEqual({ status: "supported", reason: null });
    expect(parseGrounding({ grounding: { status: "questionable" } })).toBeNull();
    expect(parseGrounding({ status: "supported" })).toBeNull();
    expect(parseGrounding({ grounding: { status: "no_context", reason: "   " } })).toEqual({ status: "no_context", reason: null });
    expect(parseGrounding({ grounding: { status: "unsupported", reason: " 인용 없음 " } })).toEqual({ status: "unsupported", reason: "인용 없음" });
    expect(parseGrounding(null)).toBeNull();
  });

  it("step events keep decision, state and error detail", () => {
    expect(parseAgentStepEvent({ phase: "plan", event: "decision", decision: "approve", state: "PLANNING", detail: "이유" }))
      .toEqual({ phase: "plan", event: "decision", decision: "approve", state: "PLANNING", detail: "이유" });
    expect(parseAgentStepEvent({ phase: "execute", event: "state", error: "폭발" }))
      .toEqual({ phase: "execute", event: "state", detail: "폭발" });
  });

  it("transcripts: errors without an action collapse into failed state markers", () => {
    expect(parseAgentTranscript([
      { state: "EXECUTING", error: "tool missing" },
      { error: "loop crashed" },
      { action: "list_files" },
    ])).toEqual([
      { phase: "execute", event: "state", state: "EXECUTING", ok: false, detail: "tool missing" },
      { phase: "execute", event: "state", ok: false, detail: "loop crashed" },
      { phase: "execute", event: "tool", action: "list_files", ok: true },
    ]);
  });

  it("loop summaries drop non-finite or non-positive repair counts", () => {
    expect(parseLoopSummary({ repairs: { junk: "many", negative: -2 }, parse_recovered: 1 }))
      .toEqual({ repairs: {}, parseErrors: 0, parseRecovered: 1, total: 1 });
    expect(parseLoopSummary({ parse_recovered: 3 }))
      .toEqual({ repairs: {}, parseErrors: 0, parseRecovered: 3, total: 3 });
  });
});

describe("parseRunExplanation", () => {
  it("stays silent for clean verified runs and junk payloads", () => {
    expect(parseRunExplanation({ code: "verified_done", ok: true, headline: { ko: "다 됐어요", en: "All done" }, details: [] }, "ko")).toBeNull();
    expect(parseRunExplanation({ headline: {}, details: [] }, "ko")).toBeNull();
    expect(parseRunExplanation(null, "ko")).toBeNull();
  });

  it("localizes the surface language and keeps only renderable details", () => {
    expect(parseRunExplanation({
      code: "needs_review",
      ok: false,
      headline: { ko: "확인 필요", en: "Needs review" },
      details: [{ ko: "다시 시도했어요", en: "Retried" }, "garbage", { ko: "한국어만" }],
      model_strain: { level: "heavy" },
    }, "en")).toEqual({
      code: "needs_review", ok: false, headline: "Needs review", details: ["Retried"], strainLevel: "heavy",
    });
  });

  it("keeps an ok run that still has details, defaulting strain to none", () => {
    expect(parseRunExplanation({ ok: true, headline: { ko: "제목" }, details: [{ ko: "덧붙임" }] }, "ko"))
      .toEqual({ code: "", ok: true, headline: "제목", details: ["덧붙임"], strainLevel: "none" });
    expect(parseRunExplanation({ ok: "yes", headline: { ko: "h" }, model_strain: { level: "odd" } }, "ko"))
      .toMatchObject({ ok: false, strainLevel: "none" });
  });
});

describe("buildBrainReadiness", () => {
  it("adopts a valid backend readiness with keys, defaults and signals", () => {
    expect(buildBrainReadiness({
      brain_readiness: {
        state: "alive", depth: 5, score: 91,
        title_key: "custom.title", action_key: "custom.action",
        signals: { memory_count: 9, concept_count: 4, relationship_count: 2, healthy_sources: 3 },
      },
    }, 0, 0)).toEqual({
      score: 91, state: "alive", depth: 5,
      titleKey: "custom.title", actionKey: "custom.action", source: "memory_service",
      signals: { memoryCount: 9, conceptCount: 4, relationshipCount: 2, healthySources: 3 },
    });

    const defaults = buildBrainReadiness({ brain_readiness: { state: "forming", depth: 3, signals: { memoryCount: 1, conceptCount: 1 } } }, 0, 0);
    expect(defaults).toMatchObject({ score: 0, titleKey: "brain.readiness.forming", actionKey: "brain.readiness.grow" });
    expect(defaults.signals).toEqual({ memoryCount: 1, conceptCount: 1, relationshipCount: 0, healthySources: 0 });

    expect(buildBrainReadiness({ brain_readiness: { state: "quiet", depth: 1 } }, 5, 5))
      .toMatchObject({ actionKey: "brain.readiness.start", signals: { memoryCount: 0 } });
  });

  it("rejects invalid backend states or depths and falls back by counts", () => {
    expect(buildBrainReadiness({ brain_readiness: { state: "sideways", depth: 3 } }, 0, 0).source).toBe("frontend_fallback");
    expect(buildBrainReadiness({ brain_readiness: { state: "alive", depth: 9 } }, 4, 4).source).toBe("frontend_fallback");
    expect(buildBrainReadiness({ brain_readiness: { state: "alive", depth: 2.5 } }, 0, 0).source).toBe("frontend_fallback");
    expect(buildBrainReadiness({ brain_readiness: { state: "alive", depth: 0 } }, 0, 0).source).toBe("frontend_fallback");

    expect(buildBrainReadiness(null, 0, 0)).toMatchObject({ state: "quiet", score: 12, depth: 2 });
    expect(buildBrainReadiness({}, 2, 1)).toMatchObject({ state: "forming", score: 38 });
    expect(buildBrainReadiness({}, 6, 4)).toMatchObject({ state: "alive", score: 100 });
  });
});

describe("buildBrainProof shapes", () => {
  it("reads a raw (non-envelope) payload with explicit fields and score bands", () => {
    const proof = buildBrainProof({
      status: "alive",
      model_continuity: { active_model: "Gemma", brain_owner: "brain", capability: false, survives_model_switch: true, proven: true, context_store: "workspace" },
      proofs: { durable_items: 4, has_durable_evidence: false, workspace_memories: 2, conversations: 1, graph_concepts: 5, vector_items: 6, healthy_sources: 2 },
      recall: { query: "질문", count: 4, items: [
        { id: "r1", source: "note_source", title: "제목", snippet: "내용", score: 0.9, matched_terms: ["제목"], confidence: "low", locator: "p.3" },
        { score: 0.5 },
        { score: 0 },
        { score: 0.8 },
      ] },
      claims: { can_recall_user_context: true, keeps_context_across_models: true, is_knowledge_store: true },
    }, "fallback-model");

    expect(proof.modelContinuity).toEqual({
      activeModel: "Gemma", brainOwner: "brain", capability: false,
      survivesModelSwitch: true, proven: true, contextStore: "workspace",
    });
    // The explicit has_durable_evidence beats the derived count.
    expect(proof.proofs).toMatchObject({ durableItems: 4, hasDurableEvidence: false, vectorItems: 6 });
    expect(proof.recall.items[0]).toMatchObject({ source: "Note Source", confidence: "low", locator: "p.3" });
    expect(proof.recall.items.map((item) => item.confidence)).toEqual(["low", "medium", "low", "high"]);
    expect(proof.recall.items[1].id).toBe("recall-1");
    expect(proof.claims).toEqual({ canRecallUserContext: true, keepsContextAcrossModels: true, isKnowledgeStore: true });
  });

  it("unwraps an ok envelope and defaults everything for silence", () => {
    const wrapped = buildBrainProof({ ok: true, status: 200, source: "live", data: { status: "forming", proofs: { durable_items: 2 } } }, "");
    expect(wrapped.status).toBe("forming");
    expect(wrapped.proofs.hasDurableEvidence).toBe(true);

    const empty = buildBrainProof(undefined);
    expect(empty.status).toBe("quiet");
    expect(empty.proofs.hasDurableEvidence).toBe(false);
    expect(empty.modelContinuity).toMatchObject({ activeModel: "", capability: true, proven: false });
  });
});

describe("buildBrainBrief", () => {
  it("fills a quiet default brief when the backend is silent", () => {
    const brief = buildBrainBrief(null);
    expect(brief).toMatchObject({
      status: "quiet", score: 0,
      headlineKey: "brain.brief.headline.quiet", bodyKey: "brain.brief.body.quiet", generatedAt: "",
    });
    expect(brief.focus).toMatchObject({ kind: "empty", empty: true, source: "Memory" });
    expect(brief.nextActions.map((action) => action.id)).toEqual(["add_source", "ask_brain"]);
    expect(brief.suggestedQuestions).toEqual([]);
    expect(brief.proactiveActions).toEqual([]);
    expect(brief.evidence.map((entry) => entry.id)).toEqual(["durable", "graph", "sources"]);
  });

  it("maps a full brief, sorts by priority and filters junk params", () => {
    const brief = buildBrainBrief({
      status: "alive", score: 250,
      headline_key: "h", body_key: "b", generated_at: "2026-08-05T00:00:00Z",
      focus: { kind: "memory", title: "초점", detail: "설명", source: "note_book", score: 0.7, empty: false },
      next_actions: [{ id: "verify_model", label_key: "l", detail_key: "d", route: "/system", priority: 5 }, {}],
      suggested_questions: [
        { id: "q-low", priority: 1, label_key: "ql", detail_key: "qd", prompt_key: "qp", params: { topic: "브레인", count: 2, junk: { nested: true } } },
        { id: "q-high", priority: 9 },
      ],
      proactive_actions: [
        { id: "p-low", intent: "delegate", prompt: "정리해줘", priority: 1, context: "not-a-record" },
        { id: "p-high", intent: "route", route: "/capture", priority: 8 },
      ],
      evidence: [{ id: "durable", label_key: "e", value: 12, detail_key: "ed" }],
    });

    expect(brief.score).toBe(100);
    expect(brief.focus).toEqual({ kind: "memory", title: "초점", detail: "설명", source: "Note Book", score: 0.7, empty: false });
    expect(brief.nextActions[0]).toMatchObject({ id: "verify_model", route: "/system", priority: 5 });
    expect(brief.nextActions[1]).toEqual({
      id: "ask_brain", labelKey: "brain.brief.action.ask", detailKey: "brain.brief.action.ask.detail", route: "", priority: 0,
    });
    expect(brief.suggestedQuestions.map((question) => question.id)).toEqual(["q-high", "q-low"]);
    expect(brief.suggestedQuestions[1].params).toEqual({ topic: "브레인", count: 2 });
    expect(brief.proactiveActions.map((action) => action.id)).toEqual(["p-high", "p-low"]);
    expect(brief.proactiveActions[1].context).toEqual({});
    expect(brief.evidence).toEqual([{ id: "durable", labelKey: "e", value: 12, detailKey: "ed" }]);
    expect(brief.generatedAt).toBe("2026-08-05T00:00:00Z");
  });

  it("accepts camelCase keys and derives focus emptiness from the title", () => {
    const brief = buildBrainBrief({
      nextActions: [{ id: "a1" }],
      suggestedQuestions: [{ id: "q1" }],
      proactiveActions: [{ id: "p1" }],
      focus: { title: "제목" },
    });
    expect(brief.nextActions.map((action) => action.id)).toEqual(["a1"]);
    expect(brief.suggestedQuestions.map((question) => question.id)).toEqual(["q1"]);
    expect(brief.proactiveActions.map((action) => action.id)).toEqual(["p1"]);
    expect(brief.focus.empty).toBe(false);
  });
});

describe("watch status and job edges", () => {
  it("keeps path-only watches, skips junk rows and unreadable errors", () => {
    const status = parseIngestionWatchStatus({
      watches: [
        "junk",
        { path: "/only/path", last_errors: ["   ", { code: 3 }, { path: "/x" }, { detail: "d" }] },
      ],
    });
    expect(status.watches).toHaveLength(1);
    expect(status.watches[0].id).toBe("/only/path");
    expect(status.watches[0].lastErrors).toEqual([{ path: "/x", detail: "" }, { path: "", detail: "d" }]);
    expect(parseIngestionWatchStatus(null)).toEqual({ enabledCount: 0, polling: false, intervalSeconds: 0, watches: [] });
  });

  it("normalizes structured job errors and keeps timestamps", () => {
    const jobs = parseIngestionJobs({ jobs: [{
      job_id: "job-2",
      created_at: "2026-08-01T00:00:00Z",
      updated_at: "2026-08-02T00:00:00Z",
      errors: [{ source: "a.pdf", detail: "깨짐" }, { detail: "이유만" }, { source: "b.md" }, {}, 42, "  "],
    }] });
    expect(jobs[0].errors).toEqual(["a.pdf — 깨짐", "이유만", "b.md"]);
    expect(jobs[0]).toMatchObject({ createdAt: "2026-08-01T00:00:00Z", updatedAt: "2026-08-02T00:00:00Z" });
    expect(parseIngestionJobs(null)).toEqual([]);
  });
});

describe("agentPayloadFiles joins", () => {
  it("joins artifact preview verdicts and falls back filename/bytes/path", () => {
    const files = agentPayloadFiles({
      created_files: [
        { path: "out/a.html", filename: "a.html", bytes: 10 },
        { path: "out/b.css" },
        { path: "" },
      ],
      artifacts: [{ path: "out/a.html", previewable: true }, null as never, { previewable: false }],
      generation: { repaired: true },
    });
    expect(files[0]).toMatchObject({ previewable: true, repaired: true });
    expect(files[1]).toMatchObject({ filename: "b.css", bytes: 0 });
    expect(files[1].previewable).toBeUndefined();
    // A degenerate empty path still yields a stable (empty) filename.
    expect(files[2].filename).toBe("");
    expect(agentPayloadFiles({})).toEqual([]);
  });
});
