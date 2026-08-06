import type * as React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { latticeApi } from "@/api/client";
import { t } from "@/i18n";
import { abortError, fakeChatStream } from "@/test/fakeChatStream";
import { fail, ok } from "@/test/renderPage";
import { useConversationSession } from "../conversationSession";
import type { BrainProactiveAction, MessageApproval } from "../types";
import { useBrainChat } from "./useBrainChat";

/**
 * The streaming answer path — what the Brain does *while* it is answering.
 *
 * Every assertion here needs frames arriving over time, which is why this file
 * arrived with `@/test/fakeChatStream` rather than before it. The cases are the
 * ones a person actually meets: an answer building up token by token, a recall
 * pulse mid-answer, pressing stop, and the server refusing.
 */

function wrapper({ children }: { children: React.ReactNode }) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>;
}

type BrainCall = { state: string; intensity?: number };

function setup(overrides: Partial<Parameters<typeof useBrainChat>[0]> = {}) {
  const brainStates: BrainCall[] = [];
  const memoryFeedback: Array<string | null> = [];
  const options = {
    language: "ko" as const,
    modelReady: true,
    onBrainChange: (state: string, intensity?: number) => {
      brainStates.push({ state, intensity });
    },
    setMemoryFeedback: ((value: unknown) => {
      memoryFeedback.push(typeof value === "function" ? "(updater)" : (value as string | null));
    }) as never,
    beginIngestion: vi.fn(),
    completeIngestion: vi.fn().mockResolvedValue({ memories: 3, entities: 2 }),
    failIngestion: vi.fn(),
    setLastRecallQuery: vi.fn() as never,
    attachAnswerProof: vi.fn().mockResolvedValue(true),
    ...overrides,
  };
  const rendered = renderHook(() => useBrainChat(options as never), { wrapper });
  return { ...rendered, brainStates, memoryFeedback, options };
}

beforeEach(() => {
  useConversationSession.getState().resetConversation();
  vi.useFakeTimers({ shouldAdvanceTime: true });
});

afterEach(() => {
  vi.useRealTimers();
});

