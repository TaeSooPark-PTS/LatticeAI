
export type SseFrame = {
  event: string;
  raw: string;
  data: Record<string, unknown> | null;
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
    return { event, raw, data: null, malformed: true };
  }
}

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
    const tail = buffer + decoder.decode();
    if (tail.trim()) {
      const frame = decodeFrame(tail);
      if (frame) yield frame;
    }
  } finally {
    try {
      void reader.cancel().catch(() => undefined);
    } catch {}
  }
}
