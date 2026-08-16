import { act, fireEvent, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { t } from "@/i18n";
import { ok, renderPage } from "@/test/renderPage";
import { makeBrief, makeConversations, makeGraph, makeProof, makeReadiness } from "@/test/brainFixtures";
import type { MemoryFragment } from "./types";
import { BrainHomeDock } from "./BrainHomeDock";

const memories: MemoryFragment[] = [
  { id: "m1", title: "지금 나눈 대화", kind: "Conversation", tags: [], agentGenerated: false },
  { id: "m2", title: "회의 메모", kind: "Note", tags: [], agentGenerated: false },
];

/** A minimal server-rendered switchboard, so the 기능 drawer has content. */
const FEATURES = {
  note: "모두 지금 바로 적용됩니다.",
  features: [
    {
      id: "allow_multimodal", kind: "toggle", label: "사진·녹음도 기억하기",
      summary: "폴더를 읽을 때 사진과 녹음도 함께 저장합니다.", default: false,
      current: false, source: "default", env_var: "LATTICEAI_ALLOW_MULTIMODAL",
      live: true, restart_required: false, caution: null, parent: null, choices: [],
    },
    {
      id: "vector_backend", kind: "choice", label: "의미 검색 방식",
      summary: "빠르기와 정확함 사이에서 고릅니다.", default: "brute",
      current: "brute", source: "default", env_var: "LATTICEAI_VECTOR_INDEX",
      live: true, restart_required: false, caution: null, parent: null,
      choices: [
        { id: "brute", label: "전부 비교", available: true, detail: null },
        { id: "hnsw", label: "근사 검색", available: false, detail: "설치 필요 — hnswlib 없음" },
      ],
    },
  ],
};

function renderDock(
  overrides: Partial<React.ComponentProps<typeof BrainHomeDock>> = {},
  pageOptions: Parameters<typeof renderPage>[1] = {},
) {
  const props = {
    language: "ko" as const,
    brainState: "idle" as const,
    intensity: 0.6,
    readiness: makeReadiness(),
    memories,
    concepts: makeGraph().nodes,
    relationshipCount: 9,
    emergenceEvents: [],
    proactiveActivities: [],
    pastConversations: makeConversations(2),
    historyBusyId: null,
    streaming: false,
    modelName: "test-model",
    proof: makeProof(),
    brief: makeBrief(),
    onOpenDepth: vi.fn(),
    onExploreBrain: vi.fn(),
    onVerifyModelContinuity: vi.fn(),
    onProactiveAction: vi.fn(),
    onResumeConversation: vi.fn(),
    onDeleteConversation: vi.fn(),
    onRequestDetails: vi.fn(),
    ...overrides,
  };
  const view = renderPage(<BrainHomeDock {...props} />, {
    ...pageOptions,
    api: { features: () => Promise.resolve(ok(FEATURES)), ...(pageOptions.api || {}) },
  });
  return { ...view, props };
}

const drawer = () => screen.queryByTestId("brain-home-drawer");
const rail = (id: string) => screen.getByTestId(`brain-dock-${id}`);

describe("BrainHomeDock rail", () => {
  it("shows the four tabs closed, with a count only when history exists", () => {
    const { unmount } = renderDock();
    for (const id of ["conversations", "stats", "map", "features"]) {
      expect(rail(id)).toHaveAttribute("aria-expanded", "false");
    }
    expect(rail("conversations")).toHaveClass("is-continuity");
    expect(rail("stats")).not.toHaveClass("is-continuity");
    expect(rail("conversations").querySelector(".brain-dock-count")?.textContent).toBe("2");
    expect(drawer()).toBeNull();
    unmount();

    renderDock({ pastConversations: [] });
    expect(rail("conversations").querySelector(".brain-dock-count")).toBeNull();
  });

  it("opens conversations without prefetching details, and closes on the same tab", async () => {
    const { props } = renderDock();
    fireEvent.click(rail("conversations"));
    expect(await screen.findByTestId("brain-home-drawer")).toBeTruthy();
    expect(props.onRequestDetails).not.toHaveBeenCalled();
    expect(rail("conversations")).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByRole("button", { name: t("ko", "brain.history.resumeAria", { title: "대화 1" }) })).toBeTruthy();

    fireEvent.click(rail("conversations"));
    expect(drawer()).toBeNull();
    expect(rail("conversations")).toHaveAttribute("aria-expanded", "false");
  });

  it("keeps the drawer open when a double tap races the first open", async () => {
    renderDock();
    const button = rail("conversations");
    // Two taps land before the first re-render: the second must not undo the
    // first. Native clicks inside one act share the pre-open handler closure.
    act(() => {
      button.click();
      button.click();
    });
    expect(await screen.findByTestId("brain-home-drawer")).toBeTruthy();
  });

  it("prefetches details for stats and map, and not for the switchboard", async () => {
    const { props } = renderDock();
    fireEvent.click(rail("stats"));
    await screen.findByTestId("brain-home-drawer");
    expect(props.onRequestDetails).toHaveBeenCalledTimes(1);

    fireEvent.click(rail("map"));
    await waitFor(() => expect(props.onRequestDetails).toHaveBeenCalledTimes(2));

    // 기능 reads /api/features and nothing else: pulling the whole proof
    // bundle for it would be work nobody asked for.
    fireEvent.click(rail("features"));
    await screen.findByTestId("feature-row-allow_multimodal");
    expect(props.onRequestDetails).toHaveBeenCalledTimes(2);
  });

  it("opens the switchboard drawer with the server's switches in it", async () => {
    renderDock();
    fireEvent.click(rail("features"));

    await screen.findByTestId("brain-home-drawer");
    expect(screen.getByLabelText(t("ko", "brain.features.aria"))).toBeTruthy();
    expect(await screen.findByTestId("feature-switch-allow_multimodal")).toHaveAttribute(
      "aria-checked",
      "false",
    );
    expect(screen.getByTestId("feature-choice-vector_backend-hnsw")).toBeDisabled();
  });
});

