import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import {
  BrainKnowledgeFlow,
  BrainMemoryAutomation,
  ConversationKnowledgeTrace,
} from "./BrainKnowledgeFlow";
import type {
  BrainBrief,
  BrainProactiveAction,
  BrainProactiveActivity,
  BrainReadiness,
  EmergenceEvent,
  IngestionSourceType,
  IngestionState,
  KnowledgeGraphModel,
  MemoryFragment,
} from "./types";

const READINESS: BrainReadiness = {
  score: 42,
  state: "forming",
  depth: 2,
  titleKey: "brain.readiness.title",
  actionKey: "brain.readiness.action",
  source: "frontend_fallback",
  signals: { memoryCount: 2, conceptCount: 1, relationshipCount: 1, healthySources: 1 },
};

function brief(overrides: Partial<BrainBrief> = {}): BrainBrief {
  return {
    status: "forming",
    score: 42,
    headlineKey: "brain.brief.headline.forming",
    bodyKey: "brain.brief.body.forming",
    focus: { kind: "memory", title: "", detail: "", source: "", score: 0, empty: true },
    nextActions: [],
    suggestedQuestions: [],
    proactiveActions: [],
    evidence: [],
    generatedAt: "",
    ...overrides,
  };
}

const EMPTY_INGESTION: Record<IngestionSourceType, IngestionState | null> = {
  chat: null,
  file: null,
  folder: null,
  note: null,
  web: null,
};

function ingestion(overrides: Partial<IngestionState> = {}): IngestionState {
  return {
    sourceType: "file",
    label: "보고서.pdf",
    stage: "parsing",
    startedAt: 0,
    completedAt: null,
    newMemories: 0,
    newEntities: 0,
    ...overrides,
  };
}

function node(id: string, overrides: Partial<KnowledgeGraphModel["nodes"][number]> = {}) {
  return { id, label: id, type: "Concept", summary: "", importance: 1, ...overrides };
}

function emergence(overrides: Partial<EmergenceEvent> = {}): EmergenceEvent {
  return {
    id: "ev-1",
    sourceType: "file",
    label: "회의 기록",
    newMemories: 3,
    newEntities: 2,
    nodeIds: [],
    at: Date.now(),
    ...overrides,
  };
}

function memory(title: string): MemoryFragment {
  return { id: `mem-${title}`, title, kind: "note", tags: [], agentGenerated: false };
}

function renderFlow(overrides: Partial<React.ComponentProps<typeof BrainKnowledgeFlow>> = {}) {
  const onExploreBrain = vi.fn();
  const utils = render(
    <BrainKnowledgeFlow
      language="ko"
      brainState="idle"
      intensity={0.4}
      graph={{ nodes: [], edges: [] }}
      readiness={READINESS}
      brief={brief()}
      memories={[]}
      ingestionStates={EMPTY_INGESTION}
      emergenceEvents={[]}
      streaming={false}
      onExploreBrain={onExploreBrain}
      {...overrides}
    />,
  );
  return { ...utils, onExploreBrain };
}