describe("useBrainChat streaming", () => {
  it("builds the answer on screen as frames arrive, not only at the end", async () => {
    const stream = fakeChatStream({
      frames: [
        { kind: "chunk", text: "로컬에서 " },
        { kind: "chunk", text: "동작합니다." },
      ],
      pauseAfter: 1,
    });
    vi.spyOn(latticeApi, "streamChat").mockImplementation(stream.streamChat as never);

    const { result } = setup();
    let send: Promise<void>;
    await act(async () => {
      send = result.current.sendText("어디서 도나요?");
      await stream.started;
    });

    // Mid-stream: the first frame is already on screen and the run is live.
    expect(result.current.streaming).toBe(true);
    expect(result.current.messages.at(-1)?.content).toBe("로컬에서 ");

    await act(async () => {
      stream.resume();
      await send;
    });

    expect(result.current.messages.at(-1)?.content).toBe("로컬에서 동작합니다.");
    expect(result.current.streaming).toBe(false);
    expect(stream.calls[0].message).toBe("어디서 도나요?");
  });

  it("leaves the organism at rest once the answer is done, even after a recall pulse", async () => {
    // Regression: `onTrace` parked a 900ms timer that pushed the Brain back to
    // "thinking". When the answer finished inside that window the timer still
    // fired, and the orb sat thinking about a question it had already answered.
    const stream = fakeChatStream({
      frames: [
        { kind: "trace", trace: { retrieved: 3 } },
        { kind: "chunk", text: "답변" },
      ],
    });
    vi.spyOn(latticeApi, "streamChat").mockImplementation(stream.streamChat as never);

    const { result, brainStates } = setup();
    await act(async () => {
      await result.current.sendText("기억에서 찾아줘");
    });
    expect(brainStates.some((call) => call.state === "recalling")).toBe(true);

    const settled = brainStates.length;
    await act(async () => {
      vi.advanceTimersByTime(2000);
    });

    const afterTimers = brainStates.slice(settled).map((call) => call.state);
    expect(afterTimers).not.toContain("thinking");
  });

  it("keeps a stopped answer honest instead of leaving an empty bubble", async () => {
    const stream = fakeChatStream({ frames: [], error: abortError() });
    vi.spyOn(latticeApi, "streamChat").mockImplementation(stream.streamChat as never);

    const { result, options } = setup();
    await act(async () => {
      await result.current.sendText("긴 질문");
    });

    expect(result.current.messages.at(-1)?.content).toContain("중단");
    expect(options.failIngestion).toHaveBeenCalledWith("chat", "stopped");
    expect(result.current.streaming).toBe(false);
  });

  it("stopStreaming aborts the run in flight", async () => {
    const stream = fakeChatStream({
      frames: [
        { kind: "chunk", text: "생각 중" },
        { kind: "chunk", text: " 계속" },
      ],
      pauseAfter: 1,
    });
    vi.spyOn(latticeApi, "streamChat").mockImplementation(stream.streamChat as never);

    const { result } = setup();
    let send: Promise<void>;
    await act(async () => {
      send = result.current.sendText("멈춰볼 질문");
      await stream.started;
    });

    await act(async () => {
      result.current.stopStreaming();
      stream.resume();
      await send;
    });

    // The abort landed between frames, so the second chunk never arrived.
    expect(result.current.messages.at(-1)?.content).toBe("생각 중");
    expect(result.current.streaming).toBe(false);
  });

  it("reports a refusal as an unavailable answer, not as a saved memory", async () => {
    const stream = fakeChatStream({ result: { error: "model not loaded" } });
    vi.spyOn(latticeApi, "streamChat").mockImplementation(stream.streamChat as never);

    const { result, options } = setup();
    await act(async () => {
      await result.current.sendText("질문");
    });

    expect(result.current.messages.at(-1)?.content).toContain("model not loaded");
    expect(options.failIngestion).toHaveBeenCalledWith("chat", "model not loaded");
    expect(options.completeIngestion).not.toHaveBeenCalled();
  });

  it("badges the answer with grounding and context quality from the trailer", async () => {
    const stream = fakeChatStream({
      frames: [{ kind: "chunk", text: "근거 있는 답" }],
      result: {
        grounding: { grounding: { status: "supported", reason: "notes.md" } },
        contextQuality: { context_quality: { mode: "graph", nodes: 3, limited: false } },
      },
    });
    vi.spyOn(latticeApi, "streamChat").mockImplementation(stream.streamChat as never);

    const { result } = setup();
    await act(async () => {
      await result.current.sendText("근거는?");
    });

    const answer = result.current.messages.at(-1);
    expect(answer?.grounding).toBeTruthy();
    expect(answer?.contextQuality).toBeTruthy();
  });

  it("accumulates live agent_step frames on the reply as they stream", async () => {
    const stream = fakeChatStream({
      frames: [
        { kind: "agentStep", step: { phase: "EXECUTING", event: "tool", action: "search", step: 1, ok: true } },
        { kind: "agentStep", step: { phase: "EXECUTING", event: "tool", action: "read_file", step: 2, ok: true } },
        { kind: "chunk", text: "정리했습니다." },
      ],
    });
    vi.spyOn(latticeApi, "streamChat").mockImplementation(stream.streamChat as never);

    const { result } = setup();
    await act(async () => {
      await result.current.sendText("파일 정리해줘");
    });

    expect(result.current.messages.at(-1)?.agentSteps?.length).toBe(2);
  });

  it("answers without a model by saying so, and never opens a stream", async () => {
    const streamSpy = vi.spyOn(latticeApi, "streamChat");
    const { result, options } = setup({ modelReady: false });

    await act(async () => {
      await result.current.sendText("모델 없이 질문");
    });

    expect(streamSpy).not.toHaveBeenCalled();
    expect(result.current.messages.at(-1)?.content).toContain("모델");
    expect(options.beginIngestion).not.toHaveBeenCalled();
  });

  it("refuses to start a second run while one is streaming", async () => {
    const stream = fakeChatStream({
      frames: [{ kind: "chunk", text: "첫 답" }],
      pauseAfter: 1,
    });
    vi.spyOn(latticeApi, "streamChat").mockImplementation(stream.streamChat as never);

    const { result } = setup();
    let send: Promise<void>;
    await act(async () => {
      send = result.current.sendText("첫 질문");
      await stream.started;
    });

    await act(async () => {
      await result.current.sendText("끼어든 질문");
    });
    expect(stream.calls.length).toBe(1);

    await act(async () => {
      stream.resume();
      await send;
    });
  });

  it("regenerate drops the previous exchange and re-asks the same question", async () => {
    const stream = fakeChatStream({ frames: [{ kind: "chunk", text: "첫 답" }] });
    vi.spyOn(latticeApi, "streamChat").mockImplementation(stream.streamChat as never);

    const { result } = setup();
    await act(async () => {
      await result.current.sendText("같은 질문");
    });
    await waitFor(() => expect(result.current.messages.length).toBe(2));

    await act(async () => {
      await result.current.regenerate();
    });

    expect(stream.calls.length).toBe(2);
    expect(stream.calls[1].message).toBe("같은 질문");
    // One user turn and one assistant turn, not a growing transcript.
    expect(result.current.messages.length).toBe(2);
  });

  it("keeps every send in one conversation so the thread survives", async () => {
    const stream = fakeChatStream({ frames: [{ kind: "chunk", text: "답" }] });
    vi.spyOn(latticeApi, "streamChat").mockImplementation(stream.streamChat as never);

    const { result } = setup();
    await act(async () => {
      await result.current.sendText("하나");
    });
    await act(async () => {
      await result.current.sendText("둘");
    });

    expect(stream.calls[0].conversation_id).toBeTruthy();
    expect(stream.calls[1].conversation_id).toBe(stream.calls[0].conversation_id);
  });
});

