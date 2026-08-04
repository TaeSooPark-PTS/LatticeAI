import { afterEach, describe, expect, it, vi } from "vitest";

import { latticeApi } from "./client";
import { useAppStore } from "@/store/appStore";

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

/**
 * The rest of `latticeApi`: the request each method actually sends, and what a
 * caller is handed when the local service does not answer.
 *
 * These two properties are what the whole UI leans on. Every page renders from
 * `res.data` unconditionally — `data.matches.map(...)`, `data.nodes.length` —
 * so a failed request that returned `undefined`, or `{}` where a list was
 * declared, is a white screen rather than an empty state. The declared shape is
 * the contract, and it has to survive a 500, a network drop and a timeout.
 */

type Recorded = { url: URL; method: string; body: unknown; headers: Headers };

/**
 * `openapi-fetch` destructures `globalThis.fetch` when the client is *created*,
 * and `base.ts` caches one client per origin for the lifetime of the module. So
 * a `vi.stubGlobal("fetch", ...)` installed inside a test is never seen: the
 * cached client is still holding whichever function existed when the first
 * request went out.
 *
 * Installing one permanent dispatcher at module scope — before any client can
 * exist — and swapping only the responder behind it keeps the indirection the
 * tests need without reaching into `base.ts` to expose its cache.
 */
let respondWith: ((url: URL, init?: RequestInit) => Response) | null = null;
const dispatcherCalls: Recorded[] = [];
const realFetch = globalThis.fetch;

globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
  if (!respondWith) return realFetch(input as RequestInfo, init);
  // `openapi-fetch` hands fetch a fully-built `Request`, so method, headers and
  // body are on the request rather than on `init`. Reading only `init` reported
  // every POST as a bodiless GET with no headers.
  const request = input instanceof Request ? input : null;
  const raw = request ? request.url : typeof input === "string" ? input : (input as URL).href;
  const url = new URL(raw, "http://localhost");
  const rawBody = request ? await request.clone().text() : init?.body ? String(init.body) : "";
  dispatcherCalls.push({
    url,
    method: (request?.method || init?.method || "GET").toUpperCase(),
    body: rawBody ? JSON.parse(rawBody) : undefined,
    headers: request ? request.headers : new Headers(init?.headers as HeadersInit),
  });
  return respondWith(url, init);
}) as typeof globalThis.fetch;

/**
 * A base URL is required, not optional, in jsdom.
 *
 * In the browser `apiBase()` resolves to "" — same origin — and the relative
 * path is resolved against the document. jsdom's fetch has no such base, so
 * `openapi-fetch` throws "Failed to parse URL" while building the Request and
 * every call comes back as status 0 without a request ever being made. Naming
 * an absolute origin is what makes the request observable; `base.ts` keys its
 * client cache on this value, so the client for it is built after the
 * dispatcher above is installed.
 */
const TEST_ORIGIN = "http://localhost";

function recordFetch(respond: (url: URL) => Response) {
  useAppStore.setState({ apiBase: TEST_ORIGIN });
  dispatcherCalls.length = 0;
  respondWith = respond;
  return dispatcherCalls;
}

function failFetchWith(error: Error) {
  useAppStore.setState({ apiBase: TEST_ORIGIN });
  dispatcherCalls.length = 0;
  respondWith = () => {
    throw error;
  };
  return dispatcherCalls;
}

afterEach(() => {
  respondWith = null;
  dispatcherCalls.length = 0;
  useAppStore.setState({ apiBase: "" });
});

const jsonResponse = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });

describe("what the client sends", () => {
  it("puts list-shaped query parameters on the URL, not in a body", async () => {
    const calls = recordFetch(() => jsonResponse({ nodes: [], edges: [] }));

    await latticeApi.graphPreview(12);

    expect(calls).toHaveLength(1);
    expect(calls[0].method).toBe("GET");
    expect(calls[0].url.pathname).toBe("/knowledge-graph/graph");
    expect(calls[0].url.searchParams.get("limit")).toBe("12");
    expect(calls[0].body).toBeUndefined();
  });

  it("percent-encodes an id that would otherwise change the path", async () => {
    // Proposal ids come from the server and are not guaranteed to be
    // path-safe; an unescaped "/" would silently address a different route.
    const calls = recordFetch(() => jsonResponse({}));

    await latticeApi.approveProposal("item/42 with space");

    expect(calls[0].url.pathname).toBe("/api/proposals/item%2F42%20with%20space/approve");
  });

  it("sends the language header on an ordinary API call", async () => {
    const calls = recordFetch(() => jsonResponse({}));

    await latticeApi.health();

    expect(calls[0].headers.get("X-Lattice-Language")).toBeTruthy();
  });
});

