/**
 * A scripted stand-in for `latticeApi.streamChat`.
 *
 * The streaming chat path was the largest untested surface in the product
 * (`useBrainChat` sat at 12% through 10.8.0) for one reason: every assertion
 * about it needs a *stream* — frames arriving over time, in a chosen order,
 * with the ability to stop between two of them and look at what the UI has.
 * A `mockResolvedValue` cannot express that, so the branches that matter —
 * partial text, a trace pulse, an approval pause, an abort mid-answer — were
 * never exercised.
 *
 * This is that missing harness. A test writes the frames it wants; the fake
 * replays them into the same handler callbacks the real reader calls, in
 * order, awaiting between each so React can flush. `pauseAfter` stops the
 * replay so the test can inspect mid-stream state, and `resume()` continues.
 *
 * It deliberately mirrors the *contract* of `streamChat`, not its transport:
 * no `ReadableStream`, no `text/event-stream` parsing. Frame decoding is the
 * job of `readEventStream`, which has its own tests; what is untested here is
 * everything the hook does with the frames once they arrive.
 */

export type FakeChatFrame =
  | { kind: "chunk"; text: string }
  | { kind: "trace"; trace: unknown }
  | { kind: "agent"; agent: Record<string, unknown> }
  | { kind: "agentStep"; step: Record<string, unknown> };

export type FakeChatHandlers = {
  signal?: AbortSignal;
  onChunk?: (delta: string, fullText: string) => void;
  onTrace?: (trace: unknown) => void;
  onAgent?: (agent: Record<string, unknown>) => void;
  onAgentStep?: (step: Record<string, unknown>) => void;
};

export type FakeChatResult = {
  source: string;
  text: string;
  trace: unknown;
  agent: Record<string, unknown> | null;
  contextQuality?: unknown;
  grounding?: unknown;
  malformedFrames: number;
  error?: string;
};

export type FakeChatStream = {
  /** Drop-in for `latticeApi.streamChat`. */
  streamChat: (body: Record<string, unknown>, handlers?: FakeChatHandlers) => Promise<FakeChatResult>;
  /** Resolves once the replay has reached `pauseAfter` (or finished). */
  started: Promise<void>;
  /** Continue a replay parked at `pauseAfter`. */
  resume: () => void;
  /** Bodies the hook sent, in order — one per send. */
  calls: Array<Record<string, unknown>>;
};

export function fakeChatStream({
  frames = [],
  result = {},
  pauseAfter,
  error,
}: {
  frames?: FakeChatFrame[];
  /** Trailer values the reader would have collected (context_quality, grounding, …). */
  result?: Partial<FakeChatResult>;
  /** Park the replay after this many frames until `resume()` is called. */
  pauseAfter?: number;
  /** Throw instead of resolving — an abort is `DOMException("…", "AbortError")`. */
  error?: unknown;
} = {}): FakeChatStream {
  const calls: Array<Record<string, unknown>> = [];
  let releasePause: (() => void) | null = null;
  let announceStarted: (() => void) | null = null;
  const started = new Promise<void>((resolve) => {
    announceStarted = resolve;
  });
  const paused = new Promise<void>((resolve) => {
    releasePause = resolve;
  });

  async function streamChat(body: Record<string, unknown>, handlers: FakeChatHandlers = {}) {
    calls.push(body);
    let text = "";
    let trace: unknown = null;
    let agent: Record<string, unknown> | null = null;

    for (let index = 0; index < frames.length; index += 1) {
      // The real reader can be aborted between any two frames; so can this one.
      if (handlers.signal?.aborted) break;
      const frame = frames[index];
      if (frame.kind === "chunk") {
        text += frame.text;
        handlers.onChunk?.(frame.text, text);
      } else if (frame.kind === "trace") {
        trace = frame.trace;
        handlers.onTrace?.(frame.trace);
      } else if (frame.kind === "agent") {
        agent = frame.agent;
        handlers.onAgent?.(frame.agent);
      } else {
        handlers.onAgentStep?.(frame.step);
      }
      // Yield so React can flush the state update this frame caused, exactly
      // as a real network frame would.
      await Promise.resolve();
      if (pauseAfter !== undefined && index + 1 === pauseAfter) {
        announceStarted?.();
        announceStarted = null;
        await paused;
      }
    }

    announceStarted?.();
    announceStarted = null;
    if (error) throw error;
    return {
      source: "live",
      text,
      trace,
      agent,
      malformedFrames: 0,
      ...result,
    } as FakeChatResult;
  }

  return {
    streamChat,
    started,
    resume: () => releasePause?.(),
    calls,
  };
}

/** The abort the browser raises when a fetch signal fires mid-stream. */
export function abortError(): DOMException {
  return new DOMException("The user aborted a request.", "AbortError");
}
