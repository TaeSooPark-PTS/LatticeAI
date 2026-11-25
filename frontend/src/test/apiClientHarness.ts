import { useAppStore } from "@/store/appStore";

export type Recorded = { url: URL; method: string; body: unknown; headers: Headers };

export type Responder = (
  url: URL,
  signal?: AbortSignal | null,
) => Response | Promise<Response>;

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
  const request = input instanceof Request ? input : null;
  const raw = request ? request.url : typeof input === "string" ? input : (input as URL).href;
  const url = new URL(raw, "http://localhost");
  const rawBody =
    request ? await request.clone().text() : typeof init?.body === "string" ? init.body : "";
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

export const TEST_ORIGIN = "http://localhost";

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
