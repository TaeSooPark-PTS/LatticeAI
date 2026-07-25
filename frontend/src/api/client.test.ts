import { afterEach, describe, expect, it, vi } from "vitest";

import { latticeApi } from "./client";

// A real SSE body: the fetch stub returns a Response whose ReadableStream the
// parser consumes exactly like the live /chat endpoint.
function sseResponse(frames: string[]): Response {
  return new Response(frames.join(""), {
    status: 200,
    headers: { "Content-Type": "text/event-stream" },
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("streamChat SSE parsing", () => {
  it("routes agent_step named frames to onAgentStep and keeps data frames intact", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(sseResponse([
      'event: agent_step\ndata: {"phase":"plan","event":"planned","step":1}\n\n',
      'event: agent_step\ndata: {"phase":"execute","event":"tool","action":"write_file","path":"notes.html","ok":true,"future_field":123}\n\n',
      'data: {"chunk":"안녕"}\n\n',
      'data: {"agent":{"status":"ok","final_state":"DONE","loop":{"repairs":{"json_fence":2},"parse_recovered":1},"steps":[]}}\n\n',
      "data: [DONE]\n\n",
    ])));
    const onAgentStep = vi.fn();
    const onChunk = vi.fn();
    const onAgent = vi.fn();

    const result = await latticeApi.streamChat(
      { message: "hi" },
      { onAgentStep, onChunk, onAgent },
    );

    expect(onAgentStep).toHaveBeenCalledTimes(2);
    expect(onAgentStep.mock.calls[0][0]).toMatchObject({ phase: "plan", event: "planned" });
    expect(onAgentStep.mock.calls[1][0]).toMatchObject({
      phase: "execute",
      event: "tool",
      action: "write_file",
      path: "notes.html",
      ok: true,
    });
    expect(onChunk).toHaveBeenCalledWith("안녕", "안녕");
    expect(onAgent).toHaveBeenCalledTimes(1);
    expect(onAgent.mock.calls[0][0]).toMatchObject({
      final_state: "DONE",
      loop: { repairs: { json_fence: 2 }, parse_recovered: 1 },
    });
    expect(result.text).toBe("안녕");
  });

  it("ignores unknown named events and malformed agent_step frames without breaking the stream", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(sseResponse([
      'event: future_event\ndata: {"x":1}\n\n',
      "event: agent_step\ndata: {broken json}\n\n",
      'event: agent_step\ndata: ["arrays","are","not","steps"]\n\n',
      'data: {"chunk":"ok"}\n\n',
      "data: [DONE]\n\n",
    ])));
    const onAgentStep = vi.fn();
    const onChunk = vi.fn();

    const result = await latticeApi.streamChat({ message: "hi" }, { onAgentStep, onChunk });

    expect(onAgentStep).not.toHaveBeenCalled();
    expect(onChunk).toHaveBeenCalledWith("ok", "ok");
    expect(result.text).toBe("ok");
  });
});
