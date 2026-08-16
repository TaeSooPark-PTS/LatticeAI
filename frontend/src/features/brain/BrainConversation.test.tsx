import { act, fireEvent, screen, waitFor } from "@testing-library/react";
import * as React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { t } from "@/i18n";
import { renderPage } from "@/test/renderPage";
import {
  assistantMessage,
  emptyIngestionStates,
  makeBrief,
  makeConversations,
  makeGraph,
  makeProof,
  makeReadiness,
  userMessage,
} from "@/test/brainFixtures";
import type { Message, MessageProof } from "./types";
import { BrainConversation } from "./BrainConversation";

const citedProof: MessageProof = {
  query: "회의 정리",
  model: "test-model",
  provenAcrossModels: true,
  citations: [
    {
      id: "c1",
      source: "memory",
      title: "회의 메모",
      snippet: "결정 사항",
      matchedTerms: ["회의"],
      confidence: "high",
      score: 0.92,
      locator: "메모 > 회의",
    },
  ],
};

// One conversation that exercises every per-message surface at once.
function conversationMessages(): Message[] {
  return [
    userMessage("이거 기억해줘"),
    assistantMessage({
      content: "답변 A입니다.",
      proof: citedProof,
      contextQuality: { mode: "hybrid", nodes: 2, limited: true, reason: "인덱스가 아직 작습니다" },
      grounding: { status: "supported", reason: "근거 2건" },
      files: [{ path: "out/report.md", filename: "report.md", bytes: 2048 }],
      agentSteps: [],
      loopSummary: { repairs: { json: 2 }, parseErrors: 1, parseRecovered: 1, total: 3 },
      runExplanation: { code: "DONE", ok: true, headline: "요청을 끝까지 수행했습니다", details: ["검증 통과"], strainLevel: "none" },
    }),
    assistantMessage({
      content: "",
      approval: {
        runId: "run-1",
        token: "tok-1",
        expiresAt: new Date(Date.now() + 600_000).toISOString(),
        planSummary: "1. 파일을 만든다",
        plan: { goal: "파일 생성" },
        status: "pending",
      },
      agentState: "NEEDS_REVIEW",
    }),
    assistantMessage({
      content: "검토 결과입니다.",
      proof: { ...citedProof, citations: [] },
      contextQuality: { mode: "lexical_only", nodes: 0, limited: false, reason: null },
      grounding: { status: "unsupported", reason: null },
      agentSteps: [
        { phase: "plan", event: "planned" },
        { phase: "execute", event: "tool", action: "write_file", path: "a.md", step: 1, ok: true },
      ],
      agentState: "FAILED",
      files: [],
    }),
    assistantMessage({ content: "최종 답변입니다." }),
    userMessage("추가 질문"),
  ];
}

function renderConversation(
  overrides: Partial<React.ComponentProps<typeof BrainConversation>> = {},
  pageOptions: Parameters<typeof renderPage>[1] = {},
) {
  const props = {
    language: "ko" as const,
    brainState: "idle" as const,
    intensity: 0.6,
    modelName: "test-model",
    modelReady: true,
    messages: [] as Message[],
    pastConversations: makeConversations(1),
    historyBusyId: null,
    starterPrompts: ["오늘 브리핑", "내 일정 정리", "메모 검색"],
    memoryFeedback: null,
    ingestionStates: emptyIngestionStates,
    emergenceEvents: [],
    proactiveActivities: [],
    draft: "",
    streaming: false,
    imageData: null,
    streamRef: React.createRef<HTMLDivElement>(),
    memories: [],
    graph: makeGraph(),
    concepts: makeGraph().nodes,
    relationshipCount: 9,
    readiness: makeReadiness(),
    proof: makeProof(),
    brief: makeBrief(),
    uploadingDocument: false,
    onOpenDepth: vi.fn(),
    onDraftChange: vi.fn(),
    onImageDataChange: vi.fn(),
    onUploadDocument: vi.fn(),
    onPickFolder: vi.fn(),
    onConnectFolder: vi.fn(),
    onIngestNote: vi.fn(),
    onIngestWeb: vi.fn(),
    onVerifyModelContinuity: vi.fn(),
    onSend: vi.fn(),
    onSendText: vi.fn(),
    onCreateActionItem: vi.fn(),
    onProactiveAction: vi.fn(),
    onApprovalResolved: vi.fn(),
    onStop: vi.fn(),
    onRegenerate: vi.fn(),
    onNewConversation: vi.fn(),
    onResumeConversation: vi.fn(),
    onDeleteConversation: vi.fn(),
    onExploreBrain: vi.fn(),
    onRequestDetails: vi.fn(),
    ...overrides,
  };
  const view = renderPage(<BrainConversation {...props} />, pageOptions);
  return { ...view, props };
}

