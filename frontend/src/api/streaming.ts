import {
  apiBase,
  type ApiResult,
  friendlyCaughtError,
  friendlyError,
  workspaceHeaders,
} from "./base";
import { readEventStream } from "./eventStream";

export type ChatCreatedFile = {
  path: string;
  filename: string;
  bytes: number;
  action?: string;
};

export type ChatAgentPayload = {
  status?: string;
  response?: string;
  created_files?: ChatCreatedFile[];
  routed_to_agent?: boolean;
  action_route?: string;
  final_state?: string;
  generation?: { repaired?: boolean; attempts?: unknown[] };
  artifacts?: Array<Record<string, unknown>>;
  run_id?: string;
  approval?: { token?: string; expires_at?: string; plan_summary?: string };
  plan?: Record<string, unknown>;
  steps?: unknown[];
  loop?: {
    repairs?: Record<string, number>;
    parse_errors?: number;
    parse_recovered?: number;
  };
  explanation?: {
    code?: string;
    ok?: boolean;
    headline?: Record<string, string>;
    details?: Array<Record<string, string>>;
    model_strain?: Record<string, unknown>;
  };
  brain_ingest?: Record<string, unknown> | Array<Record<string, unknown>>;
};

export type ChatEventHandlers = {
  onChunk?: (delta: string, fullText: string) => void;
  onTrace?: (trace: unknown) => void;
  onAgent?: (agent: ChatAgentPayload) => void;
  onAgentStep?: (step: Record<string, unknown>) => void;
  onHybridContext?: (frame: Record<string, unknown>) => void;
  onHybridDone?: (frame: Record<string, unknown>) => void;
  signal?: AbortSignal;
};

export type ModelPrepareHandlers = {
  onProgress?: (data: Record<string, unknown>) => void;
  onDone?: (data: Record<string, unknown>) => void;
  onError?: (data: Record<string, unknown>) => void;
  signal?: AbortSignal;
};

export async function streamModelPrepare(
  body: { model: string; engine?: string; allow_download?: boolean },
  handlers: ModelPrepareHandlers = {},
) {
  const base = await apiBase();
  const res = await fetch(`${base}/engines/prepare-model/stream`, {
    method: "POST",
    credentials: "include",
    signal: handlers.signal,
    headers: {
      "Content-Type": "application/json",
      Accept: "text/event-stream",
      ...workspaceHeaders(),
    } satisfies HeadersInit,
    body: JSON.stringify({ engine: null, allow_download: false, ...body }),
  });
  if (!res.ok || !res.body || !(res.headers.get("content-type") || "").includes("text/event-stream")) {
    const payload = await res.json().catch(() => null);
    const detail = payload?.detail && typeof payload.detail === "object" ? payload.detail : payload;
    const message = friendlyError(payload, res.statusText);
    handlers.onError?.({ status: "error", user_message: message, ...(detail || {}) });
    return { source: "live" as const, ok: false, status: res.status, data: detail || {}, error: message, malformedFrames: 0 };
  }
  let finalData: Record<string, unknown> = {};
  let malformedFrames = 0;
  for await (const frame of readEventStream(res.body)) {
    if (frame.malformed) {
      malformedFrames += 1;
      continue;
    }
    const data = frame.data || {};
    if (frame.event === "progress") handlers.onProgress?.(data);
    if (frame.event === "error") {
      const detail = typeof data.detail === "object" && data.detail !== null ? data.detail as Record<string, unknown> : data;
      handlers.onError?.(detail);
      return { source: "live" as const, ok: false, status: Number(data.status_code || 500), data: detail, error: friendlyError({ detail }, "Model setup failed"), malformedFrames };
    }
    if (frame.event === "done") {
      finalData = data;
      handlers.onDone?.(data);
    }
  }
  return { source: "live" as const, ok: true, status: 200, data: finalData, malformedFrames };
}

