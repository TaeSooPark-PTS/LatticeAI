import { fireEvent, render as renderComponent, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { latticeApi } from "@/api/client";
import { fail, ok, renderPage } from "@/test/renderPage";
import { useAppStore } from "@/store/appStore";
import { BrainPage, SourceProvenanceList, viewLabel } from "./Brain";

vi.mock("./brain/CytoscapeGraph", () => ({
  CytoscapeGraph: ({ ariaLabel, onSelect }: { ariaLabel?: string; onSelect?: (id: string | null) => void }) => (
    <div data-testid="brain-cytoscape" aria-label={ariaLabel}>
      <button type="button" data-testid="mock-select-knowledge-group" onClick={() => onSelect?.("group:knowledge")}>
        select knowledge group
      </button>
      <button type="button" data-testid="mock-select-missing-group" onClick={() => onSelect?.("group:people")}>
        select missing group
      </button>
    </div>
  ),
}));


const GRAPH = {
  nodes: [
    { id: "n1", type: "Document", title: "릴리스 절차", summary: "태그를 만들고 CI를 통과" },
    { id: "n2", type: "Concept", title: "배포", summary: "" },
  ],
  edges: [{ from: "n1", to: "n2", type: "언급함", weight: 0.7 }],
  stats: { nodes: 2, edges: 1 },
};

const RICH_GRAPH = {
  nodes: [
    { id: "n1", type: "Document", title: "릴리스 절차", summary: "태그를 만들고 CI를 통과", source: "docs/RELEASE.md", importance_norm: 0.9 },
    { id: "n2", type: "Concept", title: "배포", summary: "", importance_norm: 0.05 },
    { id: "n3", type: "Concept", title: "제3의 개념", summary: "", importance_norm: 0.5 },
    { id: "n4", type: "MysteryType", title: "이상한 항목", summary: "", importance_norm: 0.6 },
  ],
  edges: [{ from: "n1", to: "n2", type: "언급함", weight: 0.7 }],
  stats: { nodes: 3, edges: 1 },
};

function render(overrides = {}, options = {}, props: { initialTab?: string } = {}) {
  return renderPage(<BrainPage {...props} />, {
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
      selfModel: ok({ available: true, count: 0, facts: [], by_kind: {}, summary: "", kinds: [] }),
      automationReviews: ok({ items: [] }),
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

  it("offers two ways in, and the connections map is not one of them", async () => {
    render();
    await waitFor(() => expect(screen.queryAllByRole("tab").length).toBeGreaterThan(0));
    expect(screen.queryAllByRole("tab")).toHaveLength(2);
    expect(screen.queryByRole("tab", { name: "연결 지도" })).toBeNull();
  });

  it("reaches the map from a named secondary target, and offers a way back", async () => {
    render();
    await waitFor(() => expect(screen.queryAllByRole("tab")).toHaveLength(2));

    const entry = screen.getByTestId("open-connections-map");
    expect(entry.textContent).toContain("연결 지도 열기");
    await userEvent.click(entry);

    await waitFor(() => expect(screen.queryAllByRole("tab")).toHaveLength(0));
    const back = screen.getByRole("button", { name: "기억 화면으로 돌아가기" });
    expect(screen.queryByTestId("open-connections-map")).toBeNull();

    await userEvent.click(back);
    await waitFor(() => expect(screen.queryAllByRole("tab")).toHaveLength(2));
    expect(screen.getByTestId("open-connections-map")).toBeTruthy();
  });

  it("switches between the search and memory tabs by clicking them", async () => {
    render();
    await waitFor(() => expect(screen.getAllByRole("tab")).toHaveLength(2));
    expect(screen.getByPlaceholderText("사람, 문서, 주제로 검색")).toBeTruthy();
    await userEvent.click(screen.getByRole("tab", { name: "내 자료" }));
    await waitFor(() => expect(screen.queryByPlaceholderText("사람, 문서, 주제로 검색")).toBeNull());
    await userEvent.click(screen.getByRole("tab", { name: "찾기" }));
    await waitFor(() => expect(screen.getByPlaceholderText("사람, 문서, 주제로 검색")).toBeTruthy());
  });

  it("a direct link to the map opens the map, not the search tab", async () => {
    renderPage(<BrainPage initialTab="graph" />, {
      api: {
        graph: ok(GRAPH),
        graphCoverage: ok({ total_nodes: 2, nodes_with_provenance: 1 }),
      },
    });
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "기억 화면으로 돌아가기" })).toBeTruthy();
      expect(screen.getByTestId("brain-cytoscape")).toBeTruthy();
      expect(screen.getByText("릴리스 절차")).toBeTruthy();
    });
  });

  it("a link to any of the memory-tab aliases opens the memory tab, not search", async () => {
    render({}, {}, { initialTab: "memory" });
    await waitFor(() => expect(screen.getByRole("tab", { name: "내 자료" }).getAttribute("aria-selected")).toBe("true"));
  });

  it("toggles 2-hop multi-hop synthesis mode in hybrid search and renders reasoning chains", async () => {
    const hybridSearchGraphRag = vi.fn().mockResolvedValue(
      ok({
        matches: [{ id: "m1", title: "October Postmortem", type: "Document" }],
        reasoning_paths: [
          {
            seed_id: "s1",
            seed_title: "WAL RFC",
            bridge_id: "b1",
            bridge_title: "Reader Lock",
            target_id: "m1",
            target_title: "October Postmortem",
            hops: 2,
            relations: ["MENTIONS", "RELATES_TO"],
            path_score: 0.77,
            is_contradiction: false,
          },
        ],
        synthesis: { direct_hits: 1, multi_hop_hits: 1, bridge_concepts: [], contradictions: [] },
      }),
    );
    render({ hybridSearchGraphRag });
    const toggle = await screen.findByTestId("toggle-multihop-search");
    expect(toggle.getAttribute("aria-pressed")).toBe("false");
    await userEvent.click(toggle);
    expect(toggle.getAttribute("aria-pressed")).toBe("true");

    const input = screen.getByPlaceholderText("사람, 문서, 주제로 검색");
    await userEvent.type(input, "WAL checkpointer");
    await userEvent.click(screen.getByRole("button", { name: "기억에서 찾기" }));

    await waitFor(() => expect(hybridSearchGraphRag).toHaveBeenCalledWith("WAL checkpointer", 12, undefined));
    expect(await screen.findByTestId("multihop-synthesis-panel")).toBeTruthy();
    expect(screen.getByText("Reader Lock")).toBeTruthy();

    await userEvent.click(screen.getByRole("button", { name: "Reader Lock 노드로 이동" }));
    expect(window.location.hash).toBe("#/knowledge-graph");
  });
});