afterEach(() => {
  vi.useRealTimers();
  delete (navigator as { clipboard?: unknown }).clipboard;
  window.location.hash = "";
});

describe("BrainConversation empty home", () => {
  it("shows the station, dock and starter pills; a pill only fills the draft", () => {
    const { container, props } = renderConversation({ memoryFeedback: "기억했어요" });
    expect(screen.getByTestId("brain-home-stage")).toBeTruthy();
    expect(screen.getByTestId("brain-home-dock")).toBeTruthy();
    expect(container.querySelector(".brain-utility-drawer")).toBeNull();
    expect(screen.getByRole("status").textContent).toContain("기억했어요");

    const pill = container.querySelector(".brain-prompt-pill") as HTMLButtonElement;
    fireEvent.click(pill);
    expect(props.onDraftChange).toHaveBeenCalledWith("오늘 브리핑");
    expect(props.onSendText).not.toHaveBeenCalled();
  });

  it("sends a suggested question immediately and clears the draft", () => {
    const brief = makeBrief({
      suggestedQuestions: [
        {
          id: "start",
          labelKey: "brain.suggestion.start.label",
          detailKey: "brain.suggestion.start.detail",
          promptKey: "brain.suggestion.start.prompt",
          params: {},
          priority: 1,
        },
        {
          id: "focus",
          labelKey: "brain.suggestion.focus.label",
          detailKey: "brain.suggestion.focus.detail",
          promptKey: "brain.suggestion.focus.prompt",
          params: { focus: "프로젝트 계획" },
          priority: 2,
        },
      ],
    });
    const { container, props } = renderConversation({ brief });
    const grid = container.querySelector(".brain-prompt-grid") as HTMLElement;
    const buttons = grid.querySelectorAll("button");
    expect(buttons).toHaveLength(2);

    fireEvent.click(buttons[1]);
    expect(props.onDraftChange).toHaveBeenCalledWith("");
    expect(props.onSendText).toHaveBeenCalledWith(
      t("ko", "brain.suggestion.focus.prompt", { focus: "프로젝트 계획" }),
    );
  });

  it("disables suggested questions while streaming", () => {
    const brief = makeBrief({
      suggestedQuestions: [
        {
          id: "start",
          labelKey: "brain.suggestion.start.label",
          detailKey: "brain.suggestion.start.detail",
          promptKey: "brain.suggestion.start.prompt",
          params: {},
          priority: 1,
        },
      ],
    });
    const { container } = renderConversation({ brief, streaming: true });
    const button = container.querySelector(".brain-prompt-grid button") as HTMLButtonElement;
    expect(button).toBeDisabled();
  });

  it("pins the missing-model notice to the hero until a model is ready", () => {
    const { container, unmount } = renderConversation({ modelReady: false });
    expect(container.querySelector(".brain-hero-trailing .brain-model-missing")).toBeTruthy();
    unmount();

    const { container: ready } = renderConversation();
    expect(ready.querySelector(".brain-model-missing")).toBeNull();
  });
});

