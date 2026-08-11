import { act } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { latticeApi } from "@/api/client";
import { t } from "@/i18n";
import { fakeChatStream } from "@/test/fakeChatStream";
import { fail, ok } from "@/test/renderPage";
import { setup } from "@/test/brainChatHarness";
import { useConversationSession } from "../conversationSession";
import type { BrainProactiveAction, MessageApproval } from "../types";

/**
 * What the person can do with an answer once it exists: resolve a governed
 * plan, save a follow-up, or take one of the Brain's proactive offers.
 */

beforeEach(() => {
  useConversationSession.getState().resetConversation();
  vi.useFakeTimers({ shouldAdvanceTime: true });
});

afterEach(() => {
  vi.useRealTimers();
});

describe("handleApprovalResolved", () => {
  function seedApproval(message: Partial<ReturnType<typeof useConversationSession.getState>["messages"][number]> = {}) {
    const approval: MessageApproval = {
      runId: "run-9", token: "tok-9", expiresAt: "", planSummary: "계획", plan: null, status: "pending",
    };
    act(() => {
      useConversationSession.getState().setMessages([
        { role: "user", content: "정리해줘" },
        { role: "assistant", content: "승인 대기 중", approval, ...message },
      ]);
    });
    return approval;
  }

  it("a finished resume merges exactly like a normal agent completion", () => {
    const { result } = setup();
    seedApproval();

    act(() => {
      result.current.handleApprovalResolved(1, { kind: "finished", payload: {
        response: "  정리 끝  ",
        created_files: [{ path: "docs/정리.md", filename: "정리.md", bytes: 12 }],
        final_state: "NEEDS_REVIEW",
        loop: { repairs: { retry: 2 } },
        explanation: { code: "needs_review", ok: false, headline: { ko: "확인 필요", en: "Needs review" }, details: [] },
        steps: [{ action: "write_file", args: { path: "docs/정리.md" } }],
      } });
    });

    const reply = result.current.messages[1];
    expect(reply.content).toBe("정리 끝");
    expect(reply.files?.[0].filename).toBe("정리.md");
    expect(reply.agentState).toBe("NEEDS_REVIEW");
    expect(reply.loopSummary?.total).toBe(2);
    expect(reply.runExplanation?.headline).toBe("확인 필요");
    expect(reply.agentSteps).toHaveLength(1);
    expect(reply.approval?.status).toBe("approved");
  });

  it("a finished resume without a response keeps the original text and streamed steps", () => {
    const { result } = setup();
    seedApproval({ content: "이미 흐른 답", agentSteps: [{ phase: "plan", event: "planned" }] });

    act(() => {
      result.current.handleApprovalResolved(1, { kind: "finished", payload: {} });
    });

    const reply = result.current.messages[1];
    expect(reply.content).toBe("이미 흐른 답");
    expect(reply.agentSteps).toEqual([{ phase: "plan", event: "planned" }]);
    expect(reply.files).toBeUndefined();
    expect(reply.agentState).toBeUndefined();
    expect(reply.approval?.status).toBe("approved");
  });

  it("a finished resume with no steps at all leaves the timeline absent", () => {
    const { result } = setup();
    seedApproval();

    act(() => {
      result.current.handleApprovalResolved(1, { kind: "finished", payload: { response: "그냥 끝" } });
    });

    const reply = result.current.messages[1];
    expect(reply.content).toBe("그냥 끝");
    expect(reply.agentSteps).toBeUndefined();
    expect(reply.approval?.status).toBe("approved");
  });

  it("cancel, error and expiry settle on the card without touching the answer", () => {
    const { result } = setup();

    seedApproval();
    act(() => {
      result.current.handleApprovalResolved(1, { kind: "error", reason: "토큰 불일치" });
    });
    expect(result.current.messages[1].approval).toMatchObject({ status: "error", errorReason: "토큰 불일치" });

    seedApproval();
    act(() => {
      result.current.handleApprovalResolved(1, { kind: "cancelled" });
    });
    expect(result.current.messages[1].approval?.status).toBe("cancelled");
    expect(result.current.messages[1].approval?.replanMessage).toBeUndefined();

    seedApproval();
    act(() => {
      result.current.handleApprovalResolved(1, { kind: "expired" });
    });
    expect(result.current.messages[1].approval?.status).toBe("expired");
    expect(result.current.messages[1].approval?.replanMessage).toBeUndefined();

    seedApproval();
    act(() => {
      result.current.handleApprovalResolved(1, { kind: "expired", replanMessage: "원래 요청" });
    });
    expect(result.current.messages[1].approval).toMatchObject({ status: "expired", replanMessage: "원래 요청" });
    expect(result.current.messages[1].content).toBe("승인 대기 중");
  });

  it("resolving a message that has no approval is a no-op", () => {
    const { result } = setup();
    act(() => {
      useConversationSession.getState().setMessages([{ role: "user", content: "그냥 질문" }]);
    });

    act(() => {
      result.current.handleApprovalResolved(0, { kind: "cancelled" });
      result.current.handleApprovalResolved(7, { kind: "cancelled" });
    });

    expect(result.current.messages).toEqual([{ role: "user", content: "그냥 질문" }]);
  });
});

