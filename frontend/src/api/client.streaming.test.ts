import { afterEach, describe, expect, it, vi } from "vitest";

import { latticeApi } from "./client";
import { jsonResponse, resetDispatcher, sseResponse } from "@/test/apiClientHarness";

/**
 * The streaming wrappers: what `streamChat` and `streamModelPrepare` make of
 * the frames on the wire, and what they hand back when the wire says no.
 */

afterEach(() => {
  vi.unstubAllGlobals();
});

afterEach(resetDispatcher);

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

  it("attaches hybrid_context and hybrid_done frames without rendering them as chunks", async () => {
    const onHybridContext = vi.fn();
    const onHybridDone = vi.fn();
    const onChunk = vi.fn();
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(sseResponse([
      'data: {"type":"hybrid_context","node_ids":["n1","n2"],"keywords":["release"]}\n\n',
      'data: {"type":"token","chunk":"클라우드 ","text":"클라우드 "}\n\n',
      'data: {"type":"hybrid_done","chunk":"","answer":"클라우드 답변","provider":"antigravity","model":"gemini-3.7-flash","sent_node_ids":["n1","n2"],"kg_expansion":{"status":"staged","plan":{"provenance":{"candidate_count":2}}}}\n\n',
      "data: [DONE]\n\n",
    ])));

    const result = await latticeApi.streamChat(
      { message: "hi" },
      { onHybridContext, onHybridDone, onChunk },
    );

    expect(onHybridContext).toHaveBeenCalledWith(expect.objectContaining({
      type: "hybrid_context",
      node_ids: ["n1", "n2"],
    }));
    expect(onHybridDone).toHaveBeenCalledWith(expect.objectContaining({
      type: "hybrid_done",
      answer: "클라우드 답변",
      model: "gemini-3.7-flash",
    }));
    expect(onChunk).toHaveBeenCalledWith("클라우드 ", "클라우드 ");
    expect(result.text).toBe("클라우드 답변");
    expect(result.hybridContext).toMatchObject({ type: "hybrid_context" });
    expect(result.hybridDone).toMatchObject({ type: "hybrid_done", provider: "antigravity" });
  });

  it("accepts named hybrid events and ignores an unknown type without dropping later chunks", async () => {
    const onHybridContext = vi.fn();
    const onHybridDone = vi.fn();
    const onChunk = vi.fn();
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(sseResponse([
      'event: hybrid_context\ndata: {"node_ids":["n9"]}\n\n',
      'data: {"type":"future_lane","note":"ignore me"}\n\n',
      'data: {"chunk":"로컬 답"}\n\n',
      'event: hybrid_done\ndata: {"answer":"끝","model":"x"}\n\n',
      "data: [DONE]\n\n",
    ])));

    const result = await latticeApi.streamChat(
      { message: "hi" },
      { onHybridContext, onHybridDone, onChunk },
    );

    expect(onHybridContext).toHaveBeenCalledWith({ node_ids: ["n9"] });
    expect(onHybridDone).toHaveBeenCalledWith({ answer: "끝", model: "x" });
    expect(onChunk).toHaveBeenCalledWith("로컬 답", "로컬 답");
    expect(result.text).toBe("끝");
  });

  it("ignores a named hybrid frame whose payload is not an object", async () => {
    const onHybridContext = vi.fn();
    const onHybridDone = vi.fn();
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(sseResponse([
      "event: hybrid_context\ndata: [1]\n\n",
      "event: hybrid_done\ndata: [2]\n\n",
      'data: {"chunk":"ok"}\n\n',
      "data: [DONE]\n\n",
    ])));

    const result = await latticeApi.streamChat(
      { message: "hi" },
      { onHybridContext, onHybridDone },
    );

    expect(onHybridContext).not.toHaveBeenCalled();
    expect(onHybridDone).not.toHaveBeenCalled();
    expect(result.text).toBe("ok");
  });

  it("keeps streamed tokens when hybrid_done carries no answer", async () => {
    const onHybridDone = vi.fn();
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(sseResponse([
      'data: {"chunk":"토큰"}\n\n',
      'data: {"type":"hybrid_done","chunk":"","answer":"","model":"m"}\n\n',
      "data: [DONE]\n\n",
    ])));

    const result = await latticeApi.streamChat({ message: "hi" }, { onHybridDone });

    expect(onHybridDone).toHaveBeenCalled();
    expect(result.text).toBe("토큰");
    expect(result.hybridDone).toMatchObject({ model: "m" });
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

describe("streamChat error and edge paths", () => {
  it("reports a JSON error payload instead of pretending to stream", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse({ error: "no model" }, 503)));

    const result = await latticeApi.streamChat({ message: "hi" });

    expect(result.text).toBe("");
    expect(result.error).toBe("no model");
    expect(result.malformedFrames).toBe(0);
  });

  it("falls back to detail, then statusText, when the error field is missing", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse({ detail: "gone" }, 404)));
    const withDetail = await latticeApi.streamChat({ message: "hi" });
    expect(withDetail.error).toBe("gone");

    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(
      new Response("plain text", { status: 200, statusText: "OK", headers: { "Content-Type": "text/plain" } }),
    ));
    const nonJson = await latticeApi.streamChat({ message: "hi" });
    expect(nonJson.error).toBe("OK");
  });

  it("treats an event-stream response without a body as an error", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(
      new Response(null, { status: 200, statusText: "Empty", headers: { "Content-Type": "text/event-stream" } }),
    ));

    const result = await latticeApi.streamChat({ message: "hi" });

    expect(result.text).toBe("");
    expect(result.error).toBe("Empty");
  });

  it("treats an OK answer with no content-type header as a non-stream", async () => {
    // A raw byte body keeps undici from inventing a content-type, so the
    // header lookup really does come back null here.
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(
      new Response(new TextEncoder().encode("raw"), { status: 200, statusText: "OK" }),
    ));

    const result = await latticeApi.streamChat({ message: "hi" });

    expect(result.text).toBe("");
    expect(result.error).toBe("OK");
  });

  it("collects trace, quality, grounding and agent without any handlers wired", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(sseResponse([
      'data: {"chunk":"부분"}\n\n',
      'data: {"text":"추가"}\n\n',
      'data: {"nothing_here":1}\n\n',
      "data: [1,2,3]\n\n",
      'data: {"trace":{"nodes":2},"context_quality":{"level":"strong"},"grounding":{"used":true}}\n\n',
      'data: {"context_quality":"not-an-object","grounding":7,"agent":"not-an-object"}\n\n',
      'event: agent_step\ndata: {"phase":"plan"}\n\n',
      'data: {"agent":{"status":"ok","final_state":"DONE"}}\n\n',
    ])));

    // No [DONE] sentinel: the loop must end with the stream and still return.
    const result = await latticeApi.streamChat({ message: "hi" });

    expect(result.text).toBe("부분추가");
    expect(result.trace).toEqual({ nodes: 2 });
    expect(result.contextQuality).toEqual({ level: "strong" });
    expect(result.grounding).toEqual({ used: true });
    expect(result.agent).toMatchObject({ final_state: "DONE" });
    expect(result.malformedFrames).toBe(0);
  });
});

