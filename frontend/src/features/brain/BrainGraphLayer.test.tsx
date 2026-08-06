import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import * as React from "react";
import { describe, expect, it, vi } from "vitest";

import { renderPage } from "@/test/renderPage";
import { BrainGraphLayer, BrainKnowledgeLayer, highlightMatch } from "./BrainGraphLayer";
import type { KnowledgeConcept, KnowledgeGraphModel, RelationshipThread } from "./types";

const HOUR = 60 * 60 * 1000;
const DAY = 24 * HOUR;

const concept = (
  id: string,
  label: string,
  type: string,
  overrides: Partial<KnowledgeConcept> = {},
): KnowledgeConcept => ({ id, label, type, summary: "", importance: 1, ...overrides });

const edge = (
  id: string,
  source: string,
  target: string,
  weight = 1,
): RelationshipThread => ({ id, source, target, label: "relates", weight });

function baseModel(): KnowledgeGraphModel {
  return {
    nodes: [
      concept("n1", "Alpha One", "project", { summary: "메인 프로젝트", createdAt: Date.now() - HOUR }),
      concept("n2", "Beta One", "note", { createdAt: Date.now() - 40 * DAY }),
      concept("n3", "Gamma Solo", "person", { summary: "감마 담당" }),
      concept("n4", "Delta", "one-off"),
      concept("n5", "Echo", "one-off"),
    ],
    edges: [
      edge("e1", "n1", "n2", 1),
      edge("e2", "n2", "n3", 5), // weight clamps down to 2.8
      edge("e3", "n1", "ghost"), // endpoint not visible → never drawn, still a neighbor
      edge("e4", "n4", "n5", 0.1), // weight clamps up to 0.4
    ],
  };
}

function Harness({
  model,
  models,
  initialSearch = "",
  initialSelected = null,
  onSearchSpy,
  onSelectSpy,
}: {
  model?: KnowledgeGraphModel;
  /** Optional refresh sequence: the swap button advances through these. */
  models?: KnowledgeGraphModel[];
  initialSearch?: string;
  initialSelected?: string | null;
  onSearchSpy?: (value: string) => void;
  onSelectSpy?: (id: string | null) => void;
}) {
  const sequence = models ?? [model as KnowledgeGraphModel];
  const [modelIndex, setModelIndex] = React.useState(0);
  const [search, setSearch] = React.useState(initialSearch);
  const [selectedId, setSelectedId] = React.useState<string | null>(initialSelected);
  return (
    <>
      {sequence.length > 1 ? (
        <button
          type="button"
          data-testid="swap-model"
          onClick={() => setModelIndex((index) => Math.min(index + 1, sequence.length - 1))}
        >
          swap
        </button>
      ) : null}
      <BrainGraphLayer
        model={sequence[modelIndex]}
        search={search}
        selectedId={selectedId}
        onSearch={(value) => {
          onSearchSpy?.(value);
          setSearch(value);
        }}
        onSelect={(id) => {
          onSelectSpy?.(id);
          setSelectedId(id);
        }}
      />
    </>
  );
}

const nodeButton = (container: HTMLElement, label: string) =>
  Array.from(container.querySelectorAll<HTMLButtonElement>(".graph-node")).find((button) =>
    (button.textContent || "").includes(label),
  ) as HTMLButtonElement;

const edgeLines = (container: HTMLElement) =>
  Array.from(container.querySelectorAll<SVGLineElement>(".brain-graph-edges line"));

describe("BrainKnowledgeLayer", () => {
  it("admits it is still forming when no concept exists", () => {
    const { container } = renderPage(<BrainKnowledgeLayer concepts={[]} depth={3} />);
    expect(container.textContent).toContain("지식이 형성되는 중입니다.");
  });

  it("caps the constellation at 7 below depth 4 and 10 from depth 4 on", () => {
    const concepts = Array.from({ length: 12 }, (_, index) =>
      concept(`c${index}`, `개념 ${index}`, "topic"),
    );
    const shallow = renderPage(<BrainKnowledgeLayer concepts={concepts} depth={3} />);
    expect(shallow.container.querySelectorAll(".concept-signal")).toHaveLength(7);
    shallow.unmount();

    const deep = renderPage(<BrainKnowledgeLayer concepts={concepts} depth={4} />);
    expect(deep.container.querySelectorAll(".concept-signal")).toHaveLength(10);
  });

  it("prefers the summary as tooltip and falls back to the type", () => {
    const { container } = renderPage(
      <BrainKnowledgeLayer
        concepts={[
          concept("a", "여행", "topic", { summary: "여행 계획 지식" }),
          concept("b", "예산", "money"),
        ]}
        depth={3}
      />,
    );
    const [first, second] = Array.from(container.querySelectorAll<HTMLButtonElement>(".concept-signal"));
    expect(first.title).toBe("여행 계획 지식");
    expect(second.title).toBe("money");
  });
});

