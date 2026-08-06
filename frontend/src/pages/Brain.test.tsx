import { fireEvent, render as renderComponent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { latticeApi } from "@/api/client";
import { fail, ok, renderPage } from "@/test/renderPage";
import { useAppStore } from "@/store/appStore";
import { BrainPage, GraphStatus, MemoryStatus, RetrievalStatus, SourceProvenanceList, viewLabel } from "./Brain";

// Cytoscape draws into a real 2D canvas context, which jsdom does not provide
// ("Could not create canvas of type 2d"). That is an environment gap, not the
// behaviour under test — these tests are about where the map sits in the
// hierarchy, not how it paints. The canvas itself is asserted for real by the
// Playwright suite, which loads `#/knowledge-graph` and requires
// `[data-testid='brain-cytoscape']` to be visible. Test-only select buttons
// stand in for the real cytoscape click/keyboard handlers so the page's own
// selection state (including the "collapsed group" id shape) stays reachable:
// one targets a group id that is actually present in the fixture graphs
// below, the other a group id that never is (nothing to find, either way).
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

// A second fixture with explicit importance and a source path, used by the
// explorer-interaction tests below: one node clears the default importance
// filter comfortably (and carries a source, for the copy-source button), the
// other sits low enough to be excluded once the importance floor is raised.
const RICH_GRAPH = {
  nodes: [
    { id: "n1", type: "Document", title: "릴리스 절차", summary: "태그를 만들고 CI를 통과", source: "docs/RELEASE.md", importance_norm: 0.9 },
    { id: "n2", type: "Concept", title: "배포", summary: "", importance_norm: 0.05 },
    // Clears the basic-mode importance floor (0.1) on its own, unlike n2, so
    // it is reachable there without touching the slider — and has neither a
    // summary nor a source, unlike n1.
    { id: "n3", type: "Concept", title: "제3의 개념", summary: "", importance_norm: 0.5 },
    // A type with no `ui.entity.*` copy at all, so `graphTypeLabel` falls all
    // the way through to `titleize(raw)`.
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

  /**
   * The connections map is a subview, not a peer of search.
   *
   * As a third tab it was one of the first three choices a newcomer met here: a
   * force-directed node cloud offered with exactly the weight of "search your
   * memory", which is what nearly everyone opens this screen to do. Reordering
   * the strip does not fix that — being *in* the strip is the problem, so these
   * tests hold the demotion rather than the tab order.
   */
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

    // Entering the subview retires the tablist rather than leaving a strip with
    // nothing selected, and the way out is explicit.
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
    // `normalizeBrainView` treats "relationships"/"provenance"/"sources"/
    // "portability"/"care" as synonyms of "memory" — this drives the whole
    // OR chain through its first (short-circuiting) branch.
    render({}, {}, { initialTab: "memory" });
    await waitFor(() => expect(screen.getByRole("tab", { name: "내 자료" }).getAttribute("aria-selected")).toBe("true"));
  });
});

