import type * as React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { latticeApi } from "@/api/client";
import { abortError, fakeChatStream } from "@/test/fakeChatStream";
import { useConversationSession } from "../conversationSession";
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