describe("streamModelPrepare error and edge paths", () => {
  it("hands a structured refusal to onError before any stream exists", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse({
      detail: { status: "needs_download", user_message: "내려받기가 필요해요" },
    }, 409)));
    const onError = vi.fn();

    const result = await latticeApi.streamModelPrepare({ model: "m1" }, { onError });

    expect(result.ok).toBe(false);
    expect(result.status).toBe(409);
    expect(result.error).toBe("내려받기가 필요해요");
    // The structured detail is spread over the base shape, so its own status
    // wins while the friendly message is preserved.
    expect(onError).toHaveBeenCalledWith({
      status: "needs_download",
      user_message: "내려받기가 필요해요",
    });
  });

  it("survives a refusal with no handlers and no structured detail", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse({ message: "flat" }, 500)));
    const flat = await latticeApi.streamModelPrepare({ model: "m1" });
    expect(flat.ok).toBe(false);
    expect(flat.error).toBe("flat");
    expect(flat.data).toEqual({ message: "flat" });

    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(
      new Response("not json", { status: 502, statusText: "Bad Gateway", headers: { "Content-Type": "text/plain" } }),
    ));
    const nonJson = await latticeApi.streamModelPrepare({ model: "m1" });
    expect(nonJson.error).toBe("Bad Gateway");
    expect(nonJson.data).toEqual({});
  });

  it("treats an event-stream response without a body as a refusal", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(
      new Response(null, { status: 200, statusText: "Empty", headers: { "Content-Type": "text/event-stream" } }),
    ));

    const result = await latticeApi.streamModelPrepare({ model: "m1" });

    expect(result.ok).toBe(false);
    expect(result.error).toBe("Empty");
  });

  it("still calls onError when an OK answer carries no content-type header", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(
      new Response(new TextEncoder().encode("raw"), { status: 200, statusText: "Down" }),
    ));
    const onError = vi.fn();

    const result = await latticeApi.streamModelPrepare({ model: "m1" }, { onError });

    expect(result.ok).toBe(false);
    expect(result.data).toEqual({});
    expect(onError).toHaveBeenCalledWith({ status: "error", user_message: "Down" });
  });

  it("ignores a progress frame whose payload is valid JSON but not an object", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(sseResponse([
      "event: progress\ndata: [10]\n\n",
      'event: done\ndata: {"status":"ready"}\n\n',
    ])));
    const onProgress = vi.fn();

    const result = await latticeApi.streamModelPrepare({ model: "m1" }, { onProgress });

    expect(onProgress).toHaveBeenCalledWith({});
    expect(result.ok).toBe(true);
  });

  it("stops on an in-stream error frame and unwraps its detail", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(sseResponse([
      'event: progress\ndata: {"pct":10}\n\n',
      'event: error\ndata: {"detail":{"user_message":"디스크가 가득 찼어요"},"status_code":507}\n\n',
    ])));
    const onError = vi.fn();

    const result = await latticeApi.streamModelPrepare({ model: "m1" }, { onError });

    expect(result.ok).toBe(false);
    expect(result.status).toBe(507);
    expect(result.error).toBe("디스크가 가득 찼어요");
    expect(onError).toHaveBeenCalledWith({ user_message: "디스크가 가득 찼어요" });
  });

  it("uses the frame itself when the error carries no detail object, defaulting to 500", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(sseResponse([
      'event: error\ndata: {"user_message":"준비 실패"}\n\n',
    ])));

    // No handlers at all: onError?./onProgress?./onDone?. must tolerate it.
    const result = await latticeApi.streamModelPrepare({ model: "m1" });

    expect(result.ok).toBe(false);
    expect(result.status).toBe(500);
    expect(result.data).toEqual({ user_message: "준비 실패" });
  });

  it("finishes a handler-less progress stream, including empty data frames", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(sseResponse([
      'event: progress\ndata: {"pct":50}\n\n',
      "event: progress\ndata: \n\n",
      'event: done\ndata: {"status":"ready"}\n\n',
    ])));

    const result = await latticeApi.streamModelPrepare({ model: "m1" });

    expect(result.ok).toBe(true);
    expect(result.data).toEqual({ status: "ready" });
  });
});
