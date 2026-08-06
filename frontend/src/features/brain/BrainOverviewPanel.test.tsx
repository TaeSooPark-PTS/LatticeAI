import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { renderPage } from "@/test/renderPage";
import { BrainOverviewPanel } from "./BrainOverviewPanel";
import type { BrainProof, BrainReadiness, KnowledgeConcept, MemoryFragment } from "./types";

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

const readiness: BrainReadiness = {
  score: 60,
  state: "forming",
  depth: 3,
  titleKey: "brain.readiness.forming",
  actionKey: "brain.readiness.grow",
  source: "frontend_fallback",
  signals: { memoryCount: 4, conceptCount: 2, relationshipCount: 1, healthySources: 2 },
};

function proofWith(overrides: {
  proven?: boolean;
  keeps?: boolean;
  activeModel?: string;
  durable?: boolean;
  items?: BrainProof["recall"]["items"];
}): BrainProof {
  return {
    status: "forming",
    modelContinuity: {
      activeModel: overrides.activeModel ?? "",
      brainOwner: "user",
      capability: true,
      survivesModelSwitch: true,
      proven: overrides.proven ?? false,
      contextStore: "sqlite",
    },
    proofs: {
      durableItems: 12,
      hasDurableEvidence: overrides.durable ?? false,
      workspaceMemories: 3,
      conversations: 2,
      graphConcepts: 7,
      vectorItems: 21,
      healthySources: 2,
    },
    recall: { query: "여행 예산", count: overrides.items?.length ?? 0, items: overrides.items ?? [] },
    claims: {
      canRecallUserContext: true,
      keepsContextAcrossModels: overrides.keeps ?? false,
      isKnowledgeStore: true,
    },
  };
}

const recallItem = (snippet: string) => ({
  id: "r1",
  source: "graph",
  title: "지난 여행 계획",
  snippet,
  score: 0.9,
  matchedTerms: ["여행"],
  confidence: "high" as const,
  locator: "",
});

function renderPanel(input: {
  memories?: MemoryFragment[];
  concepts?: KnowledgeConcept[];
  proof: BrainProof;
  onOpenDepth?: (depth: number) => void;
}) {
  return renderPage(
    <BrainOverviewPanel
      memories={input.memories ?? []}
      concepts={input.concepts ?? []}
      readiness={readiness}
      proof={input.proof}
      onOpenDepth={input.onOpenDepth ?? (() => {})}
    />,
  );
}

