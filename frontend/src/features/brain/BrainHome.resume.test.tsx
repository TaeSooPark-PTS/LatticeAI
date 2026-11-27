import { screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { latticeApi } from "@/api/client";
import { ok, renderPage } from "@/test/renderPage";
import { useAppStore } from "@/store/appStore";
import { BrainHome } from "./BrainHome";
import { useConversationSession } from "./conversationSession";

beforeEach(() => {
  vi.restoreAllMocks();
  useConversationSession.getState().resetConversation();
  useAppStore.setState({ language: "ko", pendingConversationId: null });
});

describe("BrainHome pending conversation", () => {
  it("loads each conversation another screen asks for, once", async () => {
    useAppStore.getState().setPendingConversationId("c7");
    renderPage(<BrainHome brainState="idle" intensity={0.6} onBrainChange={() => {}} />);
    const load = vi.spyOn(latticeApi, "conversation").mockResolvedValue(
      ok({ messages: [{ role: "user", content: "예산 회의 내용" }] }) as never,
    );
    useAppStore.getState().setPendingConversationId("c8");

    expect(await screen.findByText("예산 회의 내용")).toBeTruthy();
    await waitFor(() => expect(useConversationSession.getState().conversationId).toBe("c8"));
    expect(load.mock.calls).toEqual([["c7"], ["c8"]]);
    expect(useAppStore.getState().pendingConversationId).toBeNull();
  });
});