describe("BrainConversation with messages", () => {
  it("renders the header with model pill and score in advanced mode", () => {
    const { container, props } = renderConversation({ messages: conversationMessages() });
    const header = container.querySelector(".brain-chat-header") as HTMLElement;
    expect(header.querySelector(".brain-model-pill")?.textContent).toBe("test-model");
    expect(header.textContent).toContain("(62%)");

    fireEvent.click(screen.getByRole("button", { name: new RegExp(t("ko", "brain.newChat")) }));
    expect(props.onNewConversation).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button", { name: t("ko", "brain.firstScreen.action.graph") }));
    expect(props.onExploreBrain).toHaveBeenCalledTimes(1);
  });

  it("hides the pill and score in basic mode and softens the context note", () => {
    const { container } = renderConversation({ messages: conversationMessages() }, { mode: "basic" });
    const header = container.querySelector(".brain-chat-header") as HTMLElement;
    expect(header.querySelector(".brain-model-pill")).toBeNull();
    expect(header.textContent).not.toContain("(62%)");

    // Basic mode keeps the limited-context note but drops the technical reason.
    const note = screen.getByTestId("context-quality-note");
    expect(note.querySelector("small")).toBeNull();
  });

  it("shows citations, grounding, files, loop repairs, run explanation and agent states", () => {
    const { container } = renderConversation({ messages: conversationMessages() });

    // Inline citation markers only where citations exist.
    const bubbles = container.querySelectorAll(".brain-message-bubble");
    expect(bubbles[1].querySelector(".brain-inline-citations")).toBeTruthy();
    expect(bubbles[3].querySelector(".brain-inline-citations")).toBeNull();

    // Grounding: supported with a reason tooltip, unsupported without one.
    const badges = screen.getAllByTestId("grounding-badge");
    expect(badges).toHaveLength(2);
    expect(badges[0].className).toContain("is-supported");
    expect(badges[0]).toHaveAttribute("title", "근거 2건");
    expect(badges[0].textContent).toBe(t("ko", "brain.grounding.supported"));
    expect(badges[1].className).toContain("is-none");
    expect(badges[1]).not.toHaveAttribute("title");
    expect(badges[1].textContent).toBe(t("ko", "brain.grounding.none"));

    // Context quality: the limited note renders its reason in advanced mode.
    const note = screen.getByTestId("context-quality-note");
    expect(note.textContent).toContain(t("ko", "brain.contextQuality.limited"));
    expect(note.querySelector("small")?.textContent).toContain("인덱스가 아직 작습니다");

    // Created files card for the message that produced one.
    expect(container.textContent).toContain("report.md");

    // Agent terminal states: FAILED and NEEDS_REVIEW look different.
    const states = screen.getAllByTestId("agent-state-note");
    expect(states).toHaveLength(2);
    expect(states[0].className).toContain("is-review");
    expect(states[0].textContent).toBe(t("ko", "brain.agent.needsReview"));
    expect(states[1].className).toContain("is-failed");
    expect(states[1].textContent).toBe(t("ko", "brain.agent.failed"));

    // Loop repairs + run explanation notes surfaced for the first answer.
    expect(container.textContent).toContain("요청을 끝까지 수행했습니다");
  });

  it("resolves an inline approval through the paused run", async () => {
    const { props } = renderConversation({ messages: conversationMessages() });
    fireEvent.click(screen.getByTestId("approval-approve"));
    await waitFor(() => expect(props.onApprovalResolved).toHaveBeenCalledTimes(1));
    expect(props.onApprovalResolved).toHaveBeenCalledWith(2, expect.objectContaining({ kind: "finished" }));
  });

  it("copies an answer even without the clipboard API and resets the label", async () => {
    expect((navigator as { clipboard?: unknown }).clipboard).toBeUndefined();
    renderConversation({ messages: conversationMessages() });
    vi.useFakeTimers();

    const copyButtons = screen.getAllByRole("button", { name: t("ko", "brain.message.copyAria") });
    fireEvent.click(copyButtons[copyButtons.length - 1]);
    await act(async () => {
      await Promise.resolve();
    });
    expect(screen.getByText(t("ko", "brain.message.copied"))).toBeTruthy();

    act(() => {
      vi.advanceTimersByTime(2000);
    });
    expect(screen.queryByText(t("ko", "brain.message.copied"))).toBeNull();
  });

  it("writes the answer into a real clipboard when one exists", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { clipboard: { writeText } });
    renderConversation({ messages: conversationMessages() });

    const copyButtons = screen.getAllByRole("button", { name: t("ko", "brain.message.copyAria") });
    fireEvent.click(copyButtons[copyButtons.length - 1]);
    await waitFor(() => expect(writeText).toHaveBeenCalledWith("최종 답변입니다."));
  });

  it("swallows a clipboard failure without flipping the label", async () => {
    const writeText = vi.fn().mockRejectedValue(new Error("denied"));
    Object.assign(navigator, { clipboard: { writeText } });
    renderConversation({ messages: conversationMessages() });

    const copyButtons = screen.getAllByRole("button", { name: t("ko", "brain.message.copyAria") });
    fireEvent.click(copyButtons[0]);
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(writeText).toHaveBeenCalledWith("답변 A입니다.");
    expect(screen.queryByText(t("ko", "brain.message.copied"))).toBeNull();
  });

  it("offers regenerate, save-task and follow-ups only on the latest answer", () => {
    const { props } = renderConversation({ messages: conversationMessages() });

    // Three answers with content → three action rows; one regenerate/save.
    expect(screen.getAllByRole("button", { name: t("ko", "brain.message.copyAria") })).toHaveLength(3);
    const regenerate = screen.getAllByRole("button", { name: t("ko", "brain.message.regenerateAria") });
    expect(regenerate).toHaveLength(1);
    fireEvent.click(regenerate[0]);
    expect(props.onRegenerate).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByRole("button", { name: t("ko", "brain.message.saveTaskAria") }));
    expect(props.onCreateActionItem).toHaveBeenCalledWith("최종 답변입니다.");

    const followups = screen.getByLabelText(t("ko", "brain.message.followup.aria"));
    const buttons = followups.querySelectorAll("button");
    expect(buttons).toHaveLength(3);
    fireEvent.click(buttons[0]);
    expect(props.onSendText).toHaveBeenCalledWith(t("ko", "brain.message.followup.checklist.prompt"));
  });

  it("silences per-message actions while streaming and marks only the live timeline", () => {
    const streamingMessages: Message[] = [
      userMessage("진행해줘"),
      assistantMessage({ content: "이전 단계", agentSteps: [{ phase: "plan", event: "planned" }] }),
      assistantMessage({ content: "진행 중", agentSteps: [{ phase: "execute", event: "tool", action: "write_file" }] }),
    ];
    const { container } = renderConversation({ messages: streamingMessages, streaming: true });
    expect(screen.queryByRole("button", { name: t("ko", "brain.message.copyAria") })).toBeNull();
    expect(container.querySelectorAll(".brain-agent-steps, [data-testid='agent-step-timeline']").length).toBeGreaterThan(0);
    expect(container.querySelector(".brain-composer")).toHaveAttribute("aria-busy", "true");
  });

  it("shows the missing-model notice above the composer when the model is gone", () => {
    const { container } = renderConversation({ messages: conversationMessages(), modelReady: false });
    expect(container.querySelector(".brain-model-missing")).toBeTruthy();
  });

  it("badges only a cloud-answered reply, never a local one", () => {
    renderConversation({
      messages: [
        userMessage("로컬 질문"),
        assistantMessage({ content: "이 컴퓨터에서 답합니다." }),
        userMessage("클라우드 질문"),
        assistantMessage({
          content: "클라우드에서 답합니다.",
          cloudAnswer: {
            provider: "Antigravity",
            model: "gemini-3.7-flash",
            sentNodeCount: 2,
            expansion: { status: "staged", candidateCount: 1, stagedForReview: true },
          },
        }),
      ],
    });

    const chips = screen.getAllByTestId("cloud-answer-chip");
    expect(chips).toHaveLength(1);
    expect(chips[0].textContent).toContain(t("ko", "brain.cloud.chip.model", { model: "gemini-3.7-flash" }));
    expect(screen.queryByText("이 컴퓨터에서 답합니다.")).toBeTruthy();
  });
});

