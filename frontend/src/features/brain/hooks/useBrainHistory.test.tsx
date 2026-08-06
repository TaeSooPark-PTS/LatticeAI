import type * as React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { latticeApi } from "@/api/client";
import { t } from "@/i18n";
import { fail, ok, stubApi } from "@/test/renderPage";
import { useConversationSession } from "../conversationSession";
import { useBrainHistory } from "./useBrainHistory";

/**
 * Resuming, deleting and listing past conversations — the "지난 대화" surface.
 * The store is the real conversation session, so every assertion about what a
 * resume or delete did reads the same state the Brain home renders.
 */

function wrapper({ children }: { children: React.ReactNode }) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0, staleTime: 0 }, mutations: { retry: false } },
  });
  return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>;
}

function setup(api: Parameters<typeof stubApi>[0] = {}) {
  stubApi(api);
  const feedback: Array<string | null> = [];
  const setMemoryFeedback = ((value: unknown) => {
    feedback.push(typeof value === "function" ? "(updater)" : (value as string | null));
  }) as React.Dispatch<React.SetStateAction<string | null>>;
  const rendered = renderHook(() => useBrainHistory({ language: "ko", setMemoryFeedback }), { wrapper });
  return { ...rendered, feedback };
}

beforeEach(() => {
  useConversationSession.getState().resetConversation();
});