describe("what the caller is handed when the service does not answer", () => {
  it("returns the declared shape, emptied, on a server error", async () => {
    recordFetch(() => jsonResponse({ detail: "boom" }, 500));

    const res = await latticeApi.graph();

    expect(res.ok).toBe(false);
    expect(res.status).toBe(500);
    expect(res.source).toBe("unavailable");
    // Not `undefined`, and not `{}` — the page maps over these.
    expect(res.data).toEqual({ nodes: [], edges: [] });
    expect(res.error).toBeTruthy();
  });

  it("returns the declared shape, emptied, when the service is unreachable", async () => {
    failFetchWith(new TypeError("Failed to fetch"));

    const res = await latticeApi.memoryManager();

    expect(res.ok).toBe(false);
    expect(res.status).toBe(0);
    expect(res.data).toEqual({ sources: [], tiers: [], usage: {} });
    expect(res.error).toBeTruthy();
  });

  it("keeps nested list keys as lists rather than dropping them", async () => {
    failFetchWith(new TypeError("Failed to fetch"));

    const res = await latticeApi.memoryBrainBrief();

    expect(Array.isArray(res.data.next_actions)).toBe(true);
    expect(Array.isArray(res.data.evidence)).toBe(true);
  });

  it("surfaces a live payload untouched when the call succeeds", async () => {
    recordFetch(() => jsonResponse({ nodes: [{ id: "a" }], edges: [] }));

    const res = await latticeApi.graph();

    expect(res.ok).toBe(true);
    expect(res.source).toBe("live");
    expect(res.data).toEqual({ nodes: [{ id: "a" }], edges: [] });
  });
});

describe("hybridSearch", () => {
  it("normalises a `results` payload to the `matches` the UI reads", async () => {
    // The endpoint has answered under both names across versions; the UI only
    // knows `matches`, so a `results` payload rendered as "nothing found".
    recordFetch(() => jsonResponse({ results: [{ id: "n1" }] }));

    const res = await latticeApi.hybridSearch("workspace");

    expect(res.data.matches).toEqual([{ id: "n1" }]);
  });

  it("leaves a payload that already has matches alone", async () => {
    recordFetch(() => jsonResponse({ matches: [{ id: "n2" }], results: [{ id: "ignored" }] }));

    const res = await latticeApi.hybridSearch("workspace");

    expect(res.data.matches).toEqual([{ id: "n2" }]);
  });

  it("passes weights through only when the caller supplied them", async () => {
    const calls = recordFetch(() => jsonResponse({ matches: [] }));

    await latticeApi.hybridSearch("a");
    await latticeApi.hybridSearch("b", { vector: 0.7 });

    expect(calls[0].body).toEqual({ query: "a" });
    expect(calls[1].body).toEqual({ query: "b", weights: { vector: 0.7 } });
  });

  it("still returns an empty match list when the search fails", async () => {
    recordFetch(() => jsonResponse({ detail: "no index" }, 503));

    const res = await latticeApi.hybridSearch("anything");

    expect(res.ok).toBe(false);
    expect(res.data.matches).toEqual([]);
  });
});

describe("desktopBackendStatus", () => {
  it("says plainly that it is a desktop-only reading, outside the desktop shell", async () => {
    // A browser tab has no Tauri bridge. Reporting "unavailable" with a reason
    // is the honest answer; returning a fabricated ok would put a green dot on
    // a backend nobody asked.
    const res = await latticeApi.desktopBackendStatus();

    expect(res.ok).toBe(false);
    expect(res.source).toBe("unavailable");
    expect(res.error).toMatch(/Tauri/);
  });

  it("reports the status the desktop bridge returns", async () => {
    (window as unknown as { __TAURI__?: unknown }).__TAURI__ = {
      core: { invoke: vi.fn().mockResolvedValue({ running: true, port: 8765 }) },
    };
    try {
      const res = await latticeApi.desktopBackendStatus();
      expect(res.ok).toBe(true);
      expect(res.data).toEqual({ running: true, port: 8765 });
    } finally {
      delete (window as unknown as { __TAURI__?: unknown }).__TAURI__;
    }
  });
});
