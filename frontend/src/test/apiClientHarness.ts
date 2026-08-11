/**
 * Fetch harness for the `latticeApi` suites.
 *
 * `openapi-fetch` destructures `globalThis.fetch` when the client is *created*,
 * and `base.ts` caches one client per origin for the lifetime of the module. So
 * a `vi.stubGlobal("fetch", ...)` installed inside a test is never seen: the
 * cached client is still holding whichever function existed when the first
 * request went out.
 *
 * Installing one permanent dispatcher at module scope — before any client can
 * exist — and swapping only the responder behind it keeps the indirection the
 * tests need without reaching into `base.ts` to expose its cache. Importing
 * this module is what installs it, so every `client.*.test.ts` gets the same
 * dispatcher its own suite would have built.
 */
import { useAppStore } from "@/store/appStore";

export type Recorded = { url: URL; method: string; body: unknown; headers: Headers };

export type Responder = (
  url: URL,
  signal?: AbortSignal | null,
) => Response | Promise<Response>;

// A real SSE body: the fetch stub returns a Response whose ReadableStream the
// parser consumes exactly like the live /chat endpoint.
export function sseResponse(frames: string[]): Response {
  return new Response(frames.join(""), {
    status: 200,
    headers: { "Content-Type": "text/event-stream" },
  });
}

export const jsonResponse = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });

let respondWith: Responder | null = null;
export const dispatcherCalls: Recorded[] = [];
const realFetch = globalThis.fetch;

globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
  if (!respondWith) return realFetch(input as RequestInfo, init);
  // `openapi-fetch` hands fetch a fully-built `Request`, so method, headers and
  // body are on the request rather than on `init`. Reading only `init` reported
  // every POST as a bodiless GET with no headers.
  const request = input instanceof Request ? input : null;
  const raw = request ? request.url : typeof input === "string" ? input : (input as URL).href;
  const url = new URL(raw, "http://localhost");
  const rawBody =
    request ? await request.clone().text() : typeof init?.body === "string" ? init.body : "";
  // JSON bodies are recorded parsed; a multipart upload keeps its FormData so
  // the test can look at the parts instead of a boundary string.
  let body: unknown = init?.body instanceof FormData ? init.body : undefined;
  if (rawBody) {
    try {
      body = JSON.parse(rawBody);
    } catch {
      body = rawBody;
    }
  }
  dispatcherCalls.push({
    url,
    method: (request?.method || init?.method || "GET").toUpperCase(),
    body,
    headers: request ? request.headers : new Headers(init?.headers as HeadersInit),
  });
  return respondWith(url, request?.signal ?? init?.signal);
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
export const TEST_ORIGIN = "http://localhost";

/** Point the dispatcher at a responder without touching the call log. */
export function setResponder(respond: Responder): void {
  respondWith = respond;
}

export function recordFetch(respond: (url: URL) => Response) {
  useAppStore.setState({ apiBase: TEST_ORIGIN });
  dispatcherCalls.length = 0;
  respondWith = respond;
  return dispatcherCalls;
}

export function failFetchWith(error: Error) {
  useAppStore.setState({ apiBase: TEST_ORIGIN });
  dispatcherCalls.length = 0;
  respondWith = () => {
    throw error;
  };
  return dispatcherCalls;
}

export function resetDispatcher(): void {
  respondWith = null;
  dispatcherCalls.length = 0;
  useAppStore.setState({ apiBase: "" });
}