describe("HybridSearch newest-first toggle", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    useAppStore.setState({ mode: "advanced", language: "ko" } as never);
  });

  it("asks the backend for sort_by=newest only while the toggle is on", async () => {
    const hybridSearch = vi.fn().mockResolvedValue(ok({ matches: [{ id: "n1", title: "Refund policy", type: "Document" }] }));
    render({ hybridSearch });
    const toggle = await screen.findByTestId("toggle-newest-search");
    const input = screen.getByPlaceholderText("사람, 문서, 주제로 검색");
    await userEvent.type(input, "refund");
    await userEvent.click(screen.getByRole("button", { name: "기억에서 찾기" }));
    await waitFor(() => expect(hybridSearch).toHaveBeenLastCalledWith("refund", undefined, undefined));
    await userEvent.click(toggle);
    expect(toggle.getAttribute("aria-pressed")).toBe("true");
    await userEvent.click(screen.getByRole("button", { name: "기억에서 찾기" }));
    await waitFor(() => expect(hybridSearch).toHaveBeenLastCalledWith("refund", undefined, "newest"));
  });
});

describe("standalone status panels (kept for future advanced views)", () => {
  beforeEach(() => {
    useAppStore.setState({ mode: "advanced", language: "ko" } as never);
  });

  describe("viewLabel", () => {
    it("falls back to the generic brain title for a view outside the known tabs", () => {
      expect(viewLabel("ko", "not-a-real-view" as never)).toBe("Lattice Brain");
    });
  });
});

