import { act } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { latticeApi } from "@/api/client";
import { t } from "@/i18n";
import { abortError, fakeChatStream } from "@/test/fakeChatStream";
import { setup } from "@/test/brainChatHarness";
import { useConversationSession } from "../conversationSession";

/**
 * Agent frames and answer trailers: the parts of a reply that arrive beside the
 * text — step timelines, governed plans, produced files, grounding verdicts —
 * and what an answer looks like when the run fails instead.
 */

beforeEach(() => {
  useConversationSession.getState().resetConversation();
  vi.useFakeTimers({ shouldAdvanceTime: true });
});

afterEach(() => {
  vi.useRealTimers();
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