describe("BrainKnowledgeFlow", () => {
  it("renders the idle empty state before anything has been remembered", () => {
    renderFlow();
    const section = document.querySelector(".brain-knowledge-flow")!;
    expect(section.getAttribute("data-source")).toBe("idle");
    expect(section.getAttribute("data-stage")).toBe("ready");
    expect(section.className).not.toContain("is-absorbing");
    expect(screen.getByText("첫 대화나 자료를 기다리는 중")).toBeTruthy();
    expect(
      screen.getByText("질문하거나 자료를 넣으면 Brain이 기억과 연결을 만들기 시작합니다."),
    ).toBeTruthy();
    // Empty graph message instead of node buttons.
    expect(document.querySelector(".brain-flow-empty-graph")).toBeTruthy();
    expect(document.querySelectorAll(".brain-flow-node").length).toBe(0);
    // No particles at all while idle and empty.
    expect(document.querySelectorAll(".brain-flow-particle").length).toBe(0);
    expect(screen.getByText("대기 중")).toBeTruthy();
    // Counts prefer the readiness signals when the local lists are smaller.
    expect(screen.getByText("주제 1 · 관계 1")).toBeTruthy();
  });

  it("marks the chat lane while streaming and ranks emerging nodes first", () => {
    const graph: KnowledgeGraphModel = {
      nodes: [
        node("허브", { importance: 0.5 }),
        node("연결A", { importance: 0.7 }),
        node("연결B", { importance: 0.9, summary: "요약" }),
        node("외톨이1", { importance: 0.8 }),
        node("외톨이2", { importance: 0.2 }),
      ],
      edges: [
        { id: "e1", source: "허브", target: "연결A", label: "관련", weight: 1 },
        { id: "e2", source: "허브", target: "연결B", label: "포함", weight: 1 },
        { id: "e3", source: "허브", target: "유령", label: "끊김", weight: 1 },
      ],
    };
    const { onExploreBrain } = renderFlow({
      streaming: true,
      graph,
      emergenceEvents: [emergence({ nodeIds: ["연결B"] })],
      memories: [memory("첫 기억")],
    });

    const section = document.querySelector(".brain-knowledge-flow")!;
    expect(section.getAttribute("data-source")).toBe("chat");
    expect(section.getAttribute("data-stage")).toBe("connected");
    expect(section.className).toContain("is-absorbing");

    // Emerging node outranks the hub, degree outranks importance, importance
    // breaks the tie between the two isolated nodes.
    const labels = Array.from(document.querySelectorAll(".brain-flow-node strong")).map(
      (element) => element.textContent,
    );
    expect(labels).toEqual(["연결B", "허브", "연결A", "외톨이1", "외톨이2"]);
    expect(document.querySelector(".brain-flow-node.is-emerging strong")?.textContent).toBe("연결B");

    // The edge to a missing node is filtered from the svg and the sr list.
    expect(document.querySelectorAll(".brain-flow-edges line").length).toBe(2);
    expect(screen.getByText("허브 — 포함 — 연결B")).toBeTruthy();

    // Streaming absorbs: input particles plus fast output particles.
    expect(document.querySelectorAll(".brain-flow-particle").length).toBe(7);

    // Source column: chat is the active lane.
    const active = document.querySelector(".brain-flow-source.is-active")!;
    expect(active.textContent).toContain("대화");
    expect(active.getAttribute("aria-current")).toBe("step");

    // Emergence status line wins over the ready line.
    expect(screen.getByText("회의 기록")).toBeTruthy();
    expect(screen.getByText("새 기억 3개와 주제 2개가 실제 Brain에 연결됐습니다.")).toBeTruthy();

    fireEvent.click(document.querySelector(".brain-flow-node")!);
    expect(onExploreBrain).toHaveBeenCalled();
  });

  it("shows the active ingestion stage and remembers completed lanes", () => {
    renderFlow({
      ingestionStates: {
        ...EMPTY_INGESTION,
        file: ingestion({ stage: "parsing", label: "보고서.pdf" }),
        note: ingestion({ sourceType: "note", stage: "complete", label: "노트" }),
        web: ingestion({ sourceType: "web", stage: "error", label: "https://x" }),
      },
    });
    const section = document.querySelector(".brain-knowledge-flow")!;
    expect(section.getAttribute("data-source")).toBe("file");
    expect(section.getAttribute("data-stage")).toBe("parsing");
    expect(section.className).toContain("is-absorbing");
    expect(screen.getByText("보고서.pdf")).toBeTruthy();
    expect(screen.getByText("글과 표를 읽어내는 중")).toBeTruthy();
    expect(document.querySelector(".brain-flow-source.is-active")?.textContent).toContain("파일");
    expect(document.querySelector(".brain-flow-source.is-remembered")?.textContent).toContain("노트");
  });

  it("skips machine-generated titles until a human one is found", () => {
    // Generated ingestion label and uuid event label both fall through to the
    // brief focus title.
    const { rerender } = renderFlow({
      ingestionStates: {
        ...EMPTY_INGESTION,
        folder: ingestion({ sourceType: "folder", stage: "embedding", label: "brain-1782904609263" }),
      },
      emergenceEvents: [emergence({ label: "550e8400-e29b-41d4-a716-446655440000" })],
      brief: brief({ focus: { kind: "memory", title: "예산 계획", detail: "", source: "메모", score: 1, empty: false } }),
    });
    expect(screen.getByText("예산 계획")).toBeTruthy();

    // Digit-only focus falls to the first memory title.
    rerender(
      <BrainKnowledgeFlow
        language="ko"
        brainState="idle"
        intensity={0.4}
        graph={{ nodes: [], edges: [] }}
        readiness={READINESS}
        brief={brief({ focus: { kind: "memory", title: "20260805", detail: "", source: "", score: 0, empty: false } })}
        memories={[memory("면접 준비 노트")]}
        ingestionStates={EMPTY_INGESTION}
        emergenceEvents={[]}
        streaming={false}
        onExploreBrain={() => {}}
      />,
    );
    expect(screen.getByText("면접 준비 노트")).toBeTruthy();
  });

  it("keeps the ready line focused on the strongest concept when the brief is silent", () => {
    const graph: KnowledgeGraphModel = {
      nodes: [node("이직 준비", { summary: "이직 메모" })],
      edges: [],
    };
    const { rerender } = renderFlow({ graph, memories: [memory("기억 하나")] });
    expect(screen.getByText("이직 준비 주변의 기억과 연결을 유지하고 있습니다.")).toBeTruthy();
    // No emergence and nothing absorbing: slow output particles only.
    expect(document.querySelectorAll(".brain-flow-particle.is-output").length).toBe(3);
    expect(document.querySelectorAll(".brain-flow-particle").length).toBe(3);

    // An id-shaped node label falls back to the neutral "Brain" focus.
    rerender(
      <BrainKnowledgeFlow
        language="ko"
        brainState="idle"
        intensity={0.4}
        graph={{ nodes: [node("doc_88372611", { label: "doc_88372611" })], edges: [] }}
        readiness={READINESS}
        brief={brief()}
        memories={[]}
        ingestionStates={EMPTY_INGESTION}
        emergenceEvents={[]}
        streaming={false}
        onExploreBrain={() => {}}
      />,
    );
    expect(screen.getByText("Brain 주변의 기억과 연결을 유지하고 있습니다.")).toBeTruthy();
  });

  it("caps the constellation at eight nodes and counts from the larger source", () => {
    const nodes = Array.from({ length: 10 }, (_, index) => node(`개념${index}`, { importance: 10 - index }));
    renderFlow({
      graph: { nodes, edges: [] },
      brief: brief({
        focus: { kind: "memory", title: "큰 그림", detail: "", source: "", score: 1, empty: false },
        proactiveActions: [action("a-1", "ask")],
      }),
    });
    expect(document.querySelectorAll(".brain-flow-node").length).toBe(8);
    // Graph counts prefer the live lists when they are larger than readiness.
    expect(screen.getByText("주제 10 · 관계 1")).toBeTruthy();
    expect(screen.getByText("기억 기반 제안 1개")).toBeTruthy();
  });
});

