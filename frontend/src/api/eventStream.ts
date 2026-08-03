/**
 * One shared `text/event-stream` reader for every streaming endpoint.
 *
 * Each streaming call used to decode frames and call `JSON.parse` inline with
 * no guard, so a single truncated or non-JSON frame threw out of the read loop
 * and discarded the rest of an answer that was already half-rendered. Parsing
 * lives here instead: a frame that cannot be decoded is reported as
 * `malformed` and the stream keeps going.
 */

/** One decoded frame of a `text/event-stream` body. */
export type SseFrame = {
  /** `event:` name. Unnamed frames report `"message"`, like `EventSource`. */
  event: string;
  /** Trimmed `data:` payload, verbatim — sentinels such as `[DONE]` survive. */
  raw: string;
  /**
   * `raw` decoded as a JSON object: `{}` for an empty payload, and `null` when
   * the payload is not a JSON *object* (a sentinel, an array, or unparseable).
   */
  data: Record<string, unknown> | null;
  /** `raw` was non-empty and `JSON.parse` threw. Callers skip and count these. */
  malformed: boolean;
};

function decodeFrame(part: string): SseFrame | null {
  const lines = part.split("\n");
  const dataLine = lines.find((line) => line.startsWith("data:"));
  if (!dataLine) return null;
  const event = lines.find((line) => line.startsWith("event:"))?.slice(6).trim() || "message";
  const raw = dataLine.slice(5).trim();
  if (!raw) return { event, raw, data: {}, malformed: false };
  try {
    const parsed: unknown = JSON.parse(raw);
    const isObject = typeof parsed === "object" && parsed !== null && !Array.isArray(parsed);
    return { event, raw, data: isObject ? parsed as Record<string, unknown> : null, malformed: false };
  } catch {
    // Per-frame, deliberately: one bad frame must not cost the whole answer.
    return { event, raw, data: null, malformed: true };
  }
}

/**
 * Yields frames as they arrive. Breaking out of the loop (for example on a
 * `[DONE]` sentinel) cancels the body instead of leaving the connection open.
 */
export async function* readEventStream(
  body: ReadableStream<Uint8Array>,
): AsyncGenerator<SseFrame, void, void> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const parts = buffer.split("\n\n");
      buffer = parts.pop() || "";
      for (const part of parts) {
        const frame = decodeFrame(part);
        if (frame) yield frame;
      }
    }
    // A body that ends without the trailing blank line still carries a frame;
    // dropping it silently loses the same content a parse throw used to.
    const tail = buffer + decoder.decode();
    if (tail.trim()) {
      const frame = decodeFrame(tail);
      if (frame) yield frame;
    }
  } finally {
    try {
      void reader.cancel().catch(() => undefined);
    } catch {
      // Already released by a completed read; nothing left to close.
    }
  }
}