describe("createActionItem", () => {
  it("saves a follow-up titled after the last question", async () => {
    const reviewSpy = vi.spyOn(latticeApi, "createReviewItem").mockResolvedValue(ok({ id: "r1" }) as never);
    const { result, memoryFeedback } = setup();
    act(() => {
      useConversationSession.getState().setConversationId("brain-77");
      useConversationSession.getState().setMessages([
        { role: "user", content: "다음 주 준비물 알려줘" },
        { role: "assistant", content: "정리해 드릴게요" },
      ]);
    });

    let saved = false;
    await act(async () => {
      saved = await result.current.createActionItem("  할 일 정리  ");
    });

    expect(saved).toBe(true);
    expect(reviewSpy.mock.calls[0][0]).toMatchObject({
      title: "다음 주 준비물 알려줘",
      summary: "할 일 정리",
      source: "chat_followup",
      payload: { conversation_id: "brain-77" },
      provenance: { conversation_id: "brain-77" },
    });
    expect(memoryFeedback.at(-1)).toBe(t("ko", "brain.action.saved"));
  });

  it("refuses empty content before any network call", async () => {
    const reviewSpy = vi.spyOn(latticeApi, "createReviewItem");
    const { result } = setup();
    let saved = true;
    await act(async () => {
      saved = await result.current.createActionItem("   ");
    });
    expect(saved).toBe(false);
    expect(reviewSpy).not.toHaveBeenCalled();
  });

  it("falls back to a default title and reports the failure reason", async () => {
    const reviewSpy = vi.spyOn(latticeApi, "createReviewItem").mockResolvedValue(fail("금지됨", {}) as never);
    const { result, memoryFeedback } = setup();
    act(() => {
      useConversationSession.getState().setMessages([{ role: "assistant", content: "안내만 있음" }]);
    });

    let saved = true;
    await act(async () => {
      saved = await result.current.createActionItem("메모");
    });

    expect(saved).toBe(false);
    expect(reviewSpy.mock.calls[0][0]).toMatchObject({
      title: t("ko", "brain.action.defaultTitle"),
      payload: { conversation_id: "" },
    });
    expect(memoryFeedback.at(-1)).toBe(t("ko", "brain.action.saveFailed", { reason: "금지됨" }));
  });

  it("a failure without any reason still reports honestly", async () => {
    vi.spyOn(latticeApi, "createReviewItem").mockResolvedValue({ ok: false } as never);
    const { result, memoryFeedback } = setup();
    await act(async () => {
      await result.current.createActionItem("메모");
    });
    expect(memoryFeedback.at(-1)).toBe(t("ko", "brain.action.saveFailed", { reason: "" }));
  });
});