function action(id: string, intent: BrainProactiveAction["intent"]): BrainProactiveAction {
  return {
    id,
    intent,
    labelKey: "brain.flow.path.memory",
    detailKey: "brain.flow.path.input",
    prompt: "해줘",
    route: "/app/brain",
    priority: 1,
    context: {},
  };
}

function activity(id: string, status: BrainProactiveActivity["status"]): BrainProactiveActivity {
  return {
    id,
    actionId: id,
    labelKey: "brain.automation.intent.ask",
    intent: "ask",
    status,
    startedAt: 0,
  };
}

describe("BrainMemoryAutomation", () => {
  it("grounds the full panel in focus, evidence, actions and activity", () => {
    const onAction = vi.fn();
    render(
      <BrainMemoryAutomation
        language="ko"
        brief={brief({
          focus: { kind: "memory", title: "예산 계획", detail: "", source: "메모", score: 1, empty: false },
          proactiveActions: [action("a1", "delegate"), action("a2", "review"), action("a3", "route"), action("a4", "ask")],
          evidence: [
            { id: "ev0", labelKey: "brain.flow.path.memory", value: 0, detailKey: "brain.flow.path.input" },
            { id: "ev1", labelKey: "brain.flow.path.memory", value: 3, detailKey: "brain.flow.path.input" },
            { id: "ev2", labelKey: "brain.flow.path.connections", value: 2, detailKey: "brain.flow.path.input" },
            { id: "ev3", labelKey: "brain.flow.path.automation", value: 1, detailKey: "brain.flow.path.input" },
            { id: "ev4", labelKey: "brain.flow.path.memory", value: 9, detailKey: "brain.flow.path.input" },
          ],
        })}
        activities={[
          activity("run1", "running"),
          activity("run2", "completed"),
          activity("run3", "failed"),
          activity("run4", "completed"),
        ]}
        streaming={false}
        onAction={onAction}
      />,
    );

    expect(screen.getByText("예산 계획")).toBeTruthy();
    expect(screen.getByText("메모")).toBeTruthy();
    // Zero-value evidence is dropped; only the first three positives render.
    const evidence = document.querySelectorAll(".brain-automation-evidence span");
    expect(evidence.length).toBe(3);
    // The three visible actions carry their intent captions.
    expect(screen.getByText("Agent 실행")).toBeTruthy();
    expect(screen.getByText("검토함에 초안")).toBeTruthy();
    expect(screen.getByText("그래프에서 확인")).toBeTruthy();
    expect(screen.queryByText("기억으로 질문", { selector: "em" })).toBeNull();
    // Three activity rows at most, each with its own status word.
    const rows = document.querySelectorAll(".brain-automation-activity li");
    expect(rows.length).toBe(3);
    expect(rows[0].getAttribute("data-status")).toBe("running");
    expect(screen.getByText("진행 중")).toBeTruthy();
    expect(screen.getByText("실패")).toBeTruthy();

    fireEvent.click(document.querySelectorAll(".brain-automation-actions button")[0]);
    expect(onAction).toHaveBeenCalledWith(expect.objectContaining({ id: "a1" }));
  });

  it("admits when there is nothing grounded to automate yet", () => {
    render(
      <BrainMemoryAutomation
        language="ko"
        brief={brief()}
        activities={[]}
        streaming
        onAction={() => {}}
      />,
    );
    expect(
      screen.getByText("기억이 더 생기면 Brain이 근거가 있는 자동화를 이곳에 제안합니다."),
    ).toBeTruthy();
    expect(document.querySelector(".brain-automation-actions")).toBeNull();
    expect(document.querySelector(".brain-automation-activity")).toBeNull();
    expect(document.querySelector(".brain-automation-evidence")).toBeNull();
    // Empty focus falls back to friendly copy, and no source em renders.
    expect(document.querySelector(".brain-automation-grounding em")).toBeNull();
  });

  it("compact: runs the primary action and opens the rest in the popover", async () => {
    const onAction = vi.fn();
    render(
      <BrainMemoryAutomation
        language="ko"
        compact
        brief={brief({
          focus: { kind: "memory", title: "예산 계획", detail: "", source: "메모", score: 1, empty: false },
          proactiveActions: [action("p1", "ask"), action("p2", "delegate"), action("p3", "review")],
        })}
        activities={[activity("run1", "completed")]}
        streaming={false}
        onAction={onAction}
      />,
    );
    const section = screen.getByTestId("brain-automation-dock");
    expect(section.className).toContain("has-more-actions");

    fireEvent.click(document.querySelector(".brain-automation-primary-action")!);
    expect(onAction).toHaveBeenCalledWith(expect.objectContaining({ id: "p1" }));

    const details = screen.getByTestId("brain-automation-more") as HTMLDetailsElement;
    await userEvent.click(details.querySelector("summary")!);
    expect(details.open).toBe(true);
    expect(document.querySelectorAll(".brain-automation-more-popover .brain-automation-actions button").length).toBe(3);
    expect(document.querySelector(".brain-automation-more-popover .brain-automation-activity")).toBeTruthy();

    fireEvent.click(
      document.querySelectorAll(".brain-automation-more-popover .brain-automation-actions button")[1],
    );
    expect(onAction).toHaveBeenCalledWith(expect.objectContaining({ id: "p2" }));

    // Escape closes and hands focus back to the summary; other keys pass.
    fireEvent.keyDown(details, { key: "a" });
    expect(details.open).toBe(true);
    fireEvent.keyDown(details, { key: "Escape" });
    expect(details.open).toBe(false);
    expect(document.activeElement).toBe(details.querySelector("summary"));

    // The close button inside the popover does the same.
    await userEvent.click(details.querySelector("summary")!);
    expect(details.open).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "기억 기반 행동 닫기" }));
    expect(details.open).toBe(false);
  });

  it("compact: a single action needs no popover and an empty brief says so", () => {
    const { rerender } = render(
      <BrainMemoryAutomation
        language="ko"
        compact
        brief={brief({ proactiveActions: [action("only", "route")] })}
        activities={[]}
        streaming
        onAction={() => {}}
      />,
    );
    const section = screen.getByTestId("brain-automation-dock");
    expect(section.className).toContain("has-single-action");
    expect(screen.queryByTestId("brain-automation-more")).toBeNull();
    expect((document.querySelector(".brain-automation-primary-action") as HTMLButtonElement).disabled).toBe(true);

    rerender(
      <BrainMemoryAutomation
        language="ko"
        compact
        brief={brief()}
        activities={[]}
        streaming={false}
        onAction={() => {}}
      />,
    );
    expect(screen.getByTestId("brain-automation-dock").className).toContain("has-no-actions");
    expect(
      screen.getByText("기억이 더 생기면 Brain이 근거가 있는 자동화를 이곳에 제안합니다."),
    ).toBeTruthy();
  });

  it("compact: hides the activity trail inside the popover when there is none", async () => {
    render(
      <BrainMemoryAutomation
        language="ko"
        compact
        brief={brief({ proactiveActions: [action("p1", "ask"), action("p2", "ask")] })}
        activities={[]}
        streaming={false}
        onAction={() => {}}
      />,
    );
    const details = screen.getByTestId("brain-automation-more") as HTMLDetailsElement;
    await userEvent.click(details.querySelector("summary")!);
    expect(document.querySelector(".brain-automation-more-popover")).toBeTruthy();
    expect(document.querySelector(".brain-automation-more-popover .brain-automation-activity")).toBeNull();
  });
});