describe("BrainConversation utility drawer", () => {
  function openDrawer(container: HTMLElement) {
    const details = container.querySelector(".brain-utility-drawer") as HTMLDetailsElement;
    act(() => {
      details.open = true;
      fireEvent(details, new Event("toggle"));
    });
    return details;
  }

  it("prefetches details on open, not on close, and links to admin in advanced mode", async () => {
    const { container, props } = renderConversation({ messages: conversationMessages() });
    const details = openDrawer(container);
    expect(props.onRequestDetails).toHaveBeenCalledTimes(1);

    // Advanced surface: model pill, admin link and the deep panels.
    const tools = container.querySelector(".brain-utility-tools") as HTMLElement;
    expect(tools.querySelector(".brain-model-pill")?.textContent).toBe("test-model");
    expect(screen.getByLabelText(t("ko", "brain.modelDemo.aria"))).toBeTruthy();
    expect(screen.getByLabelText(t("ko", "brain.aria.overview"))).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: new RegExp(t("ko", "brain.admin")) }));
    expect(window.location.hash).toBe("#/admin");

    act(() => {
      details.open = false;
      fireEvent(details, new Event("toggle"));
    });
    expect(props.onRequestDetails).toHaveBeenCalledTimes(1);
    await waitFor(() => expect(details.open).toBe(false));
  });

  it("keeps the basic drawer plain: no admin, no model internals", () => {
    const { container } = renderConversation({ messages: conversationMessages() }, { mode: "basic" });
    expect(container.querySelector(".brain-utility-drawer > summary")?.textContent).toBe(
      t("ko", "brain.chatHome.utility.basic"),
    );
    openDrawer(container);
    const tools = container.querySelector(".brain-utility-tools") as HTMLElement;
    expect(tools.querySelector(".brain-model-pill")).toBeNull();
    expect(screen.queryByRole("button", { name: new RegExp(t("ko", "brain.admin")) })).toBeNull();
    expect(screen.queryByLabelText(t("ko", "brain.modelDemo.aria"))).toBeNull();
    expect(screen.queryByLabelText(t("ko", "brain.aria.overview"))).toBeNull();
  });
});