describe("useBrainChat presence and drafts", () => {
  it("listens while a real question is being typed, and rests otherwise", async () => {
    const { result, brainStates } = setup();
    act(() => {
      result.current.setDraft("여섯 글자 넘는 질문입니다");
    });
    await waitFor(() => expect(brainStates.at(-1)?.state).toBe("listening"));

    act(() => {
      result.current.setDraft("짧다");
    });
    await waitFor(() => expect(brainStates.at(-1)?.state).toBe("idle"));
  });

  it("send() posts the trimmed draft once and clears it", async () => {
    const stream = fakeChatStream({ frames: [{ kind: "chunk", text: "답" }] });
    vi.spyOn(latticeApi, "streamChat").mockImplementation(stream.streamChat as never);

    const { result } = setup();
    await act(async () => {
      await result.current.send();
    });
    expect(stream.calls.length).toBe(0);

    act(() => {
      result.current.setDraft("  보낼 질문  ");
    });
    await act(async () => {
      await result.current.send();
    });
    expect(stream.calls[0].message).toBe("보낼 질문");
    expect(result.current.draft).toBe("");
  });

  it("send() while an answer is streaming is refused", async () => {
    const stream = fakeChatStream({ frames: [{ kind: "chunk", text: "첫" }], pauseAfter: 1 });
    vi.spyOn(latticeApi, "streamChat").mockImplementation(stream.streamChat as never);

    const { result } = setup();
    let send!: Promise<void>;
    await act(async () => {
      send = result.current.sendText("첫 질문");
      await stream.started;
    });

    act(() => {
      result.current.setDraft("끼어들 질문");
    });
    await act(async () => {
      await result.current.send();
    });
    expect(stream.calls.length).toBe(1);

    await act(async () => {
      stream.resume();
      await send;
    });
  });

  it("attaches the pending image to exactly one send, then clears it", async () => {
    const stream = fakeChatStream({ frames: [{ kind: "chunk", text: "그림이네요" }] });
    vi.spyOn(latticeApi, "streamChat").mockImplementation(stream.streamChat as never);

    const { result } = setup();
    act(() => {
      result.current.setImageData("data:image/png;base64,QUJD");
    });
    await act(async () => {
      await result.current.sendText("이 그림 뭐야?");
    });

    expect(stream.calls[0].image_data).toBe("data:image/png;base64,QUJD");
    expect(result.current.imageData).toBeNull();
  });
});

