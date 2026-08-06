import type * as React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { latticeApi } from "@/api/client";
import { t } from "@/i18n";
import { fail, ok, stubApi } from "@/test/renderPage";
import { useConversationSession } from "../conversationSession";
import type { Message } from "../types";
import { useBrainProof } from "./useBrainProof";

/**
 * The proof surface: what the Brain can show for "정말 기억하나요?". Covers the
 * brain-proof/brain-brief queries, attaching per-answer evidence, and the
 * model-continuity check.
 */

function wrapper({ children }: { children: React.ReactNode }) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0, staleTime: 0 }, mutations: { retry: false } },
  });
  return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>;
}

function setup({
  messages = [] as Message[],
  modelName = "Gemma 4",
  api = {} as Parameters<typeof stubApi>[0],
} = {}) {
  stubApi(api);
  const feedback: Array<string | null> = [];
  const setMemoryFeedback = ((value: unknown) => {
    feedback.push(typeof value === "function" ? "(updater)" : (value as string | null));
  }) as React.Dispatch<React.SetStateAction<string | null>>;
  const rendered = renderHook(() => useBrainProof({ language: "ko", messages, modelName, setMemoryFeedback }), { wrapper });
  return { ...rendered, feedback };
}

beforeEach(() => {
  useConversationSession.getState().resetConversation();
});