describe("highlightMatch", () => {
  const renderNodes = (label: string, query: string) =>
    render(<span data-testid="hl">{highlightMatch(label, query)}</span>).getByTestId("hl");

  it("returns the label untouched for an empty query", () => {
    expect(renderNodes("Alpha", "").innerHTML).toBe("Alpha");
  });

  it("marks a match at the start and keeps the tail", () => {
    const host = renderNodes("Alpha Project", "alpha");
    expect(host.innerHTML).toBe("<mark>Alpha</mark> Project");
  });

  it("marks a match at the end and keeps the head", () => {
    const host = renderNodes("My Alpha", "alpha");
    expect(host.innerHTML).toBe("My <mark>Alpha</mark>");
  });

  it("marks every occurrence, case-insensitively", () => {
    const host = renderNodes("alpha ALPHA tail", "alpha");
    expect(host.querySelectorAll("mark")).toHaveLength(2);
    expect(host.textContent).toBe("alpha ALPHA tail");
  });

  it("returns the plain label when nothing matches", () => {
    const host = renderNodes("Beta", "zz");
    expect(host.querySelectorAll("mark")).toHaveLength(0);
    expect(host.textContent).toBe("Beta");
  });

  it("survives an empty label", () => {
    expect(renderNodes("", "zz").textContent).toBe("");
  });
});