describe("BrainOverviewPanel", () => {
  it("splits memories into agent/recent/older columns, features agent work, and routes every click to its depth", async () => {
    const onOpenDepth = vi.fn();
    const memories = [
      memory("a1", { title: "Agent 보고서", agentGenerated: true }),
      memory("u1", { title: "최근 1" }),
      memory("u2", { title: "최근 2" }),
      memory("u3", { title: "최근 3" }),
      memory("u4", { title: "이전 4" }),
      memory("u5", { title: "이전 5" }),
      memory("u6", { title: "이전 6" }),
      memory("u7", { title: "잘린 7" }),
    ];
    const concepts = ["c1", "c2", "c3", "c4", "c5"].map(concept);
    const proof = proofWith({
      proven: true,
      keeps: true,
      activeModel: "mlx-community/Llama-8B",
      durable: true,
      items: [recallItem("예산은 120만원으로 잡았음")],
    });
    const { container } = renderPanel({ memories, concepts, proof, onOpenDepth });

    const featured = container.querySelector(".brain-overview-column.is-featured");
    expect(featured?.textContent).toContain("Agent 보고서");
    expect(screen.getByText("최근 3")).toBeTruthy();
    expect(screen.getByText("이전 6")).toBeTruthy();
    expect(screen.queryByText("잘린 7")).toBeNull(); // older slice stops at 6
    expect(screen.getByText("주제 c3")).toBeTruthy();
    expect(screen.queryByText("주제 c4")).toBeNull(); // column shows at most 3

    // Proof strip: durable count, live model name, store numbers.
    expect(screen.getByText("12개 저장됨")).toBeTruthy();
    expect(screen.getByText("mlx-community/Llama-8B")).toBeTruthy();
    expect(screen.getByText("주제 7개 · 기억 조각 21개")).toBeTruthy();

    // Recall proof leads with the item and its snippet.
    expect(screen.getByText("지난 여행 계획")).toBeTruthy();
    expect(screen.getByText("예산은 120만원으로 잡았음")).toBeTruthy();

    // Readiness strip reflects the model.
    const meter = container.querySelector(".brain-readiness-meter i") as HTMLElement;
    expect(meter.style.width).toBe("60%");
    expect(container.querySelector(".brain-readiness-strip")?.getAttribute("data-state")).toBe("forming");

    // Every surface is a shortcut into the right depth.
    await userEvent.click(screen.getByRole("button", { name: "전체 그래프" }));
    expect(onOpenDepth).toHaveBeenLastCalledWith(5);
    await userEvent.click(featured as HTMLElement);
    expect(onOpenDepth).toHaveBeenLastCalledWith(2);
    await userEvent.click(screen.getByText("최근 기억").closest("button") as HTMLElement);
    expect(onOpenDepth).toHaveBeenLastCalledWith(2);
    await userEvent.click(screen.getByText("이전 기억").closest("button") as HTMLElement);
    expect(onOpenDepth).toHaveBeenLastCalledWith(2);
    await userEvent.click(screen.getByText("주요 주제").closest("button") as HTMLElement);
    expect(onOpenDepth).toHaveBeenLastCalledWith(3);
    await userEvent.click(screen.getByRole("button", { name: "주제 키우기" }));
    expect(onOpenDepth).toHaveBeenLastCalledWith(3); // readiness.depth
    await userEvent.click(screen.getByText("지난 여행 계획").closest("button") as HTMLElement);
    expect(onOpenDepth).toHaveBeenLastCalledWith(2);
  });

  it("shows honest empty columns and the waiting recall placeholder before any evidence exists", () => {
    const { container } = renderPanel({ proof: proofWith({}) });

    expect(screen.getByText("위임 결과가 생기면 여기에 강조됩니다.")).toBeTruthy();
    expect(screen.getByText("아직 최근 기억이 없습니다.")).toBeTruthy();
    expect(screen.getByText("대화가 쌓이면 과거 기억이 보입니다.")).toBeTruthy();
    expect(screen.getByText("주제가 형성되는 중입니다.")).toBeTruthy();
    expect(container.querySelector(".brain-overview-column.is-featured")).toBeNull();

    // No durable proof yet → empty context and the "no fabricated example" note.
    expect(screen.getByText("첫 기억 대기")).toBeTruthy();
    expect(screen.getByText("저장 후 증명됨")).toBeTruthy();
    expect(screen.getByText("첫 대화 후 여기에 다시 꺼낸 기억이 표시됩니다.")).toBeTruthy();
    expect(
      screen.getByText("아직 저장된 기억이 없어, 예시를 꾸며내는 대신 실제 기억이 쌓이기를 기다립니다."),
    ).toBeTruthy();
  });

  it("treats proven continuity without recall items as durable-but-indexing", () => {
    // hasDurableEvidence=false but modelContinuity.proven=true still counts as
    // durable proof (the || side), so the recall slot says "preparing".
    renderPanel({ proof: proofWith({ proven: true, keeps: true, activeModel: "MLX-4B" }) });
    expect(screen.getByText("12개 저장됨")).toBeTruthy();
    expect(screen.getByText("MLX-4B")).toBeTruthy();
    expect(screen.getByText("기억은 저장됐고, 다시 꺼낼 준비를 하고 있습니다.")).toBeTruthy();
    expect(screen.getByText("인덱싱이 끝나면 이 자리에서 실제 기억을 다시 보여줍니다.")).toBeTruthy();
  });

  it("keeps the model claim pending when continuity is proven but not claimed", () => {
    renderPanel({ proof: proofWith({ proven: true, keeps: false, activeModel: "MLX-4B" }) });
    expect(screen.getByText("저장 후 증명됨")).toBeTruthy();
    expect(screen.queryByText("MLX-4B")).toBeNull();
  });

  it("falls back to the no-model label when continuity is proven without a loaded model", () => {
    renderPanel({ proof: proofWith({ proven: true, keeps: true, activeModel: "" }) });
    expect(screen.getByText("모델 미로드")).toBeTruthy();
  });

  it("substitutes the recall query when the top item has no snippet", () => {
    renderPanel({ proof: proofWith({ durable: true, items: [recallItem("")] }) });
    expect(screen.getByText("지난 여행 계획")).toBeTruthy();
    expect(screen.getByText("여행 예산")).toBeTruthy();
  });
});
