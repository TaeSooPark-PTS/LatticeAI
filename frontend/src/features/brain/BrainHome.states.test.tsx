import { fireEvent, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { t } from "@/i18n";
import { renderPage } from "@/test/renderPage";
import { assistantMessage, makeBrief, makeProof, userMessage } from "@/test/brainFixtures";
import type { IngestionState } from "./types";
import { useBrainChat } from "./hooks/useBrainChat";
import { useBrainHistory } from "./hooks/useBrainHistory";
import { useBrainIngestion } from "./hooks/useBrainIngestion";
import { useBrainProof } from "./hooks/useBrainProof";
import { BrainHome } from "./BrainHome";

/**
 * BrainHome's own logic — the visible-state ladder, the drop overlay, depth
 * routing and conversation reset — sits above four hooks that each need a
 * server conversation to reach their interesting states. The hooks have their
 * own suites; here they are mocked so every rung of the ladder is a plain
 * input instead of a scripted stream.
 */
vi.mock("./hooks/useBrainChat");
vi.mock("./hooks/useBrainHistory");
vi.mock("./hooks/useBrainIngestion");
vi.mock("./hooks/useBrainProof");

type ChatApi = ReturnType<typeof useBrainChat>;
type IngestionApi = ReturnType<typeof useBrainIngestion>;
type ProofApi = ReturnType<typeof useBrainProof>;
type HistoryApi = ReturnType<typeof useBrainHistory>;

function chatApi(overrides: Partial<ChatApi> = {}): ChatApi {
  return {
    messages: [],
    conversationId: null,
    draft: "",
    setDraft: vi.fn(),
    imageData: null,
    setImageData: vi.fn(),
    streaming: false,
    streamRef: { current: null },
    proactiveActivities: [],
    send: vi.fn(),
    sendText: vi.fn(),
    regenerate: vi.fn(),
    createActionItem: vi.fn(),
    handleProactiveAction: vi.fn(),
    handleApprovalResolved: vi.fn(),
    stopStreaming: vi.fn(),
    ...overrides,
  } as unknown as ChatApi;
}

function ingestionApi(overrides: Partial<IngestionApi> = {}): IngestionApi {
  return {
    ingestionStates: { chat: null, file: null, folder: null, note: null, web: null },
    emergenceEvents: [],
    synthesisActive: false,
    uploadingDocument: false,
    beginIngestion: vi.fn(),
    completeIngestion: vi.fn().mockResolvedValue({ memories: 0, entities: 0 }),
    failIngestion: vi.fn(),
    uploadDocument: vi.fn().mockResolvedValue(undefined),
    pickFolder: vi.fn().mockResolvedValue(undefined),
    connectFolder: vi.fn().mockResolvedValue(undefined),
    ingestNote: vi.fn().mockResolvedValue(undefined),
    ingestWeb: vi.fn().mockResolvedValue(undefined),
    resetChatIngestion: vi.fn(),
    ...overrides,
  } as unknown as IngestionApi;
}

function proofApi(overrides: Partial<ProofApi> = {}): ProofApi {
  return {
    brainProof: makeProof(),
    brainBrief: makeBrief(),
    lastRecallQuery: "",
    setLastRecallQuery: vi.fn(),
    requestDetails: vi.fn(),
    attachAnswerProof: vi.fn().mockResolvedValue(true),
    verifyModelContinuity: vi.fn().mockResolvedValue(undefined),
    ...overrides,
  } as unknown as ProofApi;
}

function historyApi(overrides: Partial<HistoryApi> = {}): HistoryApi {
  return {
    historyQ: { data: undefined },
    pastConversations: [],
    historyBusyId: null,
    resetConversation: vi.fn(),
    resumeConversation: vi.fn().mockResolvedValue(undefined),
    deleteConversation: vi.fn().mockResolvedValue(undefined),
    ...overrides,
  } as unknown as HistoryApi;
}

function arm({
  chat = {},
  ingestion = {},
  proof = {},
  history = {},
}: {
  chat?: Partial<ChatApi>;
  ingestion?: Partial<IngestionApi>;
  proof?: Partial<ProofApi>;
  history?: Partial<HistoryApi>;
} = {}) {
  const api = {
    chat: chatApi(chat),
    ingestion: ingestionApi(ingestion),
    proof: proofApi(proof),
    history: historyApi(history),
  };
  vi.mocked(useBrainChat).mockReturnValue(api.chat);
  vi.mocked(useBrainIngestion).mockReturnValue(api.ingestion);
  vi.mocked(useBrainProof).mockReturnValue(api.proof);
  vi.mocked(useBrainHistory).mockReturnValue(api.history);
  return api;
}

function renderHome(brainState: Parameters<typeof BrainHome>[0]["brainState"] = "idle") {
  const onBrainChange = vi.fn();
  const view = renderPage(<BrainHome brainState={brainState} intensity={0.6} onBrainChange={onBrainChange} />);
  return { ...view, onBrainChange };
}

const orbState = () => screen.getByTestId("living-brain").getAttribute("data-state");
const ingestState = (stage: IngestionState["stage"]): IngestionState => ({
  sourceType: "file",
  label: "보고서.pdf",
  stage,
  startedAt: 1,
  completedAt: null,
  newMemories: 0,
  newEntities: 0,
});

describe("BrainHome visible state ladder", () => {
  it("rests idle when nothing is happening", () => {
    arm();
    renderHome();
    expect(orbState()).toBe("idle");
  });

  it("listens as soon as the draft has content", () => {
    arm({ chat: { draft: "안녕, 오늘 일정" } });
    renderHome();
    expect(orbState()).toBe("listening");
  });

  it("thinks while streaming, but keeps a recall visual when recall started it", () => {
    arm({ chat: { streaming: true } });
    const { unmount } = renderHome("idle");
    expect(orbState()).toBe("thinking");
    unmount();

    arm({ chat: { streaming: true } });
    renderHome("recalling");
    expect(orbState()).toBe("recalling");
  });

  it("recalls while any capture pipeline is still moving", () => {
    arm({
      ingestion: {
        ingestionStates: {
          chat: null,
          file: ingestState("complete"),
          folder: ingestState("error"),
          note: ingestState("parsing"),
          web: null,
        },
      },
    });
    renderHome();
    expect(orbState()).toBe("recalling");
  });

  it("synthesizes when new memories are still settling", () => {
    arm({ ingestion: { synthesisActive: true } });
    renderHome();
    expect(orbState()).toBe("synthesizing");
  });

  it("acts while a delegated automation is running", () => {
    arm({
      chat: {
        proactiveActivities: [
          { id: "a1", actionId: "x", labelKey: "brain.brief.action.ask", intent: "ask", status: "completed", startedAt: 1 },
          { id: "a2", actionId: "y", labelKey: "brain.brief.action.ask", intent: "delegate", status: "running", startedAt: 2 },
        ],
      },
    });
    renderHome();
    expect(orbState()).toBe("acting");
  });

  it("breathes harder the busier it gets", () => {
    const scale = () =>
      parseFloat(screen.getByTestId("living-brain").style.getPropertyValue("--brain-scale"));

    arm();
    const idle = renderHome();
    const idleScale = scale();
    idle.unmount();

    arm({ chat: { draft: "메모" } });
    const listening = renderHome();
    const listeningScale = scale();
    listening.unmount();

    arm({ chat: { streaming: true } });
    renderHome();
    const busyScale = scale();

    expect(listeningScale).toBeGreaterThan(idleScale);
    expect(busyScale).toBeGreaterThan(listeningScale);
  });
});

describe("BrainHome global file drop", () => {
  function dragEvent(type: string, files: File[] = []) {
    const event = new Event(type, { bubbles: true });
    Object.assign(event, { dataTransfer: { types: ["Files"], files } });
    return event;
  }

  it("lights the overlay during a file drag and routes up to five dropped files to capture", async () => {
    const api = arm();
    renderHome();
    expect(screen.queryByTestId("brain-drop-overlay")).toBeNull();

    fireEvent(window, dragEvent("dragenter"));
    expect(screen.getByTestId("brain-drop-overlay")).toBeTruthy();
    expect(screen.getByText(t("ko", "brain.dnd.title"))).toBeTruthy();

    const files = Array.from({ length: 6 }, (_, index) => new File(["x"], `f${index}.txt`, { type: "text/plain" }));
    fireEvent(window, dragEvent("drop", files));
    expect(screen.queryByTestId("brain-drop-overlay")).toBeNull();

    await waitFor(() => expect(api.ingestion.uploadDocument).toHaveBeenCalledTimes(5));
    // File objects compare structurally equal, so assert order by name.
    const uploaded = vi.mocked(api.ingestion.uploadDocument).mock.calls.map((call) => (call[0] as File).name);
    expect(uploaded).toEqual(["f0.txt", "f1.txt", "f2.txt", "f3.txt", "f4.txt"]);
  });
});

describe("BrainHome depth routing", () => {
  it("opens the knowledge graph for deep exploration", () => {
    arm();
    const { onBrainChange } = renderHome();
    fireEvent.click(screen.getByTestId("living-brain"));
    expect(window.location.hash).toBe("#/knowledge-graph");
    expect(onBrainChange).toHaveBeenCalledWith("recalling", 0.82);
    window.location.hash = "";
  });

  it("opens the memory view for shallow depths", async () => {
    arm();
    renderHome();
    fireEvent.click(screen.getByTestId("brain-dock-map"));
    await screen.findByTestId("brain-home-drawer");

    fireEvent.click(document.querySelector(".ring-label") as HTMLElement);
    fireEvent.click(document.querySelector(".brain-ring-peek-deeper") as HTMLElement);
    expect(window.location.hash).toBe("#/memory");
    window.location.hash = "";
  });
});

describe("BrainHome new conversation", () => {
  const messages = [userMessage(), assistantMessage()];

  it("stops the stream first when one is running", () => {
    const api = arm({ chat: { streaming: true, messages } });
    const { onBrainChange } = renderHome();
    fireEvent.click(screen.getByRole("button", { name: new RegExp(t("ko", "brain.newChat")) }));
    expect(api.chat.stopStreaming).toHaveBeenCalledTimes(1);
    expect(api.history.resetConversation).toHaveBeenCalledTimes(1);
    expect(api.ingestion.resetChatIngestion).toHaveBeenCalledTimes(1);
    expect(onBrainChange).toHaveBeenCalledWith("idle", 0.58);
  });

  it("skips the stop when nothing is streaming", () => {
    const api = arm({ chat: { messages } });
    renderHome();
    fireEvent.click(screen.getByRole("button", { name: new RegExp(t("ko", "brain.newChat")) }));
    expect(api.chat.stopStreaming).not.toHaveBeenCalled();
    expect(api.history.resetConversation).toHaveBeenCalledTimes(1);
  });
});

describe("BrainHome wiring", () => {
  it("sends the draft from the composer", () => {
    const api = arm({ chat: { draft: "질문" } });
    renderHome();
    fireEvent.keyDown(screen.getByPlaceholderText(t("ko", "brain.placeholder")), { key: "Enter" });
    expect(api.chat.send).toHaveBeenCalledTimes(1);
  });

  it("routes every capture control to its ingestion call", () => {
    const api = arm();
    const { container } = renderHome();
    fireEvent.click(screen.getByTestId("brain-attach-toggle"));

    const documentInput = container.querySelector(".brain-document-input input") as HTMLInputElement;
    const file = new File(["문서"], "notes.md", { type: "text/markdown" });
    fireEvent.change(documentInput, { target: { files: [file] } });
    expect(api.ingestion.uploadDocument).toHaveBeenCalledWith(file);

    fireEvent.click(screen.getByRole("button", { name: t("ko", "brain.ingest.type.folder") }));
    fireEvent.click(screen.getByRole("button", { name: new RegExp(t("ko", "capture.local.choose")) }));
    expect(api.ingestion.pickFolder).toHaveBeenCalledTimes(1);
    const folderInput = screen.getByLabelText(t("ko", "brain.ingest.folder.placeholder"));
    fireEvent.change(folderInput, { target: { value: "~/docs" } });
    fireEvent.submit(folderInput.closest("form") as HTMLFormElement);
    expect(api.ingestion.connectFolder).toHaveBeenCalledWith("~/docs");

    fireEvent.click(screen.getByRole("button", { name: t("ko", "brain.ingest.type.note") }));
    const noteInput = screen.getByLabelText(t("ko", "brain.ingest.note.placeholder"));
    fireEvent.change(noteInput, { target: { value: "오늘 배운 것" } });
    fireEvent.submit(noteInput.closest("form") as HTMLFormElement);
    expect(api.ingestion.ingestNote).toHaveBeenCalledWith("오늘 배운 것");

    fireEvent.click(screen.getByRole("button", { name: t("ko", "brain.ingest.type.web") }));
    const webInput = screen.getByLabelText(t("ko", "brain.ingest.web.placeholder"));
    fireEvent.change(webInput, { target: { value: "https://example.com" } });
    fireEvent.submit(webInput.closest("form") as HTMLFormElement);
    expect(api.ingestion.ingestWeb).toHaveBeenCalledWith("https://example.com");
  });

  it("sends a suggested question and clears the draft", () => {
    const api = arm({
      proof: {
        brainBrief: makeBrief({
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
        }),
      },
    });
    const { container } = renderHome();
    fireEvent.click(container.querySelector(".brain-prompt-grid button") as HTMLElement);
    expect(api.chat.setDraft).toHaveBeenCalledWith("");
    expect(api.chat.sendText).toHaveBeenCalledWith(t("ko", "brain.suggestion.start.prompt"));
  });

  it("resumes and deletes past conversations from the dock", async () => {
    const api = arm({
      history: { pastConversations: [{ id: "c1", title: "지난 대화", messageCount: 3 }] },
    });
    renderHome();
    fireEvent.click(screen.getByTestId("brain-dock-conversations"));
    await screen.findByTestId("brain-home-drawer");

    fireEvent.click(screen.getByRole("button", { name: t("ko", "brain.history.resumeAria", { title: "지난 대화" }) }));
    expect(api.history.resumeConversation).toHaveBeenCalledWith("c1", false);

    const del = screen.getByRole("button", { name: t("ko", "brain.history.deleteAria", { title: "지난 대화" }) });
    fireEvent.click(del);
    fireEvent.click(del);
    expect(api.history.deleteConversation).toHaveBeenCalledWith("c1");
  });

  it("verifies model continuity from the stats drawer", async () => {
    const api = arm();
    renderHome();
    fireEvent.click(screen.getByTestId("brain-dock-stats"));
    await screen.findByTestId("brain-home-drawer");
    fireEvent.click(screen.getByRole("button", { name: new RegExp(t("ko", "brain.brief.action.verify")) }));
    expect(api.proof.verifyModelContinuity).toHaveBeenCalledTimes(1);
  });

  it("wires answer actions and proactive automation in a conversation", () => {
    const api = arm({
      chat: { messages: [userMessage(), assistantMessage({ content: "정리 답변" })] },
      proof: {
        brainBrief: makeBrief({
          proactiveActions: [
            {
              id: "pa1",
              intent: "ask",
              labelKey: "brain.brief.action.ask",
              detailKey: "brain.brief.action.ask.detail",
              prompt: "물어보기",
              route: "",
              priority: 1,
              context: {},
            },
          ],
        }),
      },
    });
    renderHome();

    fireEvent.click(screen.getByRole("button", { name: t("ko", "brain.message.regenerateAria") }));
    expect(api.chat.regenerate).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByRole("button", { name: t("ko", "brain.message.saveTaskAria") }));
    expect(api.chat.createActionItem).toHaveBeenCalledWith("정리 답변");

    const followups = screen.getByLabelText(t("ko", "brain.message.followup.aria"));
    fireEvent.click(followups.querySelector("button") as HTMLElement);
    expect(api.chat.sendText).toHaveBeenCalledWith(t("ko", "brain.message.followup.checklist.prompt"));

    const automation = document.querySelector(".brain-automation-actions button") as HTMLElement;
    fireEvent.click(automation);
    expect(api.chat.handleProactiveAction).toHaveBeenCalledTimes(1);
  });
});