describe("BrainHomeDock drawer", () => {
  it("switches tabs from inside, marks the active one, and closes from the header", async () => {
    renderDock();
    fireEvent.click(rail("conversations"));
    await screen.findByTestId("brain-home-drawer");

    const statsTab = screen.getByTestId("brain-drawer-tab-stats");
    expect(screen.getByTestId("brain-drawer-tab-conversations")).toHaveAttribute("aria-pressed", "true");
    expect(statsTab).toHaveAttribute("aria-pressed", "false");

    fireEvent.click(statsTab);
    await waitFor(() => expect(statsTab).toHaveAttribute("aria-pressed", "true"));
    // Stats content: the brief panel plus the deep panels in advanced mode.
    expect(screen.getByLabelText(t("ko", "brain.brief.aria"))).toBeTruthy();
    expect(screen.getByLabelText(t("ko", "brain.modelDemo.aria"))).toBeTruthy();
    expect(screen.getByLabelText(t("ko", "brain.aria.overview"))).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: t("ko", "brain.home.shelf.close") }));
    expect(drawer()).toBeNull();
  });

  it("dismisses on the scrim", async () => {
    const { baseElement } = renderDock();
    fireEvent.click(rail("map"));
    await screen.findByTestId("brain-home-drawer");
    fireEvent.click(baseElement.querySelector(".brain-home-dock-scrim") as HTMLElement);
    expect(drawer()).toBeNull();
  });

  it("hides the deep panels in basic mode and keeps them in advanced", async () => {
    renderDock({}, { mode: "basic" });
    fireEvent.click(rail("stats"));
    await screen.findByTestId("brain-home-drawer");
    expect(screen.getByLabelText(t("ko", "brain.brief.aria"))).toBeTruthy();
    expect(screen.queryByLabelText(t("ko", "brain.modelDemo.aria"))).toBeNull();
    expect(screen.queryByLabelText(t("ko", "brain.aria.overview"))).toBeNull();
    // Basic mode also hides the brief's evidence strip.
    expect(document.querySelector(".brain-brief-evidence")).toBeNull();
  });

  it("routes brief actions through the shared handler", async () => {
    const { props } = renderDock();
    fireEvent.click(rail("stats"));
    await screen.findByTestId("brain-home-drawer");
    fireEvent.click(screen.getByRole("button", { name: new RegExp(t("ko", "brain.brief.action.verify")) }));
    expect(props.onVerifyModelContinuity).toHaveBeenCalledTimes(1);
  });

  it("wires history resume and the map CTA", async () => {
    const { props } = renderDock();
    fireEvent.click(rail("conversations"));
    await screen.findByTestId("brain-home-drawer");
    fireEvent.click(screen.getByRole("button", { name: t("ko", "brain.history.resumeAria", { title: "대화 1" }) }));
    expect(props.onResumeConversation).toHaveBeenCalledWith("conv-1");

    fireEvent.click(screen.getByTestId("brain-drawer-tab-map"));
    const mapCta = await waitFor(() => {
      const cta = document.querySelector(".brain-home-drawer-map-cta") as HTMLElement;
      expect(cta).toBeTruthy();
      return cta;
    });
    fireEvent.click(mapCta);
    expect(props.onExploreBrain).toHaveBeenCalledTimes(1);
  });

  it("lets Escape close the innermost surface first: ring peek, then drawer", async () => {
    renderDock();
    fireEvent.click(rail("map"));
    const aside = await screen.findByTestId("brain-home-drawer");

    // Open a memory-ring peek inside the map tab.
    const ringLabel = document.querySelector(".ring-label") as HTMLButtonElement;
    fireEvent.click(ringLabel);
    expect(document.getElementById("brain-ring-peek")).toBeTruthy();

    fireEvent.keyDown(aside, { key: "Escape" });
    expect(document.getElementById("brain-ring-peek")).toBeNull();
    expect(drawer()).toBeTruthy();

    fireEvent.keyDown(aside, { key: "Escape" });
    expect(drawer()).toBeNull();
  });
});
