import { afterEach, describe, expect, it, vi } from "vitest";

import { latticeApi } from "./client";
import { readEventStream } from "./eventStream";

function sseBody(frames: string[]): ReadableStream<Uint8Array> {
  const body = new Response(frames.join("")).body;
  if (!body) throw new Error("Response body missing in this environment");
  return body;
}

function sseResponse(frames: string[]): Response {
  return new Response(frames.join(""), {
    status: 200,
    headers: { "Content-Type": "text/event-stream" },
  });
}

async function collect(frames: string[]) {
  const out = [];
  for await (const frame of readEventStream(sseBody(frames))) out.push(frame);
  return out;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("readEventStream", () => {
  it("marks an unparseable frame malformed and keeps decoding the rest", async () => {
    const frames = await collect([
      'data: {"chunk":"before"}\n\n',
      'data: {"chunk":"truncated\n\n',
      'event: agent_step\ndata: {"phase":"plan"}\n\n',
      'data: {"chunk":"after"}\n\n',
    ]);

    expect(frames.map((frame) => frame.malformed)).toEqual([false, true, false, false]);
    expect(frames[1].data).toBeNull();
    expect(frames[1].raw).toBe('{"chunk":"truncated');
    expect(frames[2]).toMatchObject({ event: "agent_step", data: { phase: "plan" } });
    expect(frames[3].data).toEqual({ chunk: "after" });
  });

  it("keeps non-JSON sentinels and non-object payloads out of the malformed count", async () => {
    const frames = await collect([
      "data: \n\n",
      'data: ["not","an","object"]\n\n',
      "data: [DONE]\n\n",
    ]);

    expect(frames[0]).toMatchObject({ data: {}, malformed: false });
    // Well-formed JSON that is not an object: usable by nobody, but not a
    // decoding failure either.
    expect(frames[1]).toMatchObject({ data: null, malformed: false });
    expect(frames[2].raw).toBe("[DONE]");
  });

  it("names unnamed frames 'message' and ignores frames with no data line", async () => {
    const frames = await collect([
      ": keep-alive comment\n\n",
      'event: progress\ndata: {"pct":10}\n\n',
      'data: {"pct":20}\n\n',
    ]);

    expect(frames).toHaveLength(2);
    expect(frames[0].event).toBe("progress");
    expect(frames[1].event).toBe("message");
  });

  it("still yields a final frame that arrives without its trailing blank line", async () => {
    const frames = await collect(['data: {"chunk":"a"}\n\n', 'data: {"chunk":"b"}']);

    expect(frames.map((frame) => frame.data)).toEqual([{ chunk: "a" }, { chunk: "b" }]);
  });
});

describe("streamChat frame skipping", () => {
  it("finishes an answer whose middle frame is malformed", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(sseResponse([
      'data: {"chunk":"앞부분"}\n\n',
      'data: {"chunk":"잘린\n\n',
      "data: not json at all\n\n",
      'data: {"chunk":"뒷부분"}\n\n',
      'data: {"agent":{"status":"ok","final_state":"DONE"}}\n\n',
      "data: [DONE]\n\n",
    ])));
    const onChunk = vi.fn();
    const onAgent = vi.fn();

    const result = await latticeApi.streamChat({ message: "hi" }, { onChunk, onAgent });

    expect(result.text).toBe("앞부분뒷부분");
    expect(onChunk).toHaveBeenCalledTimes(2);
    expect(onAgent).toHaveBeenCalledTimes(1);
    expect(result.malformedFrames).toBe(2);
  });

  it("does not count the [DONE] sentinel as a malformed frame", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(sseResponse([
      'data: {"chunk":"ok"}\n\n',
      "data: [DONE]\n\n",
    ])));

    const result = await latticeApi.streamChat({ message: "hi" }, {});

    expect(result.text).toBe("ok");
    expect(result.malformedFrames).toBe(0);
  });

  it("keeps a malformed agent_step frame from costing the answer", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(sseResponse([
      "event: agent_step\ndata: {broken\n\n",
      'event: agent_step\ndata: {"phase":"execute","event":"tool"}\n\n',
      'data: {"chunk":"done"}\n\n',
      "data: [DONE]\n\n",
    ])));
    const onAgentStep = vi.fn();

    const result = await latticeApi.streamChat({ message: "hi" }, { onAgentStep });

    expect(onAgentStep).toHaveBeenCalledTimes(1);
    expect(onAgentStep.mock.calls[0][0]).toMatchObject({ phase: "execute", event: "tool" });
    expect(result.text).toBe("done");
    expect(result.malformedFrames).toBe(1);
  });
});

describe("streamModelPrepare frame skipping", () => {
  it("still reports done after a malformed progress frame", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(sseResponse([
      'event: progress\ndata: {"pct":10}\n\n',
      "event: progress\ndata: {pct:oops}\n\n",
      'event: progress\ndata: {"pct":90}\n\n',
      'event: done\ndata: {"status":"ready","model":"demo"}\n\n',
    ])));
    const onProgress = vi.fn();
    const onDone = vi.fn();

    const result = await latticeApi.streamModelPrepare({ model: "demo" }, { onProgress, onDone });

    expect(onProgress).toHaveBeenCalledTimes(2);
    expect(onDone).toHaveBeenCalledWith({ status: "ready", model: "demo" });
    expect(result.ok).toBe(true);
    expect(result.data).toEqual({ status: "ready", model: "demo" });
    expect(result.malformedFrames).toBe(1);
  });
});

describe("readEventStream teardown", () => {
  it("drops a trailing fragment that never grew a data line", async () => {
    const frames = await collect([
      'data: {"chunk":"a"}\n\n',
      ": half a keep-alive comment with no data",
    ]);

    expect(frames).toHaveLength(1);
    expect(frames[0].data).toEqual({ chunk: "a" });
  });

  it("cancels the body when the consumer breaks early, even if cancel rejects", async () => {
    let cancelled = false;
    const encoder = new TextEncoder();
    const body = new ReadableStream<Uint8Array>({
      pull(controller) {
        controller.enqueue(encoder.encode('data: {"chunk":"more"}\n\n'));
      },
      cancel() {
        cancelled = true;
        return Promise.reject(new Error("cancel failed"));
      },
    });

    for await (const frame of readEventStream(body)) {
      expect(frame.data).toEqual({ chunk: "more" });
      break;
    }

    expect(cancelled).toBe(true);
    // Give the swallowed rejection a microtask to surface if it ever leaks.
    await new Promise((resolve) => setTimeout(resolve, 0));
  });

  it("tolerates a reader whose cancel throws synchronously after a completed read", async () => {
    const reader = {
      read: async () => ({ done: true as const, value: undefined }),
      cancel: () => {
        throw new Error("already released");
      },
    };
    const body = { getReader: () => reader } as unknown as ReadableStream<Uint8Array>;

    const frames = [];
    for await (const frame of readEventStream(body)) frames.push(frame);

    expect(frames).toEqual([]);
  });
});