describe("BrainGraphLayer", () => {
  it("shows the empty graph and empty focus panel when no node matches", () => {
    const { container } = renderPage(<Harness model={{ nodes: [], edges: [] }} />);
    expect(screen.getByText("아직 맞는 지식이 없습니다.")).toBeTruthy();
    expect(screen.getByText("대화, 문서, 프로젝트를 쌓으면 Brain 그래프가 자랍니다.")).toBeTruthy();
    expect(container.querySelector(".brain-graph-canvas")).toBeNull();
    // No timestamps anywhere → the day-window chips are unusable, all-time is not.
    expect(screen.getByRole("button", { name: "최근 7일" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "전체 기간" })).toBeEnabled();
  });

  it("renders nodes and clamped edges, defaults focus to the first node, and marks recency", () => {
    const { container } = renderPage(<Harness model={baseModel()} />);

    const nodes = container.querySelectorAll(".graph-node");
    expect(nodes).toHaveLength(5);
    expect(nodeButton(container, "Alpha One").className).toContain("is-selected");
    expect(nodeButton(container, "Alpha One").getAttribute("data-recent")).toBe("true");
    expect(nodeButton(container, "Beta One").getAttribute("data-recent")).toBeNull(); // old
    expect(nodeButton(container, "Gamma Solo").getAttribute("data-recent")).toBeNull(); // no timestamp
    expect(nodeButton(container, "Delta").style.getPropertyValue("--x")).toContain("%");

    const lines = edgeLines(container);
    expect(lines).toHaveLength(3); // e3 has an invisible endpoint
    expect(lines.map((line) => line.style.getPropertyValue("--weight"))).toEqual(["1", "2.8", "0.4"]);
    // No focus, no query → edges carry no highlight verdict at all.
    expect(lines.every((line) => line.getAttribute("data-highlight") === null)).toBe(true);
    expect(container.querySelector(".brain-graph-canvas")?.getAttribute("data-focus-active")).toBe("false");

    // Default focus panel: first node, its summary, and the ambient line.
    const focus = container.querySelector(".brain-graph-focus") as HTMLElement;
    expect(focus.textContent).toContain("Alpha One");
    expect(focus.textContent).toContain("메인 프로젝트");
    expect(focus.textContent).toContain("대화와 문서에서 함께 나온 내용이 선으로 이어집니다.");

    // The type chips list every distinct type, sorted.
    const chips = Array.from(container.querySelectorAll(".brain-graph-type-chips .brain-graph-chip"));
    expect(chips.map((chip) => chip.textContent)).toEqual(["전체 타입", "note", "one-off", "person", "project"]);
    expect(chips[0].className).toContain("is-active");
  });

  it("dims non-neighbors in focus mode, highlights touching edges, and toggles selection off", async () => {
    const onSelectSpy = vi.fn();
    const { container } = renderPage(
      <Harness model={baseModel()} initialSelected="n1" onSelectSpy={onSelectSpy} />,
    );

    expect(container.querySelector(".brain-graph-canvas")?.getAttribute("data-focus-active")).toBe("true");
    // n1 itself and its 1-hop neighbors stay lit (the ghost neighbor counts too).
    expect(screen.getByText("이웃 2개 강조")).toBeTruthy();
    expect(nodeButton(container, "Alpha One").getAttribute("data-focus")).toBe("true");
    expect(nodeButton(container, "Beta One").getAttribute("data-focus")).toBe("true");
    expect(nodeButton(container, "Delta").getAttribute("data-focus")).toBe("false");

    const lines = edgeLines(container);
    expect(lines[0].getAttribute("data-highlight")).toBe("true"); // e1 touches n1
    expect(lines[1].getAttribute("data-highlight")).toBe("false"); // e2 does not
    expect(lines[2].getAttribute("data-highlight")).toBe("false"); // e4 does not

    // Clicking another node moves the selection; clicking it again clears it.
    await userEvent.click(nodeButton(container, "Delta"));
    expect(onSelectSpy).toHaveBeenLastCalledWith("n4");
    await userEvent.click(nodeButton(container, "Delta"));
    expect(onSelectSpy).toHaveBeenLastCalledWith(null);
    expect(container.querySelector(".brain-graph-canvas")?.getAttribute("data-focus-active")).toBe("false");
  });

  it("keeps focus inactive for a selection with no neighbors and offers the summary fallback", () => {
    const model: KnowledgeGraphModel = {
      nodes: [concept("n1", "Alpha One", "project"), concept("n2", "Beta One", "note")],
      edges: [],
    };
    const { container } = renderPage(<Harness model={model} initialSelected="n2" />);
    expect(container.querySelector(".brain-graph-canvas")?.getAttribute("data-focus-active")).toBe("false");
    expect(nodeButton(container, "Alpha One").getAttribute("data-focus")).toBeNull();

    const focus = container.querySelector(".brain-graph-focus") as HTMLElement;
    expect(focus.textContent).toContain("Beta One");
    expect(focus.textContent).toContain("이 개념은 가장 깊은 지식 계층의 일부입니다.");
    expect(focus.textContent).not.toContain("이웃");
  });

  it("filters by query across label/type/summary, marks label matches, and grades edges", async () => {
    const onSearchSpy = vi.fn();
    const { container } = renderPage(
      <Harness model={baseModel()} initialSelected="n1" onSearchSpy={onSearchSpy} />,
    );
    const input = screen.getByRole("combobox");

    await userEvent.click(input);
    expect(input.getAttribute("aria-expanded")).toBe("false"); // focused but no query yet

    await userEvent.type(input, "one");
    expect(onSearchSpy).toHaveBeenCalledTimes(3);
    expect(input.getAttribute("aria-expanded")).toBe("true");

    // "one" hits Alpha One / Beta One by label and the one-off pair by type.
    await waitFor(() => expect(container.querySelectorAll(".graph-node")).toHaveLength(4));
    expect(nodeButton(container, "Alpha One").className).toContain("is-match");
    expect(nodeButton(container, "Alpha One").querySelector("mark")?.textContent).toBe("One");
    expect(nodeButton(container, "Delta").className).not.toContain("is-match");

    // Both endpoints visible: label-matched edge lights up, type-only edge does not.
    const lines = edgeLines(container);
    expect(lines).toHaveLength(2);
    expect(lines[0].getAttribute("data-highlight")).toBe("true"); // n1–n2
    expect(lines[1].getAttribute("data-highlight")).toBe("false"); // n4–n5

    // The typeahead ranks all four and marks the already-selected node.
    const options = screen.getAllByRole("option");
    expect(options).toHaveLength(4);
    expect(options[0].getAttribute("aria-selected")).toBe("true");
    expect(options[1].getAttribute("aria-selected")).toBe("false");
  });

  it("picks a suggestion on mousedown, promoting it to search text and selection", async () => {
    const onSearchSpy = vi.fn();
    const onSelectSpy = vi.fn();
    const { container } = renderPage(
      <Harness model={baseModel()} onSearchSpy={onSearchSpy} onSelectSpy={onSelectSpy} />,
    );
    const input = screen.getByRole("combobox");
    await userEvent.type(input, "beta");
    const option = await screen.findByRole("option");
    expect(option.textContent).toContain("note");

    fireEvent.mouseDown(option.querySelector("button") as HTMLButtonElement);
    expect(onSearchSpy).toHaveBeenLastCalledWith("Beta One");
    expect(onSelectSpy).toHaveBeenLastCalledWith("n2");
    await waitFor(() => expect(screen.queryByRole("listbox")).toBeNull());
    expect(nodeButton(container, "Beta One").className).toContain("is-selected");
  });

  it("admits when no suggestion matches and clears the search from the input button", async () => {
    const onSearchSpy = vi.fn();
    const { container } = renderPage(<Harness model={baseModel()} onSearchSpy={onSearchSpy} />);
    const input = screen.getByRole("combobox");
    await userEvent.type(input, "zzz");

    expect(screen.getByText("일치하는 노드가 없습니다.")).toBeTruthy();
    expect(screen.getByText("아직 맞는 지식이 없습니다.")).toBeTruthy(); // canvas empty too
    expect(screen.getByText("대화, 문서, 프로젝트를 쌓으면 Brain 그래프가 자랍니다.")).toBeTruthy();

    await userEvent.click(screen.getByRole("button", { name: "포커스 해제" }));
    expect(onSearchSpy).toHaveBeenLastCalledWith("");
    await waitFor(() =>
      expect(screen.queryByRole("button", { name: "포커스 해제" })).toBeNull(),
    );
    expect(container.querySelectorAll(".graph-node")).toHaveLength(5);
  });

  it("closes the typeahead only after the blur grace period", () => {
    vi.useFakeTimers();
    try {
      const { container } = renderPage(<Harness model={baseModel()} initialSearch="one" />);
      const input = container.querySelector(".brain-graph-search input") as HTMLInputElement;

      fireEvent.focus(input);
      expect(container.querySelector(".brain-graph-typeahead")).toBeTruthy();

      fireEvent.blur(input);
      expect(container.querySelector(".brain-graph-typeahead")).toBeTruthy(); // still within grace

      act(() => {
        vi.advanceTimersByTime(130);
      });
      expect(container.querySelector(".brain-graph-typeahead")).toBeNull();
    } finally {
      vi.useRealTimers();
    }
  });

  it("toggles type chips on and off, resets them, and prunes stale types on refresh", async () => {
    const model = baseModel();
    const { container } = renderPage(
      <Harness
        models={[
          model,
          { ...model, nodes: [...model.nodes] }, // same types, new array identity
          { nodes: model.nodes.filter((node) => node.type !== "person"), edges: model.edges },
        ]}
      />,
    );

    await userEvent.click(screen.getByRole("button", { name: "note 타입 표시 전환" }));
    expect(container.querySelectorAll(".graph-node")).toHaveLength(1);
    expect(screen.getByRole("button", { name: "note 타입 표시 전환" }).className).toContain("is-active");
    expect(screen.getByRole("button", { name: "전체 타입" }).className).not.toContain("is-active");

    // A second type widens the filter; unticking one narrows it again.
    await userEvent.click(screen.getByRole("button", { name: "person 타입 표시 전환" }));
    expect(container.querySelectorAll(".graph-node")).toHaveLength(2);
    await userEvent.click(screen.getByRole("button", { name: "note 타입 표시 전환" }));
    expect(container.querySelectorAll(".graph-node")).toHaveLength(1);
    expect(nodeButton(container, "Gamma Solo")).toBeTruthy();

    // The all-types chip clears the type selection outright.
    await userEvent.click(screen.getByRole("button", { name: "전체 타입" }));
    expect(container.querySelectorAll(".graph-node")).toHaveLength(5);
    expect(screen.getByRole("button", { name: "전체 타입" }).className).toContain("is-active");
    await userEvent.click(screen.getByRole("button", { name: "person 타입 표시 전환" }));
    expect(container.querySelectorAll(".graph-node")).toHaveLength(1);

    // Reset restores everything.
    await userEvent.click(screen.getByRole("button", { name: "필터 초기화" }));
    expect(container.querySelectorAll(".graph-node")).toHaveLength(5);
    expect(screen.queryByRole("button", { name: "필터 초기화" })).toBeNull();

    // Select person again, then refresh the graph twice: first with the same
    // types (selection survives), then without person (selection is pruned).
    await userEvent.click(screen.getByRole("button", { name: "person 타입 표시 전환" }));
    await userEvent.click(screen.getByTestId("swap-model"));
    expect(screen.getByRole("button", { name: "person 타입 표시 전환" }).className).toContain("is-active");
    expect(container.querySelectorAll(".graph-node")).toHaveLength(1); // filter still applies

    await userEvent.click(screen.getByTestId("swap-model"));
    await waitFor(() =>
      expect(screen.queryByRole("button", { name: "person 타입 표시 전환" })).toBeNull(),
    );
    // The stale selection no longer filters anything.
    expect(container.querySelectorAll(".graph-node")).toHaveLength(4);
  });

  it("narrows to the recent window, keeps undated nodes, and returns on all-time", async () => {
    const { container } = renderPage(<Harness model={baseModel()} />);

    const week = screen.getByRole("button", { name: "최근 7일" });
    expect(week).toBeEnabled(); // timestamps exist in this model
    await userEvent.click(week);
    expect(week.className).toContain("is-active");
    // Beta One is 40 days old → dropped; undated nodes are kept.
    expect(container.querySelectorAll(".graph-node")).toHaveLength(4);
    expect(nodeButton(container, "Beta One")).toBeUndefined();
    expect(nodeButton(container, "Gamma Solo")).toBeTruthy();
    expect(screen.getByRole("button", { name: "필터 초기화" })).toBeTruthy();

    await userEvent.click(screen.getByRole("button", { name: "전체 기간" }));
    expect(container.querySelectorAll(".graph-node")).toHaveLength(5);
  });
});