describe("useBrainChat regenerate edges", () => {
  it("does nothing while streaming or without a user turn", async () => {
    const stream = fakeChatStream({ frames: [{ kind: "chunk", text: "답" }], pauseAfter: 1 });
    vi.spyOn(latticeApi, "streamChat").mockImplementation(stream.streamChat as never);

    const { result } = setup();
    await act(async () => {
      await result.current.regenerate();
    });
    expect(stream.calls.length).toBe(0);

    let send!: Promise<void>;
    await act(async () => {
      send = result.current.sendText("질문");
      await stream.started;
    });
    await act(async () => {
      await result.current.regenerate();
    });
    expect(stream.calls.length).toBe(1);

    await act(async () => {
      stream.resume();
      await send;
    });
  });

  it("trims exactly the last exchange whatever shape the tail has", async () => {
    const stream = fakeChatStream({ frames: [{ kind: "chunk", text: "다시" }] });
    vi.spyOn(latticeApi, "streamChat").mockImplementation(stream.streamChat as never);
    const { result } = setup();

    // A stopped run can leave the user turn last.
    act(() => {
      useConversationSession.getState().setMessages([{ role: "user", content: "질문" }]);
    });
    await act(async () => {
      await result.current.regenerate();
    });
    expect(stream.calls[0].message).toBe("질문");

    // A doubled assistant tail loses only the trailing answer.
    act(() => {
      useConversationSession.getState().setMessages([
        { role: "user", content: "질문2" },
        { role: "assistant", content: "답1" },
        { role: "assistant", content: "답2" },
      ]);
    });
    await act(async () => {
      await result.current.regenerate();
    });
    expect(stream.calls[1].message).toBe("질문2");
  });
});