describe("standalone status panels (kept for future advanced views)", () => {
  beforeEach(() => {
    useAppStore.setState({ mode: "advanced", language: "ko" } as never);
  });

  describe("GraphStatus", () => {
    it("prefers explicit totals and shows a structured view outside basic mode", () => {
      renderComponent(<GraphStatus data={{ nodes: { Document: 3 }, edges: { mentions: 1 }, total_nodes: 42, total_edges: 9 }} />);
      expect(screen.getByText("기억 종류")).toBeTruthy();
      expect(screen.getByText("연결 종류")).toBeTruthy();
    });

    it("reduces raw type counts into totals, including a zero-count type, and shows badges in basic mode", () => {
      useAppStore.setState({ mode: "basic", language: "ko" } as never);
      renderComponent(<GraphStatus data={{ nodes: { Document: 3, Concept: 2, Empty: 0 }, edges: { mentions: 4, Blank: 0 } }} />);
      expect(screen.getByText("Document")).toBeTruthy();
      expect(screen.getByText("Mentions")).toBeTruthy();
    });

    it("copes with entirely missing node/edge maps", () => {
      renderComponent(<GraphStatus data={{}} />);
      expect(screen.getByText("기억 종류")).toBeTruthy();
    });
  });

  describe("RetrievalStatus", () => {
    it("summarises each pipeline's state and extra fields", () => {
      renderComponent(<RetrievalStatus data={{
        pipelines: {
          vector: { state: "ready", model: "bge-small", dims: 384 },
          keyword: "degraded",
          parser: { status: "partial" },
          blank: {},
        },
      }} />);
      expect(screen.getByText("Vector")).toBeTruthy();
      // The row's own state is looked up as an entity type badge ("ready" ->
      // "준비됨"); the extra fields print verbatim in the description line.
      expect(screen.getByText("Model: bge-small · Dims: 384")).toBeTruthy();
      // A non-record pipeline value carries no state/status of its own, so the
      // row falls back to the generic "reported" copy for both fields.
      expect(screen.getByText("Keyword")).toBeTruthy();
      expect(screen.getByText("degraded")).toBeTruthy();
      // `status` (no `state`) and neither at all both fall back correctly.
      expect(screen.getByText("Parser")).toBeTruthy();
      expect(screen.getByText("Blank")).toBeTruthy();
    });

    it("falls back to a structured view when there are no pipelines to list", () => {
      renderComponent(<RetrievalStatus data={{ status: "unknown" }} />);
      expect(screen.getByText("상태")).toBeTruthy();
      expect(screen.getByText("unknown")).toBeTruthy();
    });
  });

  describe("MemoryStatus", () => {
    it("prefers explicit usage counts and lists sources", () => {
      renderComponent(<MemoryStatus data={{
        usage: { sources: 3, total_items: 90, total_bytes: 512 },
        health: "good",
        sources: [{ id: "src-1", title: "Workspace" }],
      }} />);
      expect(screen.getByText("good")).toBeTruthy();
      expect(screen.getByText("Workspace")).toBeTruthy();
    });

    it("derives counts from the raw source list when usage is absent", () => {
      // A mix of a real count, a record with none, and a non-record entry —
      // the reduce callback itself only runs at all when `data.sources` (not
      // `tiers`) is a non-empty array.
      renderComponent(<MemoryStatus data={{ sources: [{ count: 5 }, {}, "not-a-record"] }} />);
      expect(screen.getByText("보고됨")).toBeTruthy();
      expect(screen.getByText("5")).toBeTruthy();
    });

    it("reads the tiers list when there is no `sources` field at all", () => {
      renderComponent(<MemoryStatus data={{ tiers: [{ count: 2 }, { count: 3 }] }} />);
      expect(screen.getByText("보고됨")).toBeTruthy();
    });
  });

  describe("viewLabel", () => {
    it("falls back to the generic brain title for a view outside the known tabs", () => {
      // BrainView is a closed union in normal use; this direct call is the
      // only way to reach the fallback the function still guards against.
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
    // The type sits beside "· <timestamp>" as sibling text within one line,
    // so only a substring match finds it.
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

    // A composing Enter (mid-IME-conversion) must not fire a search.
    fireEvent.keyDown(input, { key: "Enter", isComposing: true });
    expect(latticeApi.hybridSearch).not.toHaveBeenCalled();

    await userEvent.type(input, "결과");
    fireEvent.keyDown(input, { key: "Enter" });
    await waitFor(() => expect(latticeApi.hybridSearch).toHaveBeenCalledWith("결과"));
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
    // `result.matches || result` falls back to the whole (matches-less)
    // envelope body; `EntityList` copes with that rather than crashing.
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
      // `stats` is unused by the rendered summary today but is still parsed
      // (`isRecord(portData.stats) ? ... : {}`) on every render; supplying it
      // as a real record exercises that parse's truthy branch. Same for
      // `memoryManager`'s `usage` record below.
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
    // Re-opening recall collapses sources in turn.
    await userEvent.click(screen.getByText("기억 다시 찾기"));
    await waitFor(() => expect(screen.getByPlaceholderText("다시 찾을 기억의 내용...")).toBeTruthy());
    // Clicking the header of the section that is already open collapses it
    // to nothing rather than switching to another section.
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

    // The "찾기" button itself, not just Enter, must trigger a recall too.
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
    // A bare record is not an array, so `asArray` yields nothing to render —
    // the empty state, not a crash, is the correct outcome here.
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
    // Both `{` and `[` start a special-key token for user-event's keyboard
    // parser; only those opening characters need escaping (doubling) to be
    // typed literally.
    await userEvent.type(importBox, '{{"nodes":[[]}');
    await userEvent.click(screen.getByRole("button", { name: "가져오기 미리보기" }));
    await waitFor(() => expect(latticeApi.graphImport).toHaveBeenCalledWith({ nodes: [] }, true));
    await waitFor(() => expect(screen.getByText("가져오기 미리보기 완료")).toBeTruthy());
    // A successful preview clears nothing (only recall/import text fields
    // clear themselves elsewhere) but does invalidate portability — the
    // panel keeps showing the artifact for further edits.
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
    // JSON.parse always throws a genuine Error; a rejection thrown by the API
    // call is the only way to reach the `String(err)` side of the catch.
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
    // The page header's own text is present from the very first synchronous
    // render, before the query has settled — waiting on it would pass
    // whether or not the query ever resolved into its "ok: false" state, so
    // wait on `OperationResult`'s own failure copy instead.
    await waitFor(() => expect(screen.getByText("요청을 처리하지 못했어요")).toBeTruthy());
    expect(screen.queryByTestId("brain-cytoscape")).toBeNull();
    expect(document.body.textContent).not.toMatch(/undefined|NaN/);
  });

  it("falls back to the envelope itself when its `.data` is nullish", async () => {
    // `graph.data?.data ?? graph.data`: a response whose own `.data` is
    // missing still has to resolve to *something* rather than `undefined`.
    renderGraph({ graph: () => Promise.resolve({ ok: true, status: 200, source: "live", data: null }) });
    await waitFor(() => expect(screen.getByText("아직 연결된 기억이 없어요")).toBeTruthy());
  });

  it("shows a loading panel while the graph query is in flight", async () => {
    // "연결 지도" (the panel's own title) also names the page kicker and the
    // subview nav bar, so the loading spinner's own copy is the unambiguous
    // signal that this is `LoadingPanel`, not just the subview shell.
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
    // The other node no longer matches the query and drops out of view.
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
    await userEvent.selectOptions(groupSelect, "지식");
    await userEvent.selectOptions(labelSelect, "모든 이름");
    await userEvent.selectOptions(labelSelect, "이름 숨기기");
    // No crash across every group/label combination is the contract here;
    // the underlying filtering is graphExplorer's, already covered there.
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
    // Dragging below the basic-mode floor snaps straight back to it.
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
    // Clearing the selection returns to the "important items" panel.
    await userEvent.click(screen.getByRole("button", { name: "선택 해제" }));
    await waitFor(() => expect(screen.getByText("중요한 항목")).toBeTruthy());
  });

  it("selects a node with neither a summary nor a source", async () => {
    // Advanced mode's importance floor starts at 0, so the low-importance
    // node is visible in the "important items" aside without touching the
    // slider (basic mode's floor of 10% would hide it outright).
    renderGraph();
    await userEvent.click(await screen.findByText("배포"));
    expect(screen.queryByText("출처 복사")).toBeNull();
    // No summary paragraph is rendered either — only the label/group/importance
    // badges and the key/value block below them.
    expect(screen.queryByText("태그를 만들고 CI를 통과")).toBeNull();
  });

  it("renders the selected-node card as a plain key/value list in basic mode", async () => {
    renderGraph({}, { mode: "basic" });
    await userEvent.click(await screen.findByText("릴리스 절차"));
    // KeyValueList (basic mode), not StructuredView, is what's rendering here:
    // the real source path shows up rather than the "no source" placeholder.
    expect(screen.queryByText("출처 기록 없음")).toBeNull();
    expect(screen.getByText(/docs\/RELEASE\.md/)).toBeTruthy();
  });

  it("shows the no-source placeholder in the basic-mode key/value card", async () => {
    // n3 clears the basic-mode importance floor (0.1) on its own and has no
    // source of its own, unlike n1 above.
    renderGraph({}, { mode: "basic" });
    await userEvent.click(await screen.findByText("제3의 개념"));
    expect(screen.getByText("출처 기록 없음")).toBeTruthy();
  });

  it("selects a collapsed group and expands it back", async () => {
    renderGraph();
    await screen.findByTestId("brain-cytoscape");
    // Collapse the "knowledge" group pill, then select the resulting cluster.
    await userEvent.click(screen.getByRole("button", { name: /^지식/ }));
    await waitFor(() => expect(screen.getByText("접힘")).toBeTruthy());
    await userEvent.click(screen.getByTestId("mock-select-knowledge-group"));
    await waitFor(() => expect(screen.getByText("접힌 묶음")).toBeTruthy());
    // Expanding clears the pill's own collapsed badge; the aside card's
    // static "you selected a group" label is unaffected by the toggle since
    // the selection itself is not cleared.
    await userEvent.click(screen.getByRole("button", { name: "묶음 펼치기" }));
    await waitFor(() => expect(screen.queryByText("접힘")).toBeNull());
    expect(screen.getByText("접힌 묶음")).toBeTruthy();
  });

  it("shows the nothing-selected empty state for a group id that matches nothing", async () => {
    renderGraph();
    await screen.findByTestId("brain-cytoscape");
    await userEvent.click(screen.getByTestId("mock-select-missing-group"));
    // "people" has no members in this fixture, so it never made it into
    // `parsed.groups` — the id resolves to neither a node nor a group.
    await waitFor(() => expect(screen.getByText("아직 선택한 항목이 없어요")).toBeTruthy());
  });
});