describe("SourceProvenanceList", () => {
  beforeEach(() => {
    useAppStore.setState({ mode: "advanced", language: "ko" } as never);
    Object.assign(navigator, { clipboard: { writeText: vi.fn() } });
  });

  it("classifies a chat source with a recorded time and an inspectable path, and copies it", async () => {
    renderComponent(<SourceProvenanceList items={[
      { id: "s1", title: "대화 요약", source_type: "conversation", path: "conv/123", created_at: "2026-07-01T00:00:00" },
    ]} />);
    expect(screen.getByText(/^대화 ·/)).toBeTruthy();
    expect(screen.getByText("conv/123")).toBeTruthy();
    expect(screen.getByText("확인 가능")).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: "출처 복사" }));
    await waitFor(() => expect(navigator.clipboard.writeText).toHaveBeenCalledWith("conv/123"));
  });

  it("classifies a document source via its metadata and reports no recorded time", () => {
    renderComponent(<SourceProvenanceList items={[
      { id: "s2", filename: "notes.pdf", metadata: { source_type: "document" } },
    ]} />);
    expect(screen.getByText(/^문서 ·/)).toBeTruthy();
    expect(screen.getByText(/생성 시간이 기록되지 않음/)).toBeTruthy();
    expect(screen.getByText("출처 이력 없음")).toBeTruthy();
    expect(screen.getByText(/이전 기억에는 출처 경로나 대화가 기록되지 않았습니다/)).toBeTruthy();
  });

  it("classifies import and manual sources, and names a known memory tier by id", () => {
    renderComponent(<SourceProvenanceList items={[
      { id: "workspace", source_type: "archive_restore" },
      { id: "s4", kind: "manual_note", updated_at: "2026-07-02T00:00:00" },
    ]} />);
    expect(screen.getByText("작업공간 기억")).toBeTruthy();
    expect(screen.getByText(/^가져오기 ·/)).toBeTruthy();
    expect(screen.getByText(/^수동 입력 ·/)).toBeTruthy();
  });

  it("falls back to an unknown type and a numbered title when nothing else identifies the row", () => {
    renderComponent(<SourceProvenanceList items={[{}]} />);
    expect(screen.getByText(/^알 수 없는 출처 ·/)).toBeTruthy();
    expect(screen.getByText("출처 1")).toBeTruthy();
  });

  it("shows an empty state with no items", () => {
    renderComponent(<SourceProvenanceList items={[]} />);
    expect(screen.getByText("아직 출처가 없습니다")).toBeTruthy();
  });
});

describe("search tab: HybridSearch", () => {
  it("searches on Enter, ignoring an Enter that is part of IME composition", async () => {
    render({ hybridSearch: () => Promise.resolve(ok({ matches: [{ title: "결과 A", type: "Document" }] })) });
    const input = await screen.findByPlaceholderText("사람, 문서, 주제로 검색");

    fireEvent.keyDown(input, { key: "Enter", isComposing: true });
    expect(latticeApi.hybridSearch).not.toHaveBeenCalled();

    await userEvent.type(input, "결과");
    fireEvent.keyDown(input, { key: "Enter" });
    await waitFor(() => expect(latticeApi.hybridSearch).toHaveBeenCalledWith("결과", undefined, undefined));
    await waitFor(() => expect(screen.getByText("결과 A")).toBeTruthy());
  });

  it("searches from the button and copes with a response that has no `matches` field", async () => {
    render({ hybridSearch: () => Promise.resolve(ok({ mode: "hybrid" })) });
    const input = await screen.findByPlaceholderText("사람, 문서, 주제로 검색");
    const button = screen.getByRole("button", { name: "기억에서 찾기" });
    expect((button as HTMLButtonElement).disabled).toBe(true);

    await userEvent.type(input, "아무거나");
    expect((button as HTMLButtonElement).disabled).toBe(false);
    await userEvent.click(button);
    await waitFor(() => expect(latticeApi.hybridSearch).toHaveBeenCalled());
    expect(document.body.textContent).not.toMatch(/undefined/);
  });
});