describe("useBrainChat agent frames", () => {
  it("parks a governed plan on the reply as a pending approval", async () => {
    const stream = fakeChatStream({ frames: [{ kind: "agent", agent: {
      status: "awaiting_approval",
      run_id: "run-1",
      approval: { token: "tok-1", expires_at: "2026-08-06T09:00:00+09:00", plan_summary: "파일 정리 계획" },
      plan: { steps: [] },
    } }] });
    vi.spyOn(latticeApi, "streamChat").mockImplementation(stream.streamChat as never);

    const { result } = setup();
    await act(async () => {
      await result.current.sendText("정리해줘");
    });

    expect(result.current.messages.at(-1)?.approval).toMatchObject({
      runId: "run-1", token: "tok-1", status: "pending", planSummary: "파일 정리 계획",
    });
  });

  it("joins created files with preview verdicts and surfaces a FAILED state", async () => {
    const stream = fakeChatStream({ frames: [{ kind: "agent", agent: {
      created_files: [{ path: "out/a.html", filename: "a.html", bytes: 5 }],
      artifacts: [{ path: "out/a.html", previewable: false }],
      generation: { repaired: true },
      final_state: "FAILED",
    } }] });
    vi.spyOn(latticeApi, "streamChat").mockImplementation(stream.streamChat as never);

    const { result } = setup();
    await act(async () => {
      await result.current.sendText("파일 만들어줘");
    });

    const reply = result.current.messages.at(-1);
    expect(reply?.files?.[0]).toMatchObject({ path: "out/a.html", previewable: false, repaired: true });
    expect(reply?.agentState).toBe("FAILED");
  });

  it("ignores an agent frame that carries nothing", async () => {
    const stream = fakeChatStream({ frames: [
      { kind: "agent", agent: {} },
      { kind: "chunk", text: "그냥 답" },
    ] });
    vi.spyOn(latticeApi, "streamChat").mockImplementation(stream.streamChat as never);

    const { result } = setup();
    await act(async () => {
      await result.current.sendText("질문");
    });

    const reply = result.current.messages.at(-1);
    expect(reply?.content).toBe("그냥 답");
    expect(reply?.files).toBeUndefined();
    expect(reply?.agentState).toBeUndefined();
    expect(reply?.agentSteps).toBeUndefined();
  });

  it("keeps streamed step frames over the post-hoc transcript and adds loop honesty", async () => {
    const stream = fakeChatStream({ frames: [
      { kind: "agentStep", step: { phase: "execute", event: "tool", action: "search" } },
      { kind: "agent", agent: {
        loop: { repairs: { json_fence: 1 } },
        steps: [{ action: "search", state: "EXECUTING" }, { state: "DONE" }],
      } },
    ] });
    vi.spyOn(latticeApi, "streamChat").mockImplementation(stream.streamChat as never);

    const { result } = setup();
    await act(async () => {
      await result.current.sendText("찾아줘");
    });

    const reply = result.current.messages.at(-1);
    expect(reply?.agentSteps).toEqual([{ phase: "execute", event: "tool", action: "search" }]);
    expect(reply?.loopSummary?.total).toBe(1);
  });

  it("derives the step timeline from the final transcript when nothing streamed", async () => {
    const stream = fakeChatStream({ frames: [{ kind: "agent", agent: {
      final_state: "NEEDS_REVIEW",
      steps: [{ action: "write_file", args: { path: "a.md" } }],
    } }] });
    vi.spyOn(latticeApi, "streamChat").mockImplementation(stream.streamChat as never);

    const { result } = setup();
    await act(async () => {
      await result.current.sendText("기록해줘");
    });

    const reply = result.current.messages.at(-1);
    expect(reply?.agentSteps).toEqual([{ phase: "execute", event: "tool", action: "write_file", path: "a.md", ok: true }]);
    expect(reply?.agentState).toBe("NEEDS_REVIEW");
  });

  it("attaches the run explanation when the agent had to strain", async () => {
    const stream = fakeChatStream({ frames: [{ kind: "agent", agent: {
      explanation: { code: "recovered", ok: false, headline: { ko: "복구했어요", en: "Recovered" }, details: [] },
    } }] });
    vi.spyOn(latticeApi, "streamChat").mockImplementation(stream.streamChat as never);

    const { result } = setup();
    await act(async () => {
      await result.current.sendText("해줘");
    });

    expect(result.current.messages.at(-1)?.runExplanation?.headline).toBe("복구했어요");
  });

  it("drops a malformed step frame instead of rendering a broken row", async () => {
    const stream = fakeChatStream({ frames: [
      { kind: "agentStep", step: {} },
      { kind: "chunk", text: "응답" },
    ] });
    vi.spyOn(latticeApi, "streamChat").mockImplementation(stream.streamChat as never);

    const { result } = setup();
    await act(async () => {
      await result.current.sendText("질문");
    });

    expect(result.current.messages.at(-1)?.agentSteps).toBeUndefined();
  });

  it("a null trace never fires a recall pulse", async () => {
    const stream = fakeChatStream({ frames: [
      { kind: "trace", trace: null },
      { kind: "chunk", text: "답" },
    ] });
    vi.spyOn(latticeApi, "streamChat").mockImplementation(stream.streamChat as never);

    const { result, brainStates } = setup();
    await act(async () => {
      await result.current.sendText("질문");
    });

    expect(brainStates.some((call) => call.state === "recalling")).toBe(false);
  });

  it("a recall pulse mid-answer settles back into thinking while the stream continues", async () => {
    const stream = fakeChatStream({ frames: [
      { kind: "trace", trace: { retrieved: 1 } },
      { kind: "trace", trace: { retrieved: 2 } },
      { kind: "chunk", text: "답" },
    ], pauseAfter: 2 });
    vi.spyOn(latticeApi, "streamChat").mockImplementation(stream.streamChat as never);

    const { result, brainStates } = setup();
    let send!: Promise<void>;
    await act(async () => {
      send = result.current.sendText("기억 질문");
      await stream.started;
    });

    const before = brainStates.length;
    await act(async () => {
      vi.advanceTimersByTime(1000);
    });
    expect(brainStates.slice(before).some((call) => call.state === "thinking")).toBe(true);

    await act(async () => {
      stream.resume();
      await send;
    });
  });

  it("unmounting mid-answer clears the parked recall timer and aborts the stream", async () => {
    const stream = fakeChatStream({ frames: [
      { kind: "trace", trace: { retrieved: 1 } },
      { kind: "chunk", text: "남은 답" },
    ], pauseAfter: 1 });
    vi.spyOn(latticeApi, "streamChat").mockImplementation(stream.streamChat as never);

    const { result, unmount, brainStates } = setup();
    let send!: Promise<void>;
    await act(async () => {
      send = result.current.sendText("질문");
      await stream.started;
    });

    unmount();
    await act(async () => {
      stream.resume();
      await send;
    });

    const settled = brainStates.length;
    await act(async () => {
      vi.advanceTimersByTime(2000);
    });
    expect(brainStates.length).toBe(settled);
  });
});

