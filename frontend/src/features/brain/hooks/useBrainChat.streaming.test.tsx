import { act, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { latticeApi } from "@/api/client";
import { abortError, fakeChatStream } from "@/test/fakeChatStream";
import { setup } from "@/test/brainChatHarness";
import { useConversationSession } from "../conversationSession";

/**
 * The streaming answer path — what the Brain does *while* it is answering.
 *
 * Every assertion here needs frames arriving over time, which is why this file
 * arrived with `@/test/fakeChatStream` rather than before it. The cases are the
 * ones a person actually meets: an answer building up token by token, a recall
 * pulse mid-answer, pressing stop, and the server refusing.
 */

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