describe("ConversationKnowledgeTrace", () => {
  it("walks the conversation into concepts while ingesting", () => {
    const onExploreBrain = vi.fn();
    render(
      <ConversationKnowledgeTrace
        language="ko"
        state={ingestion({ sourceType: "chat", stage: "embedding" })}
        concepts={[node("예산"), node("일정"), node("보고"), node("네번째")]}
        relationshipCount={7}
        onExploreBrain={onExploreBrain}
      />,
    );
    const trace = document.querySelector(".brain-conversation-trace")!;
    expect(trace.className).toContain("is-active");
    expect(screen.getByText("무슨 내용인지 파악하는 중")).toBeTruthy();
    expect(screen.getByText("예산 · 일정 · 보고")).toBeTruthy();
    expect(screen.getByText("실제 관계 7개")).toBeTruthy();
    fireEvent.click(screen.getByRole("button"));
    expect(onExploreBrain).toHaveBeenCalled();
  });

  it("rests once the conversation is stored and shows the empty graph hint", () => {
    const { rerender } = render(
      <ConversationKnowledgeTrace
        language="ko"
        state={null}
        concepts={[]}
        relationshipCount={0}
        onExploreBrain={() => {}}
      />,
    );
    expect(document.querySelector(".brain-conversation-trace")!.className).not.toContain("is-active");
    expect(screen.getByText("대화를 Brain 기억으로 보관")).toBeTruthy();
    expect(screen.getByText("첫 연결을 만드는 중")).toBeTruthy();

    rerender(
      <ConversationKnowledgeTrace
        language="ko"
        state={ingestion({ sourceType: "chat", stage: "complete" })}
        concepts={[]}
        relationshipCount={0}
        onExploreBrain={() => {}}
      />,
    );
    expect(document.querySelector(".brain-conversation-trace")!.className).not.toContain("is-active");
    expect(screen.getByText("내 지식으로 들어왔습니다")).toBeTruthy();
  });
});
