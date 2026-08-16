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

  it("attaches hybrid context as it arrives and marks the reply as a cloud answer", async () => {
    const stream = fakeChatStream({
      frames: [
        { kind: "hybridContext", frame: { type: "hybrid_context", node_ids: ["n1", "n2"], keywords: ["릴리스"] } },
        { kind: "chunk", text: "부분" },
        {
          kind: "hybridDone",
          frame: {
            type: "hybrid_done",
            answer: "클라우드에서 답합니다.",
            provider: "Antigravity",
            model: "gemini-3.7-flash",
            sent_node_ids: ["n1", "n2"],
            kg_expansion: { status: "staged", plan: { provenance: { candidate_count: 2 } } },
          },
        },
      ],
    });
    vi.spyOn(latticeApi, "streamChat").mockImplementation(stream.streamChat as never);

    const { result } = setup();
    await act(async () => {
      await result.current.sendText("릴리스 어떻게 하지");
    });

    const reply = result.current.messages.at(-1);
    expect(reply?.content).toBe("클라우드에서 답합니다.");
    expect(reply?.hybridContext?.nodeIds).toEqual(["n1", "n2"]);
    expect(reply?.cloudAnswer).toMatchObject({
      provider: "Antigravity",
      model: "gemini-3.7-flash",
      sentNodeCount: 2,
      expansion: { candidateCount: 2, stagedForReview: true },
    });
  });

  it("ignores unparseable hybrid frames and a hybrid update that no longer has an assistant reply", async () => {
    const stream = fakeChatStream({
      frames: [
        { kind: "hybridContext", frame: { type: "hybrid_done" } },
        { kind: "hybridDone", frame: { type: "hybrid_context" } },
        { kind: "hybridContext", frame: { type: "hybrid_context", node_ids: ["late"] } },
        { kind: "hybridDone", frame: { type: "hybrid_done", answer: "늦음", model: "x" } },
      ],
      pauseAfter: 2,
    });
    vi.spyOn(latticeApi, "streamChat").mockImplementation(stream.streamChat as never);

    const { result } = setup();
    let send: Promise<void>;
    await act(async () => {
      send = result.current.sendText("하이브리드");
      await stream.started;
    });
    act(() => {
      useConversationSession.getState().setMessages([{ role: "user", content: "하이브리드" }]);
    });
    await act(async () => {
      stream.resume();
      await send;
    });

    expect(result.current.messages.at(-1)?.role).toBe("user");
    expect(result.current.messages.at(-1)?.cloudAnswer).toBeUndefined();
  });

  it("applies hybrid trailers when the live handlers never saw the frames", async () => {
    const stream = fakeChatStream({
      frames: [{ kind: "chunk", text: "미리" }],
      result: {
        hybridContext: { type: "hybrid_context", node_ids: ["n9"] },
        hybridDone: { type: "hybrid_done", answer: "최종", model: "m", sent_node_ids: [] },
      },
    });
    vi.spyOn(latticeApi, "streamChat").mockImplementation(stream.streamChat as never);

    const { result } = setup();
    await act(async () => {
      await result.current.sendText("트레일러");
    });

    const reply = result.current.messages.at(-1);
    expect(reply?.content).toBe("최종");
    expect(reply?.hybridContext?.nodeIds).toEqual(["n9"]);
    expect(reply?.cloudAnswer?.sentNodeCount).toBe(1);
    expect(reply?.cloudAnswer?.model).toBe("m");
  });

  it("does not overwrite a live cloud answer with an empty trailer", async () => {
    const stream = fakeChatStream({
      frames: [{
        kind: "hybridDone",
        frame: { type: "hybrid_done", answer: "살아 있는 답", model: "live" },
      }],
      result: { hybridDone: { type: "hybrid_done", answer: "덮지 마", model: "trailer" } },
    });
    vi.spyOn(latticeApi, "streamChat").mockImplementation(stream.streamChat as never);

    const { result } = setup();
    await act(async () => {
      await result.current.sendText("이미 표시됨");
    });

    expect(result.current.messages.at(-1)?.content).toBe("살아 있는 답");
    expect(result.current.messages.at(-1)?.cloudAnswer?.model).toBe("live");
  });

  it("keeps streamed text when hybrid_done has no answer, counting in-flight nodes", async () => {
    const stream = fakeChatStream({
      frames: [
        { kind: "hybridContext", frame: { type: "hybrid_context", node_ids: ["a", "b"] } },
        { kind: "chunk", text: "토큰" },
        { kind: "hybridDone", frame: { type: "hybrid_done", answer: "", model: "m" } },
      ],
    });
    vi.spyOn(latticeApi, "streamChat").mockImplementation(stream.streamChat as never);

    const { result } = setup();
    await act(async () => {
      await result.current.sendText("빈 답");
    });

    const reply = result.current.messages.at(-1);
    expect(reply?.content).toBe("토큰");
    expect(reply?.cloudAnswer?.sentNodeCount).toBe(2);
    expect(reply?.cloudAnswer?.model).toBe("m");
  });

  it("uses in-flight node ids when a trailer hybrid_done has none", async () => {
    const stream = fakeChatStream({
      frames: [
        { kind: "hybridContext", frame: { type: "hybrid_context", node_ids: ["a", "b"] } },
        { kind: "chunk", text: "미리" },
      ],
      result: {
        hybridDone: { type: "hybrid_done", answer: "최종", model: "m", sent_node_ids: [] },
      },
    });
    vi.spyOn(latticeApi, "streamChat").mockImplementation(stream.streamChat as never);

    const { result } = setup();
    await act(async () => {
      await result.current.sendText("컨텍스트만");
    });

    expect(result.current.messages.at(-1)?.content).toBe("최종");
    expect(result.current.messages.at(-1)?.cloudAnswer?.sentNodeCount).toBe(2);
  });

  it("counts trailer context nodes when the live reply has none yet", async () => {
    const stream = fakeChatStream({
      frames: [{ kind: "chunk", text: "미리" }],
      result: {
        hybridDone: { type: "hybrid_done", answer: "", model: "m", sent_node_ids: [] },
      },
    });
    vi.spyOn(latticeApi, "streamChat").mockImplementation(stream.streamChat as never);

    const { result } = setup();
    await act(async () => {
      await result.current.sendText("노드 없음");
    });

    expect(result.current.messages.at(-1)?.content).toBe("미리");
    expect(result.current.messages.at(-1)?.cloudAnswer?.sentNodeCount).toBe(0);
  });

  it("sends network_mode only when this conversation is pinned to local", async () => {
    const stream = fakeChatStream({ frames: [{ kind: "chunk", text: "로컬" }] });
    vi.spyOn(latticeApi, "streamChat").mockImplementation(stream.streamChat as never);

    const { result } = setup();
    await act(async () => {
      await result.current.sendText("기본");
    });
    expect(stream.calls[0].network_mode).toBeUndefined();

    act(() => {
      useConversationSession.getState().setPreferLocalOnly(true);
    });
    await act(async () => {
      await result.current.sendText("로컬만");
    });
    expect(stream.calls[1].network_mode).toBe("local_only");
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
