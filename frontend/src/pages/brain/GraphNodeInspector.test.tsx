import { fireEvent, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import "@/i18n/brain";
import { DigitalBrainExplorer } from "./DigitalBrainExplorer";
import { GraphNodeInspector } from "./GraphNodeInspector";
import { parseGraph } from "./graphExplorer";
import { useAppStore } from "@/store/appStore";
import { fail, ok, renderPage } from "@/test/renderPage";

vi.mock("./CytoscapeGraph", () => ({
  CytoscapeGraph: ({ onSelect }: { onSelect: (id: string | null) => void }) => (
    <div data-testid="mock-cy-canvas">
      <button type="button" onClick={() => onSelect("n1")}>select-n1</button>
    </div>
  ),
}));

const SAMPLE_GRAPH = {
  nodes: [
    { id: "n1", type: "Decision", title: "Zero-Copy IPC", summary: "Shared ring buffer", source: "docs/ipc.md", importance: 0.9 },
    { id: "n2", type: "Note", title: "Legacy Socket Pipe", importance: 0.7 },
    { id: "n3", type: "Concept", title: "Memory Arena", importance: 0.6 },
  ],
  edges: [
    { id: "e1", source: "n1", target: "n2", type: "SUPERSEDES", weight: 0.95 },
    { id: "e2", source: "n1", target: "n3", type: "DEPENDS_ON", weight: 0.8 },
  ],
};

describe("GraphNodeInspector & DigitalBrainExplorer ergonomics", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    window.location.hash = "";
    Object.assign(navigator, { clipboard: { writeText: vi.fn() } });
    useAppStore.setState({
      mode: "basic",
      language: "ko",
      focusedGraphNodeId: null,
      pendingChatPrompt: null,
    });
  });

  it("loads neighborhood, provenance, source modal, and Ask-Brain prompt in GraphNodeInspector", async () => {
    const parsed = parseGraph(SAMPLE_GRAPH, "ko");
    const onSelectNode = vi.fn();

    renderPage(
      <GraphNodeInspector
        selected={parsed.nodes[0]!}
        groups={parsed.groups}
        mode="basic"
        language="ko"
        onSelectNode={onSelectNode}
      />,
      {
        api: {
          graphNodeInspect: ok({
            node: { id: "n1", confidence: 0.92 },
            neighborhood: {
              nodes: [
                { id: "n2", title: "Legacy Socket Pipe", type: "Note" },
                { id: "n3", title: "" },
                {},
              ],
              edges: [
                { from: "n1", to: "n2", type: "CONTRADICTS", weight: 0.9 },
                { from: "n3", to: "n1", type: "", weight: Number.NaN },
                { from: "n1", to: "unlisted-peer", type: "MENTIONS", weight: 0.95 },
                { from: "n1", to: "n2", type: "DUPLICATE" },
                { from: "n1", to: "n1", type: "SELF" },
                { from: "other", to: "another", type: "UNRELATED" },
                { from: "n1", to: "" },
                {},
              ],
            },
            reasoning: {
              synthesis: { direct_hits: 2, multi_hop_hits: 1, bridge_concepts: [], contradictions: [] },
              reasoning_paths: [
                {
                  seed_id: "n1",
                  seed_title: "Zero-Copy IPC",
                  bridge_id: "n3",
                  bridge_title: "Memory Arena",
                  target_id: "n2",
                  target_title: "Legacy Socket Pipe",
                  hops: 2,
                  relations: ["DEPENDS_ON", "CONTRADICTS"],
                  path_score: 0.88,
                  is_contradiction: true,
                },
              ],
            },
          }),
          graphNode: ok({
            node: { id: "n1", title: "Zero-Copy IPC", type: "Decision", summary: "Full stored source text" },
          }),
        },
      },
    );

    expect(await screen.findByText("신뢰도 92%")).toBeTruthy();
    expect(screen.getByTestId("inspector-neighbor-n2")).toBeTruthy();
    expect(screen.getByTestId("inspector-neighbor-n3")).toBeTruthy();

    await userEvent.click(screen.getByTestId("inspector-neighbor-n2"));
    expect(onSelectNode).toHaveBeenCalledWith("n2");

    await userEvent.click(screen.getByRole("button", { name: "Memory Arena 노드로 이동" }));
    expect(onSelectNode).toHaveBeenCalledWith("n3");

    await userEvent.click(screen.getByRole("button", { name: "출처 복사" }));
    expect(navigator.clipboard.writeText).toHaveBeenCalledWith("docs/ipc.md");

    await userEvent.click(screen.getByTestId("inspector-open-source"));
    expect(await screen.findByTestId("source-chunk-modal")).toBeTruthy();
    expect(await screen.findByText("Full stored source text")).toBeTruthy();
    await userEvent.click(screen.getByTestId("source-modal-open-graph"));
    expect(useAppStore.getState().focusedGraphNodeId).toBe("n1");
    expect(window.location.hash).toBe("#/knowledge-graph");

    await userEvent.click(screen.getByTestId("inspector-open-source"));
    await screen.findByTestId("source-chunk-modal");
    await userEvent.click(screen.getByRole("button", { name: "원문 닫기" }));
    expect(screen.queryByTestId("source-chunk-modal")).toBeNull();

    await userEvent.click(screen.getByTestId("inspector-ask-brain"));
    expect(useAppStore.getState().pendingChatPrompt).toContain("Zero-Copy IPC");
    expect(window.location.hash).toBe("#/brain");
  });

  it("handles advanced mode, fallback importance confidence, missing source/summary, and empty/failed inspect payload", async () => {
    const parsed = parseGraph(
      { nodes: [{ id: "solo", type: "Note", title: "Solo Node", importance: 0.55 }], edges: [] },
      "en",
    );
    renderPage(
      <GraphNodeInspector
        selected={parsed.nodes[0]!}
        groups={parsed.groups}
        mode="advanced"
        language="en"
        onSelectNode={() => {}}
      />,
      { api: { graphNodeInspect: fail("offline", 503) } },
    );

    expect(await screen.findByText("55% confidence")).toBeTruthy();
    expect(await screen.findByText("No directly connected neighbor nodes.")).toBeTruthy();
  });

  it("honors focusedGraphNodeId, conflict-only toggle, and / + Escape keyboard shortcuts in DigitalBrainExplorer", async () => {
    useAppStore.setState({ focusedGraphNodeId: "missing-node" });
    const first = renderPage(<DigitalBrainExplorer data={SAMPLE_GRAPH} />, {
      api: { graphNodeInspect: ok({ node: {}, neighborhood: { nodes: [], edges: [] } }) },
    });
    await waitFor(() => expect(useAppStore.getState().focusedGraphNodeId).toBeNull());
    first.unmount();

    useAppStore.setState({ focusedGraphNodeId: "n1" });
    renderPage(<DigitalBrainExplorer data={SAMPLE_GRAPH} />, {
      api: { graphNodeInspect: ok({ node: { id: "n1" }, neighborhood: { nodes: [], edges: [] } }) },
    });

    await waitFor(() => expect(useAppStore.getState().focusedGraphNodeId).toBeNull());
    expect(await screen.findByTestId("graph-node-inspector")).toBeTruthy();

    const conflictBtn = screen.getByTestId("graph-conflicts-toggle");
    expect(conflictBtn.getAttribute("aria-pressed")).toBe("false");
    await userEvent.click(conflictBtn);
    expect(conflictBtn.getAttribute("aria-pressed")).toBe("true");

    const searchInput = screen.getByRole("textbox", { name: "지식 그래프 검색" });
    searchInput.blur();
    fireEvent.keyDown(window, { key: "/" });
    expect(document.activeElement).toBe(searchInput);
    fireEvent.keyDown(searchInput, { key: "/" });

    fireEvent.keyDown(window, { key: "Escape" });
    await waitFor(() => expect(screen.queryByTestId("graph-node-inspector")).toBeNull());
    expect(conflictBtn.getAttribute("aria-pressed")).toBe("false");

    await userEvent.click(conflictBtn);
    expect(conflictBtn.getAttribute("aria-pressed")).toBe("true");
    fireEvent.keyDown(window, { key: "Escape" });
    expect(conflictBtn.getAttribute("aria-pressed")).toBe("false");
    fireEvent.keyDown(window, { key: "Escape" });
  });
});