describe("memory tab: UnifiedMemoryPanel", () => {
  it("shows the basic-mode summary and marks export status unknown until portability answers", () => {
    render({ graphPortability: fail("unavailable", {}) }, { mode: "basic" }, { initialTab: "memory" });
    expect(screen.getByText("확인 중")).toBeTruthy();
    expect(screen.getByText("내 컴퓨터")).toBeTruthy();
  });

  it("shows the advanced-mode summary with format, storage and export-ready copy", async () => {
    render({
      graphPortability: ok({ graph_schema_version: "3", storage: { engine: "sqlite" }, stats: { nodes: 10 } }),
      memoryManager: ok({ usage: { sources: 4, total_items: 12, total_bytes: 2048 } }),
    }, { mode: "advanced" }, { initialTab: "memory" });
    await waitFor(() => expect(screen.getByText("sqlite")).toBeTruthy());
    expect(screen.getByText("3")).toBeTruthy();
    expect(screen.getByText("4")).toBeTruthy();
  });

  it("falls back to a dash when the format and storage engine are both unreported", async () => {
    render({ graphPortability: ok({}) }, { mode: "advanced" }, { initialTab: "memory" });
    await waitFor(() => expect(screen.getAllByText("–").length).toBeGreaterThan(0));
  });

  it("collapses the recall section and expands sources in its place", async () => {
    render({}, {}, { initialTab: "memory" });
    await waitFor(() => expect(screen.getByPlaceholderText("다시 찾을 기억의 내용...")).toBeTruthy());
    await userEvent.click(screen.getByText("출처와 이력"));
    await waitFor(() => expect(screen.queryByPlaceholderText("다시 찾을 기억의 내용...")).toBeNull());
    await userEvent.click(screen.getByText("기억 다시 찾기"));
    await waitFor(() => expect(screen.getByPlaceholderText("다시 찾을 기억의 내용...")).toBeTruthy());
    await userEvent.click(screen.getByText("기억 다시 찾기"));
    await waitFor(() => expect(screen.queryByPlaceholderText("다시 찾을 기억의 내용...")).toBeNull());
  });

  it("recalls a query on Enter or by button, and shows the compact/rebuild actions in advanced mode", async () => {
    render({
      memoryRecall: () => Promise.resolve(ok({ matches: [{ title: "메모" }] })),
      memoryCompact: () => Promise.resolve(ok({})),
      memoryRebuild: () => Promise.resolve(ok({})),
    }, { mode: "advanced" }, { initialTab: "memory" });
    const input = await screen.findByPlaceholderText("다시 찾을 기억의 내용...");
    await userEvent.type(input, "회의록{enter}");
    await waitFor(() => expect(latticeApi.memoryRecall).toHaveBeenCalledWith("회의록", 25));
    await waitFor(() => expect(screen.getByText("기억 검색 완료")).toBeTruthy());

    await userEvent.click(screen.getByRole("button", { name: "압축 정리" }));
    await waitFor(() => expect(latticeApi.memoryCompact).toHaveBeenCalled());
    await userEvent.click(screen.getByRole("button", { name: "벡터 다시 만들기" }));
    await waitFor(() => expect(latticeApi.memoryRebuild).toHaveBeenCalled());

    await userEvent.clear(input);
    await userEvent.type(input, "다른 질문");
    await userEvent.click(screen.getByRole("button", { name: "찾기" }));
    await waitFor(() => expect(latticeApi.memoryRecall).toHaveBeenLastCalledWith("다른 질문", 25));
  });

  it("hides the compact/rebuild actions in basic mode and does not recall on a bare Enter", async () => {
    render({}, { mode: "basic" }, { initialTab: "memory" });
    const input = await screen.findByPlaceholderText("다시 찾을 기억의 내용...");
    expect(screen.queryByRole("button", { name: "압축 정리" })).toBeNull();
    await userEvent.type(input, "{enter}");
    expect(latticeApi.memoryRecall).not.toHaveBeenCalled();
  });

  it("shows a loading panel while provenance is in flight, then the source list", async () => {
    let resolveProvenance!: (value: unknown) => void;
    render({
      graphProvenance: () => new Promise((resolve) => { resolveProvenance = resolve; }),
    }, {}, { initialTab: "memory" });
    await userEvent.click(await screen.findByText("출처와 이력"));
    await screen.findByText("출처 불러오는 중");
    resolveProvenance(ok({ items: [{ id: "s1", title: "회의 노트" }] }));
    await waitFor(() => expect(screen.getByText("회의 노트")).toBeTruthy());
  });

  it("reads a bare provenance record directly when it carries no `items` key", async () => {
    render({
      graphProvenance: () => Promise.resolve(ok({ id: "solo", title: "단일 출처" })),
    }, {}, { initialTab: "memory" });
    await userEvent.click(await screen.findByText("출처와 이력"));
    await waitFor(() => expect(screen.getByText("아직 출처가 없습니다")).toBeTruthy());
  });

  it("copes with a provenance envelope whose `.data` is not a record at all", async () => {
    render({
      graphProvenance: () => Promise.resolve({ ok: true, status: 200, source: "live", data: "not-a-record" }),
    }, {}, { initialTab: "memory" });
    await userEvent.click(await screen.findByText("출처와 이력"));
    await waitFor(() => expect(screen.getByText("아직 출처가 없습니다")).toBeTruthy());
  });

  it("exports, backs up, and previews a valid import artifact", async () => {
    render({
      graphExport: () => Promise.resolve(ok({})),
      graphBackup: () => Promise.resolve(ok({})),
      graphImport: () => Promise.resolve(ok({ would_add: 3 })),
    }, {}, { initialTab: "memory" });
    await userEvent.click(await screen.findByText("내보내기와 백업"));
    await userEvent.click(screen.getByRole("button", { name: "Brain 내보내기" }));
    await waitFor(() => expect(latticeApi.graphExport).toHaveBeenCalled());
    await userEvent.click(screen.getByRole("button", { name: "백업 만들기" }));
    await waitFor(() => expect(latticeApi.graphBackup).toHaveBeenCalled());

    const importBox = screen.getByPlaceholderText("가져오기 미리보기를 위해 내보낸 Brain 데이터를 붙여넣으세요");
    await userEvent.type(importBox, '{{"nodes":[[]}');
    await userEvent.click(screen.getByRole("button", { name: "가져오기 미리보기" }));
    await waitFor(() => expect(latticeApi.graphImport).toHaveBeenCalledWith({ nodes: [] }, true));
    await waitFor(() => expect(screen.getByText("가져오기 미리보기 완료")).toBeTruthy());
  });

  it("turns invalid import JSON into a reported error instead of throwing", async () => {
    render({}, {}, { initialTab: "memory" });
    await userEvent.click(await screen.findByText("내보내기와 백업"));
    const importBox = screen.getByPlaceholderText("가져오기 미리보기를 위해 내보낸 Brain 데이터를 붙여넣으세요");
    await userEvent.type(importBox, "이건 JSON이 아닙니다");
    await userEvent.click(screen.getByRole("button", { name: "가져오기 미리보기" }));
    await waitFor(() => expect(screen.getByText("요청을 처리하지 못했어요")).toBeTruthy());
  });

  it("stringifies a non-Error rejection from the import call itself", async () => {
    render({ graphImport: () => Promise.reject("plain string failure") }, {}, { initialTab: "memory" });
    await userEvent.click(await screen.findByText("내보내기와 백업"));
    const importBox = screen.getByPlaceholderText("가져오기 미리보기를 위해 내보낸 Brain 데이터를 붙여넣으세요");
    await userEvent.type(importBox, '{{"nodes":[[]}');
    await userEvent.click(screen.getByRole("button", { name: "가져오기 미리보기" }));
    await waitFor(() => expect(screen.getByText("요청을 처리하지 못했어요")).toBeTruthy());
  });
});