export async function streamChat(body: Record<string, unknown>, handlers: ChatEventHandlers = {}) {
  const base = await apiBase();
  const res = await fetch(`${base}/chat`, {
    method: "POST",
    credentials: "include",
    signal: handlers.signal,
    headers: {
      "Content-Type": "application/json",
      Accept: "text/event-stream",
      ...workspaceHeaders(),
    } satisfies HeadersInit,
    body: JSON.stringify({ stream: true, max_tokens: 2048, temperature: 0.2, ...body }),
  });
  if (!res.ok || !res.body || !(res.headers.get("content-type") || "").includes("text/event-stream")) {
    const payload = await res.json().catch(() => null);
    return { source: "live", text: "", trace: null, error: payload?.error || payload?.detail || res.statusText, malformedFrames: 0 };
  }
  let text = "";
  let trace: unknown = null;
  let agent: ChatAgentPayload | null = null;
  let contextQuality: unknown = null;
  let grounding: unknown = null;
  let hybridContext: unknown = null;
  let hybridDone: unknown = null;
  let malformedFrames = 0;
  for await (const frame of readEventStream(res.body)) {
    if (frame.event === "message" && frame.raw === "[DONE]") {
      return { source: "live", text, trace, agent, contextQuality, grounding, hybridContext, hybridDone, malformedFrames };
    }
    if (frame.malformed) {
      malformedFrames += 1;
      continue;
    }
    if (frame.event === "agent_step") {
      if (frame.data) handlers.onAgentStep?.(frame.data);
      continue;
    }
    const data = frame.data;
    const frameType = frame.event !== "message"
      ? frame.event
      : typeof data?.type === "string" ? data.type : "";
    if (frameType === "hybrid_context" && data) {
      hybridContext = data;
      handlers.onHybridContext?.(data);
      continue;
    }
    if (frameType === "hybrid_done" && data) {
      hybridDone = data;
      if (typeof data.answer === "string" && data.answer) text = data.answer;
      handlers.onHybridDone?.(data);
      continue;
    }
    if (frame.event !== "message") continue;
    if (!data) continue;
    const delta = (data.chunk || data.text || "") as string;
    if (delta) {
      text += delta;
      handlers.onChunk?.(delta, text);
    }
    if (data.trace) {
      trace = data.trace;
      handlers.onTrace?.(trace);
    }
    if (data.context_quality && typeof data.context_quality === "object") {
      contextQuality = data.context_quality;
    }
    if (data.grounding && typeof data.grounding === "object") {
      grounding = data.grounding;
    }
    if (data.agent && typeof data.agent === "object") {
      agent = data.agent as ChatAgentPayload;
      handlers.onAgent?.(agent);
    }
  }
  return { source: "live", text, trace, agent, contextQuality, grounding, hybridContext, hybridDone, malformedFrames };
}

export type AgentResumeBody = {
  run_id: string;
  approval_token: string;
  approve: boolean;
  edited_plan?: Record<string, unknown>;
};

export async function resumeAgentApproval(body: AgentResumeBody): Promise<ApiResult<Record<string, unknown>>> {
  const base = await apiBase();
  try {
    const res = await fetch(`${base}/agent/resume`, {
      method: "POST",
      credentials: "include",
      headers: {
        "Content-Type": "application/json",
        Accept: "application/json",
        ...workspaceHeaders(),
      } satisfies HeadersInit,
      body: JSON.stringify(body),
    });
    const data = await res.json().catch(() => null);
    return {
      ok: res.ok,
      status: res.status,
      data: (data && typeof data === "object" ? data : {}) as Record<string, unknown>,
      source: res.ok ? "live" : "unavailable",
      error: res.ok ? undefined : friendlyError(data, res.statusText || `HTTP ${res.status}`),
    };
  } catch (err) {
    return { ok: false, status: 0, data: {}, source: "unavailable", error: friendlyCaughtError(err, "unreachable") };
  }
}