describe("useBrainProof", () => {
  it("assembles the proof and brief from their queries once a conversation exists", async () => {
    const { result } = setup({
      messages: [{ role: "user", content: "질문" }],
      api: {
        memoryBrainProof: ok({
          status: "alive",
          model_continuity: { active_model: "Gemma 4", proven: true },
          proofs: { durable_items: 6 },
          recall: { query: "", count: 0, items: [] },
          claims: { keeps_context_across_models: true },
        }),
        memoryBrainBrief: ok({ status: "alive", score: 80 }),
      },
    });

    await waitFor(() => expect(result.current.brainProof.status).toBe("alive"));
    expect(result.current.brainProof.proofs.durableItems).toBe(6);
    await waitFor(() => expect(result.current.brainBrief.score).toBe(80));
    expect(latticeApi.memoryBrainProof).toHaveBeenCalledWith("", 3);
  });

  it("stays lazy until details are requested, then fetches", async () => {
    const { result } = setup();
    expect(latticeApi.memoryBrainProof).not.toHaveBeenCalled();
    expect(result.current.brainProof.status).toBe("quiet");
    expect(result.current.lastRecallQuery).toBe("");

    act(() => {
      result.current.requestDetails();
    });
    await waitFor(() => expect(latticeApi.memoryBrainProof).toHaveBeenCalledWith("", 3));
  });

  it("refetches the proof for the newest recall query", async () => {
    const { result } = setup();
    act(() => {
      result.current.setLastRecallQuery("어제 정리한 메모");
    });
    await waitFor(() => expect(latticeApi.memoryBrainProof).toHaveBeenCalledWith("어제 정리한 메모", 3));
    expect(result.current.lastRecallQuery).toBe("어제 정리한 메모");
  });

  it("attachAnswerProof pins citations onto the latest answer", async () => {
    act(() => {
      useConversationSession.getState().setMessages([
        { role: "user", content: "회의 내용 기억나?" },
        { role: "assistant", content: "네, 요약해 드릴게요." },
      ]);
    });
    const { result } = setup({
      api: {
        memoryBrainProof: ok({
          model_continuity: { active_model: "Gemma 4", proven: true },
          claims: { keeps_context_across_models: true },
          recall: { query: "회의", count: 1, items: [
            { id: "m1", source: "note", title: "회의록", snippet: "결정 사항", score: 0.9, matched_terms: ["회의"], locator: "p.2" },
          ] },
        }),
      },
    });

    let attached = false;
    await act(async () => {
      attached = await result.current.attachAnswerProof("회의 내용 기억나?");
    });

    expect(attached).toBe(true);
    const answer = useConversationSession.getState().messages.at(-1);
    expect(answer?.proof).toMatchObject({
      query: "회의 내용 기억나?",
      model: "Gemma 4",
      provenAcrossModels: true,
    });
    expect(answer?.proof?.citations[0]).toMatchObject({ id: "m1", source: "Note", snippet: "결정 사항", locator: "p.2" });
  });

  it("attachAnswerProof leaves a user-tail conversation untouched but still succeeds", async () => {
    act(() => {
      useConversationSession.getState().setMessages([{ role: "user", content: "아직 답 없음" }]);
    });
    const { result } = setup({ modelName: "", api: { memoryBrainProof: ok({}) } });

    let attached = false;
    await act(async () => {
      attached = await result.current.attachAnswerProof("아직 답 없음");
    });

    expect(attached).toBe(true);
    expect(useConversationSession.getState().messages[0].proof).toBeUndefined();
  });

  it("attachAnswerProof reports an unavailable proof with the server reason or a fallback", async () => {
    const { result, feedback } = setup({ api: { memoryBrainProof: fail("기억 저장소 접근 불가", {}) } });
    let attached = true;
    await act(async () => {
      attached = await result.current.attachAnswerProof("질문");
    });
    expect(attached).toBe(false);
    expect(feedback.at(-1)).toBe(t("ko", "brain.proof.unavailable", { reason: "기억 저장소 접근 불가" }));

    stubApi({ memoryBrainProof: { ok: false, status: 503, source: "unavailable", data: null } });
    await act(async () => {
      attached = await result.current.attachAnswerProof("질문");
    });
    expect(attached).toBe(false);
    expect(feedback.at(-1)).toBe(t("ko", "brain.proof.unavailable", { reason: t("ko", "ui.status.unavailable") }));
  });

  it("verifyModelContinuity asks for a question first when there is nothing to check", async () => {
    const { result, feedback } = setup();
    await act(async () => {
      await result.current.verifyModelContinuity();
    });
    expect(feedback.at(-1)).toBe(t("ko", "brain.modelDemo.needQuestion"));
    expect(latticeApi.memoryBrainProof).not.toHaveBeenCalled();
  });

  it("verifyModelContinuity proves continuity from the last question", async () => {
    act(() => {
      useConversationSession.getState().setMessages([
        { role: "user", content: "이 모델도 내 기억을 알아?" },
        { role: "assistant", content: "네." },
      ]);
    });
    const { result, feedback } = setup({
      messages: [
        { role: "user", content: "이 모델도 내 기억을 알아?" },
        { role: "assistant", content: "네." },
      ],
      api: { memoryBrainProof: ok({ recall: { items: [] } }) },
    });

    await act(async () => {
      await result.current.verifyModelContinuity();
    });

    expect(feedback).toContain(t("ko", "brain.modelDemo.checking", { model: "Gemma 4" }));
    expect(feedback.at(-1)).toBe(t("ko", "brain.modelDemo.done", { model: "Gemma 4" }));
    expect(result.current.lastRecallQuery).toBe("이 모델도 내 기억을 알아?");
    expect(latticeApi.memoryBrainProof).toHaveBeenCalledWith("이 모델도 내 기억을 알아?", 4);
  });

  it("verifyModelContinuity stops quietly when the proof cannot be attached", async () => {
    const { result, feedback } = setup({
      messages: [{ role: "user", content: "기억 확인" }],
      api: { memoryBrainProof: fail("증명 실패", {}) },
    });

    await act(async () => {
      await result.current.verifyModelContinuity();
    });

    expect(feedback.at(-1)).toBe(t("ko", "brain.proof.unavailable", { reason: "증명 실패" }));
    expect(result.current.lastRecallQuery).toBe("");
  });

  it("verifyModelContinuity prefers the already-proven recall query", async () => {
    const { result } = setup({ api: { memoryBrainProof: ok({}) } });
    act(() => {
      result.current.setLastRecallQuery("이전 질문");
    });

    await act(async () => {
      await result.current.verifyModelContinuity();
    });

    expect(latticeApi.memoryBrainProof).toHaveBeenCalledWith("이전 질문", 4);
  });
});