describe("graph subview: DigitalBrainExplorer interactions", () => {
  beforeEach(() => {
    Object.assign(navigator, { clipboard: { writeText: vi.fn() } });
  });

  function renderGraph(overrides: Record<string, unknown> = {}, options: { mode?: "basic" | "advanced" } = {}) {
    return render({ graph: ok(RICH_GRAPH), graphCoverage: ok({ total_nodes: 2, nodes_with_provenance: 1 }), ...overrides }, options, { initialTab: "graph" });
  }

  it("shows the connected-empty state when the graph has no nodes at all", async () => {
    renderGraph({ graph: ok({ nodes: [], edges: [] }) });
    await waitFor(() => expect(screen.getByText("아직 연결된 기억이 없어요")).toBeTruthy());
  });

  it("reports a failed graph fetch instead of drawing an empty canvas", async () => {
    renderGraph({ graph: fail("server unavailable", { nodes: [], edges: [] }) });
    await waitFor(() => expect(screen.getByText("요청을 처리하지 못했어요")).toBeTruthy());
    expect(screen.queryByTestId("brain-cytoscape")).toBeNull();
    expect(document.body.textContent).not.toMatch(/undefined|NaN/);
  });

  it("falls back to the envelope itself when its `.data` is nullish", async () => {
    renderGraph({ graph: () => Promise.resolve({ ok: true, status: 200, source: "live", data: null }) });
    await waitFor(() => expect(screen.getByText("아직 연결된 기억이 없어요")).toBeTruthy());
  });

  it("shows a loading panel while the graph query is in flight", async () => {
    let resolveGraph!: (value: unknown) => void;
    renderGraph({ graph: () => new Promise((resolve) => { resolveGraph = resolve; }) });
    await screen.findByText("불러오는 중…");
    expect(screen.queryByTestId("brain-cytoscape")).toBeNull();
    resolveGraph(ok(RICH_GRAPH));
    await screen.findByTestId("brain-cytoscape");
  });

  it("names a node whose type has no dedicated copy by titleizing it", async () => {
    renderGraph();
    await waitFor(() => expect(screen.getByText("이상한 항목")).toBeTruthy());
    expect(screen.getByText("MysteryType")).toBeTruthy();
  });

  it("copes with a backend search result that has no `matches` field", async () => {
    renderGraph({ hybridSearch: () => Promise.resolve(ok({ mode: "hybrid" })) });
    const search = await screen.findByRole("textbox", { name: "지식 그래프 검색" });
    await userEvent.type(search, "배포");
    await userEvent.click(await screen.findByRole("button", { name: "전체 기억에서 찾기" }));
    await waitFor(() => expect(latticeApi.hybridSearch).toHaveBeenCalled());
    expect(document.body.textContent).not.toMatch(/undefined/);
  });

  it("filters by the search box and offers a backend search over all memories", async () => {
    renderGraph({ hybridSearch: () => Promise.resolve(ok({ matches: [{ title: "검색 결과 1", type: "Document" }] })) });
    const search = await screen.findByRole("textbox", { name: "지식 그래프 검색" });
    await userEvent.type(search, "배포");
    await waitFor(() => expect(screen.getByText("배포")).toBeTruthy());
    expect(screen.queryByText("릴리스 절차")).toBeNull();

    const searchAll = screen.getByRole("button", { name: "전체 기억에서 찾기" });
    await userEvent.click(searchAll);
    await waitFor(() => expect(latticeApi.hybridSearch).toHaveBeenCalledWith("배포"));
    await waitFor(() => expect(screen.getByText("검색 결과 1")).toBeTruthy());
  });

  it("filters by group and by label mode", async () => {
    renderGraph();
    await screen.findByTestId("brain-cytoscape");
    const [groupSelect, labelSelect] = screen.getAllByRole("combobox");
    await userEvent.selectOptions(groupSelect!, "지식");
    await userEvent.selectOptions(labelSelect!, "모든 이름");
    await userEvent.selectOptions(labelSelect!, "이름 숨기기");
    expect(screen.getByTestId("brain-cytoscape")).toBeTruthy();
  });

  it("raises the importance floor and hides the low-importance node", async () => {
    renderGraph();
    await screen.findByText("릴리스 절차");
    const slider = screen.getByLabelText("최소 관계 중요도");
    fireEvent.change(slider, { target: { value: "0.5" } });
    await waitFor(() => expect(screen.getByText(/1개 숨김/)).toBeTruthy());
  });

  it("clamps the importance floor back to 10% in basic mode", async () => {
    renderGraph({}, { mode: "basic" });
    await screen.findByText("릴리스 절차");
    const slider = screen.getByLabelText("최소 관계 중요도");
    expect(screen.getByText("10%+")).toBeTruthy();
    fireEvent.change(slider, { target: { value: "0" } });
    await waitFor(() => expect(screen.getByText("10%+")).toBeTruthy());
  });

  it("clicks the fit button without error", async () => {
    renderGraph();
    await screen.findByTestId("brain-cytoscape");
    await userEvent.click(screen.getByRole("button", { name: "화면 맞춤" }));
    expect(screen.getByTestId("brain-cytoscape")).toBeTruthy();
  });

  it("selects a node with a summary and a source, and copies the source", async () => {
    renderGraph();
    await userEvent.click(await screen.findByText("릴리스 절차"));
    expect(screen.getByText("태그를 만들고 CI를 통과")).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: "출처 복사" }));
    await waitFor(() => expect(navigator.clipboard.writeText).toHaveBeenCalledWith("docs/RELEASE.md"));
    await userEvent.click(screen.getByRole("button", { name: "선택 해제" }));
    await waitFor(() => expect(screen.getByText("중요한 항목")).toBeTruthy());
  });

  it("selects a node with neither a summary nor a source", async () => {
    renderGraph();
    await userEvent.click(await screen.findByText("배포"));
    expect(screen.queryByText("출처 복사")).toBeNull();
    expect(screen.queryByText("태그를 만들고 CI를 통과")).toBeNull();
  });

  it("renders the selected-node card as a plain key/value list in basic mode", async () => {
    renderGraph({}, { mode: "basic" });
    await userEvent.click(await screen.findByText("릴리스 절차"));
    expect(screen.queryByText("출처 기록 없음")).toBeNull();
    expect(screen.getByText(/docs\/RELEASE\.md/)).toBeTruthy();
  });

  it("shows the no-source placeholder in the basic-mode key/value card", async () => {
    renderGraph({}, { mode: "basic" });
    await userEvent.click(await screen.findByText("제3의 개념"));
    expect(screen.getByText("출처 기록 없음")).toBeTruthy();
  });

  it("selects a collapsed group and expands it back", async () => {
    renderGraph();
    await screen.findByTestId("brain-cytoscape");
    await userEvent.click(screen.getByRole("button", { name: /^지식/ }));
    await waitFor(() => expect(screen.getByText("접힘")).toBeTruthy());
    await userEvent.click(screen.getByTestId("mock-select-knowledge-group"));
    await waitFor(() => expect(screen.getByText("접힌 묶음")).toBeTruthy());
    await userEvent.click(screen.getByRole("button", { name: "묶음 펼치기" }));
    await waitFor(() => expect(screen.queryByText("접힘")).toBeNull());
    expect(screen.getByText("접힌 묶음")).toBeTruthy();
  });

  it("shows the nothing-selected empty state for a group id that matches nothing", async () => {
    renderGraph();
    await screen.findByTestId("brain-cytoscape");
    await userEvent.click(screen.getByTestId("mock-select-missing-group"));
    await waitFor(() => expect(screen.getByText("아직 선택한 항목이 없어요")).toBeTruthy());
  });
});