describe("useBrainChat trailers and failures", () => {
  it("badges context quality alone when no grounding verdict arrives", async () => {
    const stream = fakeChatStream({
      frames: [{ kind: "chunk", text: "제한된 답" }],
      result: { contextQuality: { context_quality: { mode: "lexical_only", nodes: 0, limited: true } } },
    });
    vi.spyOn(latticeApi, "streamChat").mockImplementation(stream.streamChat as never);

    const { result } = setup();
    await act(async () => {
      await result.current.sendText("질문");
    });

    const reply = result.current.messages.at(-1);
    expect(reply?.contextQuality?.mode).toBe("lexical_only");
    expect(reply?.grounding).toBeUndefined();
  });

  it("badges grounding read from the trace record alone", async () => {
    const stream = fakeChatStream({
      frames: [{ kind: "chunk", text: "근거 없는 답" }],
      result: { trace: { grounding: { status: "unsupported", reason: "인용 없음" } } },
    });
    vi.spyOn(latticeApi, "streamChat").mockImplementation(stream.streamChat as never);

    const { result } = setup();
    await act(async () => {
      await result.current.sendText("질문");
    });

    const reply = result.current.messages.at(-1);
    expect(reply?.grounding).toEqual({ status: "unsupported", reason: "인용 없음" });
    expect(reply?.contextQuality).toBeUndefined();
  });

  it("badges the streamed answer even when another surface already appended to the session", async () => {
    // The conversation store is shared across surfaces; a message written into
    // it mid-stream must not steal the trailer badge from the actual answer.
    const stream = fakeChatStream({
      frames: [{ kind: "chunk", text: "근거 답" }],
      result: { grounding: { grounding: { status: "supported", reason: "notes.md" } } },
      pauseAfter: 1,
    });
    vi.spyOn(latticeApi, "streamChat").mockImplementation(stream.streamChat as never);

    const { result } = setup();
    let send!: Promise<void>;
    await act(async () => {
      send = result.current.sendText("근거는?");
      await stream.started;
    });

    act(() => {
      useConversationSession.getState().setMessages((items) => [
        ...items,
        { role: "user", content: "이어서 물어볼게" },
      ]);
    });
    await act(async () => {
      stream.resume();
      await send;
    });

    const messages = result.current.messages;
    expect(messages.at(-1)?.role).toBe("user");
    expect(messages.at(-1)?.grounding).toBeUndefined();
    expect(messages.at(-2)?.grounding).toMatchObject({ status: "supported" });
  });

  it("a thrown Error lands on the empty reply as an honest failure", async () => {
    const stream = fakeChatStream({ error: new Error("brain melted") });
    vi.spyOn(latticeApi, "streamChat").mockImplementation(stream.streamChat as never);

    const { result, options, memoryFeedback } = setup();
    await act(async () => {
      await result.current.sendText("질문");
    });

    expect(result.current.messages.at(-1)?.content).toContain("brain melted");
    expect(options.failIngestion).toHaveBeenCalledWith("chat", "brain melted");
    expect(memoryFeedback.at(-1)).toBeNull();
  });

  it("a non-Error throw is stringified, not swallowed", async () => {
    const stream = fakeChatStream({ error: "socket torn" });
    vi.spyOn(latticeApi, "streamChat").mockImplementation(stream.streamChat as never);

    const { result, options } = setup();
    await act(async () => {
      await result.current.sendText("질문");
    });

    expect(result.current.messages.at(-1)?.content).toContain("socket torn");
    expect(options.failIngestion).toHaveBeenCalledWith("chat", "socket torn");
  });

  it("stopping after some text keeps the partial answer as-is", async () => {
    const stream = fakeChatStream({
      frames: [{ kind: "chunk", text: "부분 답변" }],
      error: abortError(),
    });
    vi.spyOn(latticeApi, "streamChat").mockImplementation(stream.streamChat as never);

    const { result, options, memoryFeedback } = setup();
    await act(async () => {
      await result.current.sendText("질문");
    });

    expect(result.current.messages.at(-1)?.content).toBe("부분 답변");
    expect(memoryFeedback.at(-1)).toBe(t("ko", "brain.stopped"));
    expect(options.failIngestion).toHaveBeenCalledWith("chat", "stopped");
  });

  it("two sends racing into one tick both finish and the stop handle follows the newest", async () => {
    const stream = fakeChatStream({ frames: [{ kind: "chunk", text: "동시" }] });
    vi.spyOn(latticeApi, "streamChat").mockImplementation(stream.streamChat as never);

    const { result } = setup();
    await act(async () => {
      const first = result.current.sendText("하나");
      const second = result.current.sendText("둘");
      await Promise.all([first, second]);
    });

    expect(stream.calls.length).toBe(2);
    expect(result.current.streaming).toBe(false);
  });
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