describe("useBrainHistory", () => {
  it("lists past conversations newest first from the history query", async () => {
    const { result } = setup({
      chatHistory: ok([
        { id: "old", title: "예전 대화", message_count: 2, updated_at: "2026-08-01T00:00:00Z" },
        { id: "new", title: "새 대화", message_count: 4, updated_at: "2026-08-05T00:00:00Z" },
      ]),
    });

    await waitFor(() => expect(result.current.pastConversations).toHaveLength(2));
    expect(result.current.pastConversations.map((conversation) => conversation.id)).toEqual(["new", "old"]);
    expect(result.current.historyBusyId).toBeNull();
  });

  it("resumes a conversation into the live session", async () => {
    const { result, feedback } = setup({
      conversation: ok({ messages: [
        { role: "user", content: "어제 뭐 했지?" },
        { role: "assistant", content: "메모를 정리했어요." },
      ] }),
    });

    await act(async () => {
      await result.current.resumeConversation("conv-1", false);
    });

    expect(useConversationSession.getState().conversationId).toBe("conv-1");
    expect(useConversationSession.getState().messages).toHaveLength(2);
    expect(feedback).toContain(t("ko", "brain.history.loading"));
    expect(feedback.at(-1)).toBe(t("ko", "brain.history.resumed"));
    expect(result.current.historyBusyId).toBeNull();
  });

  it("never resumes while an answer is still streaming", async () => {
    const { result } = setup();
    await act(async () => {
      await result.current.resumeConversation("conv-1", true);
    });
    expect(latticeApi.conversation).not.toHaveBeenCalled();
  });

  it("ignores a second request while the first is still loading", async () => {
    let release!: (value: unknown) => void;
    const { result } = setup({
      conversation: () => new Promise((resolve) => { release = resolve; }),
    });

    let first!: Promise<void>;
    act(() => {
      first = result.current.resumeConversation("conv-1", false);
    });
    await waitFor(() => expect(result.current.historyBusyId).toBe("conv-1"));

    await act(async () => {
      await result.current.resumeConversation("conv-2", false);
    });
    expect(latticeApi.conversation).toHaveBeenCalledTimes(1);

    await act(async () => {
      release(ok({ messages: [{ role: "user", content: "복원" }] }));
      await first;
    });
    expect(result.current.historyBusyId).toBeNull();
  });

  it("reports a failed load with the server's reason, or a plain fallback", async () => {
    const { result, feedback } = setup({ conversation: fail("사라진 대화", {}) });
    await act(async () => {
      await result.current.resumeConversation("conv-x", false);
    });
    expect(feedback.at(-1)).toBe(t("ko", "brain.history.loadFailed", { reason: "사라진 대화" }));
    expect(useConversationSession.getState().conversationId).toBeNull();

    stubApi({ conversation: { ok: false, status: 503, source: "unavailable", data: {} } });
    await act(async () => {
      await result.current.resumeConversation("conv-x", false);
    });
    expect(feedback.at(-1)).toBe(t("ko", "brain.history.loadFailed", { reason: "unavailable" }));
  });

  it("treats an empty conversation as unloadable rather than wiping the session", async () => {
    const { result, feedback } = setup({ conversation: ok({ messages: [] }) });
    act(() => {
      useConversationSession.getState().setMessages([{ role: "user", content: "지금 대화" }]);
    });

    await act(async () => {
      await result.current.resumeConversation("conv-empty", false);
    });

    expect(feedback.at(-1)).toBe(t("ko", "brain.history.loadFailed", {
      reason: t("ko", "brain.history.emptyConversation"),
    }));
    expect(useConversationSession.getState().messages).toHaveLength(1);
  });

  it("deleting the open conversation also resets the live session", async () => {
    const { result, feedback } = setup({ deleteConversation: ok({}) });
    act(() => {
      useConversationSession.getState().setConversationId("conv-open");
      useConversationSession.getState().setMessages([{ role: "user", content: "삭제될 대화" }]);
    });

    await act(async () => {
      await result.current.deleteConversation("conv-open");
    });

    expect(useConversationSession.getState().conversationId).toBeNull();
    expect(useConversationSession.getState().messages).toEqual([]);
    expect(feedback.at(-1)).toBe(t("ko", "brain.history.deleted"));
  });

  it("deleting some other conversation leaves the live session alone", async () => {
    const { result } = setup({ deleteConversation: ok({}) });
    act(() => {
      useConversationSession.getState().setConversationId("conv-keep");
      useConversationSession.getState().setMessages([{ role: "user", content: "이어지는 대화" }]);
    });

    await act(async () => {
      await result.current.deleteConversation("conv-другой");
    });

    expect(useConversationSession.getState().conversationId).toBe("conv-keep");
    expect(useConversationSession.getState().messages).toHaveLength(1);
  });

  it("reports a failed delete with the server's reason, or a plain fallback", async () => {
    const { result, feedback } = setup({ deleteConversation: fail("잠긴 대화", {}) });
    await act(async () => {
      await result.current.deleteConversation("conv-x");
    });
    expect(feedback.at(-1)).toBe(t("ko", "brain.history.deleteFailed", { reason: "잠긴 대화" }));

    stubApi({ deleteConversation: { ok: false, status: 503, source: "unavailable", data: {} } });
    await act(async () => {
      await result.current.deleteConversation("conv-x");
    });
    expect(feedback.at(-1)).toBe(t("ko", "brain.history.deleteFailed", { reason: "unavailable" }));
  });

  it("ignores a delete while another history action is running", async () => {
    let release!: (value: unknown) => void;
    const { result } = setup({
      deleteConversation: () => new Promise((resolve) => { release = resolve; }),
    });

    let first!: Promise<void>;
    act(() => {
      first = result.current.deleteConversation("conv-1");
    });
    await waitFor(() => expect(result.current.historyBusyId).toBe("conv-1"));

    await act(async () => {
      await result.current.deleteConversation("conv-2");
    });
    expect(latticeApi.deleteConversation).toHaveBeenCalledTimes(1);

    await act(async () => {
      release(ok({}));
      await first;
    });
    expect(result.current.historyBusyId).toBeNull();
  });

  it("exposes the session reset directly", () => {
    const { result } = setup();
    act(() => {
      useConversationSession.getState().setConversationId("conv-r");
      result.current.resetConversation();
    });
    expect(useConversationSession.getState().conversationId).toBeNull();
  });
});
