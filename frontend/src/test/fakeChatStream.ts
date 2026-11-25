
export type FakeChatFrame =
  | { kind: "chunk"; text: string }
  | { kind: "trace"; trace: unknown }
  | { kind: "agent"; agent: Record<string, unknown> }
  | { kind: "agentStep"; step: Record<string, unknown> }
  | { kind: "hybridContext"; frame: Record<string, unknown> }
  | { kind: "hybridDone"; frame: Record<string, unknown> };

export type FakeChatHandlers = {
  signal?: AbortSignal;
  onChunk?: (delta: string, fullText: string) => void;
  onTrace?: (trace: unknown) => void;
  onAgent?: (agent: Record<string, unknown>) => void;
  onAgentStep?: (step: Record<string, unknown>) => void;
  onHybridContext?: (frame: Record<string, unknown>) => void;
  onHybridDone?: (frame: Record<string, unknown>) => void;
};

export type FakeChatResult = {
  source: string;
  text: string;
  trace: unknown;
  agent: Record<string, unknown> | null;
  contextQuality?: unknown;
  grounding?: unknown;
  hybridContext?: unknown;
  hybridDone?: unknown;
  malformedFrames: number;
  error?: string;
};

export type FakeChatStream = {
  streamChat: (body: Record<string, unknown>, handlers?: FakeChatHandlers) => Promise<FakeChatResult>;
  started: Promise<void>;
  resume: () => void;
  calls: Array<Record<string, unknown>>;
};

export function fakeChatStream({
  frames = [],
  result = {},
  pauseAfter,
  error,
}: {
  frames?: FakeChatFrame[];
  result?: Partial<FakeChatResult>;
  pauseAfter?: number;
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
      if (handlers.signal?.aborted) break;
      const frame = frames[index]!;
      if (frame.kind === "chunk") {
        text += frame.text;
        handlers.onChunk?.(frame.text, text);
      } else if (frame.kind === "trace") {
        trace = frame.trace;
        handlers.onTrace?.(frame.trace);
      } else if (frame.kind === "agent") {
        agent = frame.agent;
        handlers.onAgent?.(frame.agent);
      } else if (frame.kind === "hybridContext") {
        handlers.onHybridContext?.(frame.frame);
      } else if (frame.kind === "hybridDone") {
        if (typeof frame.frame.answer === "string" && frame.frame.answer) {
          text = frame.frame.answer;
        }
        handlers.onHybridDone?.(frame.frame);
      } else {
        handlers.onAgentStep?.(frame.step);
      }
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

export function abortError(): DOMException {
  return new DOMException("The user aborted a request.", "AbortError");
}