describe("handleProactiveAction", () => {
  const baseAction: BrainProactiveAction = {
    id: "act-1", intent: "ask", labelKey: "l", detailKey: "d", prompt: "질문해줘", route: "", priority: 1, context: {},
  };

  afterEach(() => {
    window.location.hash = "";
  });

  it("routes navigation actions and keeps a newest-first activity history", async () => {
    const stream = fakeChatStream({ frames: [{ kind: "chunk", text: "답" }] });
    vi.spyOn(latticeApi, "streamChat").mockImplementation(stream.streamChat as never);
    const { result } = setup();

    await act(async () => {
      await result.current.handleProactiveAction({ ...baseAction, id: "go", intent: "route", route: "/capture" });
    });
    expect(window.location.hash).toBe("#/capture");

    await act(async () => {
      await result.current.handleProactiveAction({ ...baseAction, id: "ask-now", prompt: "오늘 뭐 했지?" });
    });

    expect(stream.calls[0]?.message).toBe("오늘 뭐 했지?");
    expect(result.current.proactiveActivities.map((activity) => [activity.actionId, activity.status, activity.detail])).toEqual([
      ["ask-now", "completed", "chat"],
      ["go", "completed", "/capture"],
    ]);
    expect(result.current.proactiveActivities.every((activity) => activity.completedAt)).toBe(true);
  });

  it("a route action without a destination fails as an empty prompt", async () => {
    const { result } = setup();
    await act(async () => {
      await result.current.handleProactiveAction({ ...baseAction, id: "no-dest", intent: "route", route: "", prompt: "   " });
    });
    expect(result.current.proactiveActivities[0]).toMatchObject({ actionId: "no-dest", status: "failed", detail: "empty prompt" });
  });

  it("delegates to the agent council and relaxes the notice afterwards", async () => {
    const runSpy = vi.spyOn(latticeApi, "runAgent").mockResolvedValue(ok({}) as never);
    const { result, memoryFeedback } = setup();

    await act(async () => {
      await result.current.handleProactiveAction({ ...baseAction, id: "del", intent: "delegate", prompt: "보고서 정리" });
    });

    expect(runSpy).toHaveBeenCalledWith("보고서 정리", ["planner", "executor", "reviewer"]);
    expect(result.current.proactiveActivities[0]).toMatchObject({ status: "completed", detail: "agent" });
    expect(memoryFeedback.at(-1)).toBe(t("ko", "brain.delegate.done"));

    await act(async () => {
      vi.advanceTimersByTime(4300);
    });
    expect(memoryFeedback.at(-1)).toBeNull();
  });

  it("a refused delegation reports the council's reason", async () => {
    vi.spyOn(latticeApi, "runAgent").mockResolvedValue(fail("협의회 없음", {}) as never);
    const { result, memoryFeedback } = setup();

    await act(async () => {
      await result.current.handleProactiveAction({ ...baseAction, id: "del-f", intent: "delegate", prompt: "정리" });
    });

    expect(result.current.proactiveActivities[0]).toMatchObject({ status: "failed", detail: "협의회 없음" });
    expect(memoryFeedback.at(-1)).toBe(t("ko", "brain.delegate.failed", { reason: "Error: 협의회 없음" }));
  });

  it("a refused delegation without a reason falls back to the standard notice", async () => {
    vi.spyOn(latticeApi, "runAgent").mockResolvedValue({ ok: false, status: 503, source: "x", data: {} } as never);
    const { result } = setup();

    await act(async () => {
      await result.current.handleProactiveAction({ ...baseAction, id: "del-q", intent: "delegate", prompt: "정리" });
    });

    expect(result.current.proactiveActivities[0].detail).toContain(t("ko", "ui.status.unavailable"));
  });

  it("review actions save through the follow-up path, honest on failure", async () => {
    const reviewSpy = vi.spyOn(latticeApi, "createReviewItem").mockResolvedValue(ok({ id: "r" }) as never);
    const { result } = setup();

    await act(async () => {
      await result.current.handleProactiveAction({ ...baseAction, id: "rev", intent: "review", prompt: "검토할 내용" });
    });
    expect(result.current.proactiveActivities[0]).toMatchObject({ actionId: "rev", status: "completed", detail: "review" });

    reviewSpy.mockResolvedValue(fail("저장 불가", {}) as never);
    await act(async () => {
      await result.current.handleProactiveAction({ ...baseAction, id: "rev-f", intent: "review", prompt: "검토" });
    });
    expect(result.current.proactiveActivities[0]).toMatchObject({ actionId: "rev-f", status: "failed", detail: "review" });
  });

  it("a review save that explodes records the raw failure", async () => {
    vi.spyOn(latticeApi, "createReviewItem").mockRejectedValue("연결 끊김");
    const { result } = setup();

    await act(async () => {
      await result.current.handleProactiveAction({ ...baseAction, id: "rev-x", intent: "review", prompt: "검토" });
    });

    expect(result.current.proactiveActivities[0]).toMatchObject({ status: "failed", detail: "연결 끊김" });
  });
});
