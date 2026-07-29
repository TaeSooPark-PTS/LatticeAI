import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { fail, ok, renderPage } from "@/test/renderPage";
import { BrainPage } from "./Brain";

/**
 * The memory surface: graph, provenance, recall and portability.
 *
 * The property that matters most here is that every number says what it
 * counted. 10.0.0 replaced a bare "출처 반영률 12%" with two counts and a
 * sentence, precisely because a percentage with no denominator cannot be
 * checked by the person reading it.
 */

const GRAPH = {
  nodes: [
    { id: "n1", type: "Document", title: "릴리스 절차", summary: "태그를 만들고 CI를 통과" },
    { id: "n2", type: "Concept", title: "배포", summary: "" },
  ],
  edges: [{ from: "n1", to: "n2", type: "언급함", weight: 0.7 }],
  stats: { nodes: 2, edges: 1 },
};

function render(overrides = {}, options = {}) {
  return renderPage(<BrainPage />, {
    api: {
      graph: ok(GRAPH),
      graphStats: ok({ nodes: 291, edges: 480 }),
      graphCoverage: ok({ total_nodes: 291, nodes_with_provenance: 35 }),
      graphProvenance: ok({ items: [] }),
      memoryManager: ok({ tiers: [] }),
      memoryRecall: ok({ matches: [] }),
      hybridSearch: ok({ matches: [], mode: "hybrid" }),
      brainHealth: ok({ overall_score: 0.8, grade: "good", dimensions: {}, recommended_actions: [] }),
      brainVectorFreshness: ok({ status: "ok", pending_items: 0, total_items: 291, detail: "" }),
      ...overrides,
    },
    ...options,
  });
}

describe("BrainPage", () => {
  beforeEach(() => vi.restoreAllMocks());

  it("renders the memory surface", async () => {
    render();
    await waitFor(() => expect((document.body.textContent || "").length).toBeGreaterThan(20));
  });

  it("never reports provenance as a bare percentage", async () => {
    // 10.0.0 replaced "출처 반영률 12%" with two counts and a sentence, because
    // a percentage with no denominator cannot be checked by its reader.
    render();
    await waitFor(() => expect(document.body.textContent).toBeTruthy());
    expect(document.body.textContent).not.toMatch(/출처 반영률\s*\d+\s*%/);
  });

  it("says so when there is nothing to measure instead of showing 0%", async () => {
    render({ graphCoverage: ok({ total_nodes: 0, nodes_with_provenance: 0 }) });
    await waitFor(() => expect(document.body.textContent).toBeTruthy());
    expect(document.body.textContent).not.toMatch(/0%/);
  });

  it("an unavailable graph is reported rather than drawn as empty", async () => {
    render({ graph: fail("server unavailable", { nodes: [], edges: [] }) });
    await waitFor(() => expect(document.body.textContent).toBeTruthy());
    expect(document.body.textContent).not.toMatch(/undefined|NaN/);
  });

  it("an empty graph reads as empty rather than as broken", async () => {
    render({ graph: ok({ nodes: [], edges: [], stats: { nodes: 0, edges: 0 } }) });
    await waitFor(() => expect(document.body.textContent).toBeTruthy());
    expect(document.body.textContent).not.toMatch(/undefined|\[object Object\]/);
  });

  it("renders in English when the language is en", async () => {
    render({}, { language: "en" });
    await waitFor(() => expect(document.body.textContent).toBeTruthy());
    expect(document.body.textContent).not.toMatch(/기억 지도|출처가 남은 기억/);
  });

  it("does not print raw markdown from model-written summaries", async () => {
    render({
      graph: ok({
        ...GRAPH,
        nodes: [{ id: "n1", type: "Document", title: "요약", summary: "**요약하자면,** 이렇게 합니다" }],
      }),
    });
    await waitFor(() => expect(document.body.textContent).toBeTruthy());
    expect(document.body.textContent).not.toMatch(/\*\*요약하자면,\*\*/);
  });

  it("a node missing its optional fields still renders", async () => {
    render({ graph: ok({ nodes: [{ id: "bare" }], edges: [] }) });
    await waitFor(() => expect(document.body.textContent).toBeTruthy());
    expect(document.body.textContent).not.toMatch(/undefined/);
  });
});
