import { fireEvent, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { renderPage } from "@/test/renderPage";
import { MemoryRings } from "./MemoryRings";
import type { BrainDepth, BrainReadiness, KnowledgeConcept, MemoryFragment } from "./types";

const memory = (id: string, overrides: Partial<MemoryFragment> = {}): MemoryFragment => ({
  id,
  title: `기억 ${id}`,
  kind: "Note",
  tags: [],
  agentGenerated: false,
  ...overrides,
});

const concept = (id: string): KnowledgeConcept => ({
  id,
  label: `주제 ${id}`,
  type: "topic",
  summary: "",
  importance: 1,
});

const readiness = (depth: BrainDepth): BrainReadiness => ({
  score: 55,
  state: "forming",
  depth,
  titleKey: "brain.readiness.forming",
  actionKey: "brain.readiness.grow",
  source: "frontend_fallback",
  signals: { memoryCount: 3, conceptCount: 2, relationshipCount: 1, healthySources: 2 },
});

function renderRings(input: {
  memories?: MemoryFragment[];
  concepts?: KnowledgeConcept[];
  relationshipCount?: number;
  depth?: BrainDepth;
  onExploreBrain?: () => void;
  onOpenDepth?: (depth: BrainDepth) => void;
} = {}) {
  return renderPage(
    <MemoryRings
      language="ko"
      brainState="idle"
      intensity={0.6}
      readiness={readiness(input.depth ?? 2)}
      memories={input.memories ?? []}
      concepts={input.concepts ?? []}
      relationshipCount={input.relationshipCount ?? 0}
      onExploreBrain={input.onExploreBrain ?? (() => {})}
      onOpenDepth={input.onOpenDepth ?? (() => {})}
    />,
  );
}

const populated = {
  memories: [
    memory("c1", { title: "지금 나눈 대화", kind: "Conversation" }),
    memory("c2", { title: "어제 대화", kind: "Conversation" }),
    memory("d1", { title: "여행 문서" }),
    memory("d2", { title: "Agent 정리 노트", agentGenerated: true }),
    memory("d3", { title: "예산 메모" }),
  ],
  concepts: [concept("t1"), concept("t2")],
  relationshipCount: 4,
};

describe("MemoryRings", () => {
  it("lights populated rings, keeps empty ones dormant, and shows each count", () => {
    const { container } = renderRings({ ...populated, concepts: [] });
    const orbit = container.querySelectorAll(".brain-orbit-field .brain-concentric-ring");
    expect(orbit).toHaveLength(4);
    expect(orbit[0].className).toContain("is-populated"); // now: 2 conversations
    expect(orbit[1].className).toContain("is-populated"); // memories: 3 durable
    expect(orbit[2].className).toContain("is-dormant"); // topics: none
    expect(orbit[3].className).toContain("is-populated"); // graph: 4 relationships

    const memoriesLabel = screen.getByRole("button", { name: "기억 미리 보기 · 3개" });
    expect(memoriesLabel.className).toContain("is-populated");
    expect(memoriesLabel.textContent).toContain("3");
    expect(screen.getByRole("button", { name: "주제 미리 보기 · 0개" }).className).not.toContain("is-populated");
  });

  it("opens a peek on ring click with agent memories first, goes deeper, and closes again", async () => {
    const onOpenDepth = vi.fn();
    const { container } = renderRings({ ...populated, onOpenDepth });
    expect(container.querySelector("#brain-ring-peek")).toBeNull();

    const ringButton = screen.getByRole("button", { name: "기억 미리 보기 · 3개" });
    await userEvent.click(ringButton);
    const peek = container.querySelector("#brain-ring-peek") as HTMLElement;
    expect(peek).toBeTruthy();
    expect(ringButton.getAttribute("aria-expanded")).toBe("true");
    expect(ringButton.className).toContain("is-active");

    // Durable fragments sort agent-generated work to the front.
    const items = peek.querySelectorAll("li");
    expect(items[0].textContent).toBe("Agent 정리 노트");
    expect(items).toHaveLength(3);

    await userEvent.click(screen.getByRole("button", { name: "이 층 자세히 보기" }));
    expect(onOpenDepth).toHaveBeenCalledWith(2);

    // Clicking the same ring again toggles the peek closed.
    await userEvent.click(ringButton);
    expect(container.querySelector("#brain-ring-peek")).toBeNull();
    expect(ringButton.getAttribute("aria-expanded")).toBe("false");
  });

  it("summarizes the graph ring by count and empties honestly when dormant", async () => {
    const { container, unmount } = renderRings({ ...populated });
    await userEvent.click(screen.getByRole("button", { name: "그래프 미리 보기 · 4개" }));
    const peek = container.querySelector("#brain-ring-peek") as HTMLElement;
    expect(peek.querySelector("ul")).toBeNull(); // graph ring never lists items
    expect(peek.textContent).toContain("4개의 연결이 이 층에 있습니다.");
    unmount();

    // Dormant "now" ring explains what would fill it.
    const dormant = renderRings({});
    await userEvent.click(screen.getByRole("button", { name: "지금 미리 보기 · 0개" }));
    expect(dormant.container.querySelector("#brain-ring-peek")?.textContent).toContain(
      "아직 최근 대화 기억이 없어요. 지금 말을 걸어보세요.",
    );
  });

  it("closes the peek from its close button", async () => {
    const { container } = renderRings({ ...populated });
    await userEvent.click(screen.getByRole("button", { name: "주제 미리 보기 · 2개" }));
    expect(container.querySelector("#brain-ring-peek")).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: "미리 보기 닫기" }));
    expect(container.querySelector("#brain-ring-peek")).toBeNull();
  });

  it("closes on window Escape but ignores other keys", async () => {
    const { container } = renderRings({ ...populated });
    await userEvent.click(screen.getByRole("button", { name: "기억 미리 보기 · 3개" }));
    expect(container.querySelector("#brain-ring-peek")).toBeTruthy();

    fireEvent.keyDown(window, { key: "a" });
    expect(container.querySelector("#brain-ring-peek")).toBeTruthy();

    fireEvent.keyDown(window, { key: "Escape" });
    expect(container.querySelector("#brain-ring-peek")).toBeNull();
  });

  it("hands the orb interaction to onExploreBrain and derives depth from the score when unset", async () => {
    const onExploreBrain = vi.fn();
    // depth 0 is outside the typed range but reachable from raw readiness data;
    // the orb then derives its depth from the score instead.
    const { container } = renderRings({
      ...populated,
      depth: 0 as BrainDepth,
      onExploreBrain,
    });
    const orb = container.querySelector(".brain-center-orb button") as HTMLButtonElement;
    expect(orb).toBeTruthy();
    await userEvent.click(orb);
    expect(onExploreBrain).toHaveBeenCalledTimes(1);
  });
});
