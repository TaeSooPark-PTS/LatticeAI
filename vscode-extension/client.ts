import * as http from "http";
import * as https from "https";

export interface ModelInfo {
  id: string;
  name?: string;
  model_name?: string;
  tag: string;
  size: string;
  source_country?: string;
  source_company?: string;
  execution_method?: string;
  internet_requirement?: string;
}

/** In-memory bounded, TTL-expiring cache of approval tokens (single-use on retrieval). */
const MAX_APPROVAL_TOKENS = 256;
const APPROVAL_TOKEN_TTL_MS = 30 * 60 * 1000;
const REQUEST_TIMEOUT_MS = 30_000;
const STREAM_TIMEOUT_MS = 120_000;

interface CachedToken {
  token: string;
  expiresAt: number;
}

const approvalTokenCache = new Map<string, CachedToken>();

function pruneApprovalTokenCache(now = Date.now()): void {
  for (const [key, entry] of approvalTokenCache.entries()) {
    if (entry.expiresAt <= now) {
      approvalTokenCache.delete(key);
    }
  }
  while (approvalTokenCache.size >= MAX_APPROVAL_TOKENS) {
    const oldestKey = approvalTokenCache.keys().next().value;
    if (oldestKey === undefined) break;
    approvalTokenCache.delete(oldestKey);
  }
}

export class LatticeAIClient {
  constructor(private baseUrl: string, private authToken?: string) { }

  setBaseUrl(baseUrl: string): void {
    this.baseUrl = baseUrl;
  }

  setAuthToken(authToken?: string): void {
    this.authToken = authToken && authToken.trim() ? authToken.trim() : undefined;
  }

  private authHeaders(extra: Record<string, string | number> = {}): Record<string, string | number> {
    if (this.authToken) {
      return { ...extra, Authorization: `Bearer ${this.authToken}` };
    }
    return extra;
  }

  /** Remember a token so List/Approve can resume runs after a pause. */
  cacheApprovalToken(runId: string, token: string): void {
    if (!runId || !token) return;
    const now = Date.now();
    pruneApprovalTokenCache(now);
    approvalTokenCache.set(runId, { token, expiresAt: now + APPROVAL_TOKEN_TTL_MS });
  }

  /** Retrieve and immediately consume (single-use) a cached approval token. */
  takeCachedApprovalToken(runId: string): string | undefined {
    const entry = approvalTokenCache.get(runId);
    if (!entry) return undefined;
    approvalTokenCache.delete(runId);
    if (entry.expiresAt <= Date.now()) return undefined;
    return entry.token;
  }

  clearCachedApprovalToken(runId: string): void {
    approvalTokenCache.delete(runId);
  }

  // ── Health ────────────────────────────────────────────────────────────────

  async health(): Promise<any> {
    return this._get("/health");
  }

  async isAlive(): Promise<boolean> {
    try {
      await this.health();
      return true;
    } catch {
      return false;
    }
  }

  // ── Models ────────────────────────────────────────────────────────────────

  async listModels(): Promise<{ recommended: ModelInfo[]; loaded: string[]; current: string }> {
    return this._get("/models");
  }

  async getCurrentModel(): Promise<string> {
    const models = await this.listModels();
    return models.current;
  }

  async loadModel(modelId: string, adapterPath?: string): Promise<any> {
    return this._post("/models/load", { model_id: modelId, adapter_path: adapterPath });
  }

  // `switchModel` lived here and posted to `/models/switch/{model_id}`. Nothing
  // in this extension ever called it, and v11.8.0 deleted the route for exactly
  // that reason across every surface — so the method would now be a 404 waiting
  // for its first caller. `loadModel` switches as part of loading, and that is
  // what the `ltcai.loadModel` command has always gone through
  // (`commands.ts` → `syncStatus.loadModelWithProgress` → `client.loadModel`).

  async unloadModel(modelId: string): Promise<any> {
    return this._delete(`/models/unload/${encodeURIComponent(modelId)}`);
  }

  // ── Generation ────────────────────────────────────────────────────────────

  async generate(
    message: string,
    context?: string,
    maxTokens = 2048,
    temperature = 0.2
  ): Promise<string> {
    const res = await this._post("/chat", {
      message,
      context,
      max_tokens: maxTokens,
      temperature,
      stream: false,
    });
    return res.response as string;
  }

  /**
   * Non-streamed /chat, returning the full payload — including the
   * `grounding` verdict and `context_quality` meta the web app badges.
   * `generate` above throws that away; recall parity needs it.
   */
  async chat(
    message: string,
    context?: string,
    maxTokens = 2048,
    temperature = 0.2
  ): Promise<any> {
    return this._post("/chat", {
      message,
      context,
      max_tokens: maxTokens,
      temperature,
      stream: false,
      source: "vscode",
    });
  }

  async *streamGenerate(
    message: string,
    context?: string,
    maxTokens = 2048,
    temperature = 0.2
  ): AsyncGenerator<string> {
    const body = JSON.stringify({
      message,
      context,
      max_tokens: maxTokens,
      temperature,
      stream: true,
    });

    yield* this._streamPost("/chat", body);
  }

  // ── Garden ────────────────────────────────────────────────────────────────

  async garden(rawData: string, category?: string): Promise<any> {
    return this._post("/garden", { raw_data: rawData, category });
  }

  async gardenTree(): Promise<any> {
    return this._get("/garden/tree");
  }

  // ── Workspace OS ─────────────────────────────────────────────────────────

  async sendToWorkspace(payload: {
    action: string;
    file_path?: string;
    language?: string;
    content?: string;
    selection?: string;
    prompt?: string;
    extension_version?: string;
    workspace_folder?: string;
  }): Promise<any> {
    return this._post("/workspace/vscode/send", payload);
  }

  async workspaceStatus(): Promise<any> {
    return this._get("/workspace/vscode/status");
  }

  async reportWorkspaceStatus(payload: {
    status: string;
    index_status?: string;
    workspace_folder?: string;
    extension_version?: string;
    active_file?: string;
    detail?: string;
  }): Promise<any> {
    return this._post("/workspace/vscode/status", payload);
  }

  // ── Agent approval (SURFACE_PARITY, v9.9.5) ───────────────────────────────

  async runAgent(message: string, opts: {
    human_in_loop?: boolean;
    planning_model?: string;
    executing_model?: string;
    reviewing_model?: string;
  } = {}): Promise<any> {
    const result = await this._post("/agent", {
      message,
      source: "vscode",
      human_in_loop: opts.human_in_loop ?? false,
      planning_model: opts.planning_model,
      executing_model: opts.executing_model,
      reviewing_model: opts.reviewing_model,
    });
    // Cache tokens for both modern awaiting_approval and legacy waiting_approval.
    const status = String(result?.status || "");
    if (status === "awaiting_approval" || status === "waiting_approval") {
      const runId = String(result?.run_id || result?.context_id || "");
      const token = String(result?.approval?.token || "");
      this.cacheApprovalToken(runId, token);
    }
    return result;
  }

  async listApprovals(): Promise<any> {
    return this._get("/agent/approvals");
  }

  async resumeAgent(payload: {
    run_id?: string;
    approval_token?: string;
    context_id?: string;
    approved?: boolean;
    approve?: boolean;
    edited_plan?: any;
  }): Promise<any> {
    const runId = payload.run_id || payload.context_id || "";
    const token = payload.approval_token || (runId ? this.takeCachedApprovalToken(runId) : undefined);
    const body: any = {
      run_id: payload.run_id,
      approval_token: token,
      context_id: payload.context_id,
      approved: payload.approved ?? payload.approve ?? true,
      edited_plan: payload.edited_plan,
    };
    // API accepts both approve and approved; send both for wire compatibility.
    body.approve = body.approved;
    const result = await this._post("/agent/resume", body);
    if (runId) this.clearCachedApprovalToken(runId);
    return result;
  }

  /**
   * Live agent run (SURFACE_PARITY v9.9.7). POSTs `/agent` with `stream:true`
   * and calls `onStep` for every named `agent_step` frame, so the editor
   * watches the loop work instead of staring at a spinner. Resolves with the
   * same terminal payload the JSON response returns.
   */
  async runAgentLive(
    message: string,
    onStep: (step: any) => void,
    opts: { human_in_loop?: boolean; project_id?: string } = {}
  ): Promise<any> {
    const body = JSON.stringify({
      message,
      source: "vscode",
      stream: true,
      human_in_loop: opts.human_in_loop ?? false,
      project_id: opts.project_id,
    });
    let final: any = null;
    for await (const frame of this._streamEvents("/agent", body)) {
      if (frame.event === "agent_step") {
        onStep(frame.data);
        continue;
      }
      if (frame.data && typeof frame.data === "object" && frame.data.agent) {
        final = frame.data.agent;
      }
    }
    if (final) {
      const status = String(final?.status || "");
      if (status === "awaiting_approval" || status === "waiting_approval") {
        this.cacheApprovalToken(
          String(final?.run_id || final?.context_id || ""),
          String(final?.approval?.token || "")
        );
      }
    }
    return final;
  }

  // ── Evidence → action (SURFACE_PARITY v9.9.7) ─────────────────────────────

  async evidenceActions(question: string, sourceIds: string[], language = "ko"): Promise<any> {
    return this._post("/api/evidence/actions", {
      question,
      source_ids: sourceIds,
      language,
    });
  }

  // ── Review Center (change proposals, SURFACE_PARITY v9.9.6) ───────────────
  // The same /api/proposals surface the web Review Center uses: mutations to
  // existing files are staged, never applied, until a human approves them.

  async listProposals(): Promise<any> {
    return this._get("/api/proposals");
  }

  async approveProposal(itemId: string): Promise<any> {
    return this._post(`/api/proposals/${encodeURIComponent(itemId)}/approve`, {});
  }

  async rejectProposal(itemId: string, reason = ""): Promise<any> {
    return this._post(`/api/proposals/${encodeURIComponent(itemId)}/reject`, { reason });
  }

  // ── Capture: folder ingestion (v10.4.0) ───────────────────────────────────

  /**
   * Ingest a local folder through the same unified pipeline the web Capture
   * view uses. The endpoint requires the local-read approval dance: without an
   * approval token it returns a `permission_required` payload, which the
   * caller surfaces rather than silently retrying.
   */
  async ingestFolder(payload: {
    path: string;
    recursive?: boolean;
    approved?: boolean;
    approval_token?: string;
    background?: boolean;
  }): Promise<any> {
    return this._post("/api/ingestion/folder", {
      path: payload.path,
      recursive: payload.recursive ?? true,
      approved: payload.approved ?? false,
      approval_token: payload.approval_token,
      background: payload.background ?? true,
    });
  }

  async ingestionJob(jobId: string): Promise<any> {
    return this._get(`/api/ingestion/jobs/${encodeURIComponent(jobId)}`);
  }

  // ── Setup: hardware-derived model recommendation (v10.4.0) ────────────────

  async setupScan(): Promise<any> {
    return this._get("/setup/scan");
  }

  // ── HTTP Helpers ──────────────────────────────────────────────────────────

  private _get(path: string): Promise<any> {
    return new Promise((resolve, reject) => {
      const url = new URL(this.baseUrl + path);
      const mod = url.protocol === "https:" ? https : http;
      let settled = false;
      const finish = (err: Error | null, value?: any) => {
        if (settled) return;
        settled = true;
        if (err) reject(err);
        else resolve(value);
      };
      const req = mod.request(
        {
          hostname: url.hostname,
          port: url.port || (url.protocol === "https:" ? 443 : 80),
          path: url.pathname + url.search,
          method: "GET",
          timeout: REQUEST_TIMEOUT_MS,
          headers: this.authHeaders(),
        },
        (res) => {
          let data = "";
          res.on("data", (c) => (data += c));
          res.on("error", (err) => finish(err));
          res.on("aborted", () => finish(new Error("Response stream aborted")));
          res.on("close", () => {
            if (!res.complete) finish(new Error("Response closed prematurely"));
          });
          res.on("end", () => {
            let parsed: any;
            try { parsed = JSON.parse(data); } catch { parsed = undefined; }
            const status = res.statusCode ?? 0;
            if (status >= 400) {
              const detail = parsed?.detail ?? parsed?.error ?? data;
              const error: any = new Error(String(detail || `HTTP ${status}`));
              error.status = status;
              finish(error);
              return;
            }
            if (parsed === undefined) finish(new Error(data || "Invalid JSON response"));
            else finish(null, parsed);
          });
        }
      );
      req.on("timeout", () => req.destroy(new Error(`Request timed out after ${REQUEST_TIMEOUT_MS}ms`)));
      req.on("error", (err) => finish(err));
      req.end();
    });
  }

  private _post(path: string, body: any): Promise<any> {
    return new Promise((resolve, reject) => {
      const bodyStr = JSON.stringify(body);
      const url = new URL(this.baseUrl + path);
      const mod = url.protocol === "https:" ? https : http;
      let settled = false;
      const finish = (err: Error | null, value?: any) => {
        if (settled) return;
        settled = true;
        if (err) reject(err);
        else resolve(value);
      };
      const req = mod.request(
        {
          hostname: url.hostname,
          port: url.port || (url.protocol === "https:" ? 443 : 80),
          path: url.pathname + url.search,
          method: "POST",
          timeout: REQUEST_TIMEOUT_MS,
          headers: this.authHeaders({
            "Content-Type": "application/json",
            "Content-Length": Buffer.byteLength(bodyStr),
          }),
        },
        (res) => {
          let data = "";
          res.on("data", (c) => (data += c));
          res.on("error", (err) => finish(err));
          res.on("aborted", () => finish(new Error("Response stream aborted")));
          res.on("close", () => {
            if (!res.complete) finish(new Error("Response closed prematurely"));
          });
          res.on("end", () => {
            let parsed: any;
            try { parsed = JSON.parse(data); } catch { parsed = undefined; }
            const status = res.statusCode ?? 0;
            if (status >= 400) {
              // A 409 (proposal conflict) or 4xx must not look like success.
              const detail = parsed?.detail ?? parsed?.error ?? data;
              const error: any = new Error(String(detail || `HTTP ${status}`));
              error.status = status;
              finish(error);
              return;
            }
            if (parsed === undefined) finish(new Error(data || "Invalid JSON response"));
            else finish(null, parsed);
          });
        }
      );
      req.on("timeout", () => req.destroy(new Error(`Request timed out after ${REQUEST_TIMEOUT_MS}ms`)));
      req.on("error", (err) => finish(err));
      req.write(bodyStr);
      req.end();
    });
  }

  /**
   * SSE frames from a POST, preserving the `event:` name.
   */
  private async *_streamEvents(
    path: string,
    body: string
  ): AsyncGenerator<{ event: string; data: any }> {
    const url = new URL(this.baseUrl + path);
    const mod = url.protocol === "https:" ? https : http;

    const frames: Array<{ event: string; data: any }> = [];
    let resolve: (() => void) | null = null;
    let done = false;
    let failure: Error | null = null;

    const wake = () => {
      resolve?.();
      resolve = null;
    };

    const req = mod.request(
      {
        hostname: url.hostname,
        port: url.port || (url.protocol === "https:" ? 443 : 80),
        path: url.pathname + url.search,
        method: "POST",
        timeout: STREAM_TIMEOUT_MS,
        headers: this.authHeaders({
          "Content-Type": "application/json",
          "Content-Length": Buffer.byteLength(body),
        }),
      },
      (res) => {
        if ((res.statusCode ?? 500) >= 400) {
          failure = new Error(`HTTP ${res.statusCode}`);
          (failure as any).status = res.statusCode;
          done = true;
          wake();
          res.resume();
          return;
        }
        let buffer = "";
        let pendingEvent = "message";
        res.on("data", (raw: Buffer) => {
          buffer += raw.toString();
          const lines = buffer.split("\n");
          buffer = lines.pop() ?? "";
          for (const line of lines) {
            if (line.startsWith("event: ")) {
              pendingEvent = line.slice(7).trim();
              continue;
            }
            if (!line.startsWith("data: ")) continue;
            const payload = line.slice(6).trim();
            const event = pendingEvent;
            pendingEvent = "message";
            if (payload === "[DONE]") {
              done = true;
            } else {
              try {
                frames.push({ event, data: JSON.parse(payload) });
              } catch {
                // Malformed frame is dropped rather than crashing the generator.
              }
            }
            wake();
          }
        });
        res.on("error", (err) => { failure = err; done = true; wake(); });
        res.on("aborted", () => {
          if (!done) failure = failure ?? new Error("Stream aborted");
          done = true;
          wake();
        });
        res.on("close", () => {
          if (!done && !res.complete) {
            failure = failure ?? new Error("Stream closed prematurely");
          }
          done = true;
          wake();
        });
        res.on("end", () => { done = true; wake(); });
      }
    );

    req.on("timeout", () => req.destroy(new Error(`Stream timed out after ${STREAM_TIMEOUT_MS}ms`)));
    req.on("error", (err) => { failure = err; done = true; wake(); });
    req.write(body);
    req.end();

    while (!done || frames.length > 0) {
      if (frames.length === 0) {
        await new Promise<void>((r) => { resolve = r; });
      }
      while (frames.length > 0) {
        yield frames.shift()!;
      }
    }
    if (failure) throw failure;
  }

  private async *_streamPost(path: string, body: string): AsyncGenerator<string> {
    const url = new URL(this.baseUrl + path);
    const mod = url.protocol === "https:" ? https : http;

    const chunks: string[] = [];
    let resolve: (() => void) | null = null;
    let done = false;
    let failure: Error | null = null;

    const wake = () => {
      resolve?.();
      resolve = null;
    };

    const req = mod.request(
      {
        hostname: url.hostname,
        port: url.port || (url.protocol === "https:" ? 443 : 80),
        path: url.pathname + url.search,
        method: "POST",
        timeout: STREAM_TIMEOUT_MS,
        headers: this.authHeaders({
          "Content-Type": "application/json",
          "Content-Length": Buffer.byteLength(body),
        }),
      },
      (res) => {
        if ((res.statusCode ?? 500) >= 400) {
          failure = new Error(`HTTP ${res.statusCode}`);
          (failure as any).status = res.statusCode;
          done = true;
          wake();
          res.resume();
          return;
        }
        let buffer = "";
        res.on("data", (raw: Buffer) => {
          buffer += raw.toString();
          const lines = buffer.split("\n");
          buffer = lines.pop() ?? "";
          for (const line of lines) {
            if (line.startsWith("data: ")) {
              const payload = line.slice(6).trim();
              if (payload === "[DONE]") {
                done = true;
              } else {
                try {
                  const parsed = JSON.parse(payload);
                  if (typeof parsed?.chunk === "string") {
                    chunks.push(parsed.chunk);
                  }
                } catch (parseErr) {
                  console.warn("Lattice AI stream skipped malformed JSON frame:", parseErr);
                }
              }
              wake();
            }
          }
        });
        res.on("error", (err) => { failure = err; done = true; wake(); });
        res.on("aborted", () => {
          if (!done) failure = failure ?? new Error("Stream aborted");
          done = true;
          wake();
        });
        res.on("close", () => {
          if (!done && !res.complete) {
            failure = failure ?? new Error("Stream closed prematurely");
          }
          done = true;
          wake();
        });
        res.on("end", () => { done = true; wake(); });
      }
    );

    req.on("timeout", () => req.destroy(new Error(`Stream timed out after ${STREAM_TIMEOUT_MS}ms`)));
    req.on("error", (err) => { failure = err; done = true; wake(); });
    req.write(body);
    req.end();

    while (!done || chunks.length > 0) {
      if (chunks.length === 0) {
        await new Promise<void>((r) => { resolve = r; });
      }
      while (chunks.length > 0) {
        yield chunks.shift()!;
      }
    }
    if (failure) throw failure;
  }

  private _delete(path: string): Promise<any> {
    return new Promise((resolve, reject) => {
      const url = new URL(this.baseUrl + path);
      const mod = url.protocol === "https:" ? https : http;
      let settled = false;
      const finish = (err: Error | null, value?: any) => {
        if (settled) return;
        settled = true;
        if (err) reject(err);
        else resolve(value);
      };
      const req = mod.request(
        {
          hostname: url.hostname,
          port: url.port || (url.protocol === "https:" ? 443 : 80),
          path: url.pathname + url.search,
          method: "DELETE",
          timeout: REQUEST_TIMEOUT_MS,
          headers: this.authHeaders(),
        },
        (res) => {
          let data = "";
          res.on("data", (c) => (data += c));
          res.on("error", (err) => finish(err));
          res.on("aborted", () => finish(new Error("Response stream aborted")));
          res.on("close", () => {
            if (!res.complete) finish(new Error("Response closed prematurely"));
          });
          res.on("end", () => {
            let parsed: any;
            try { parsed = JSON.parse(data); } catch { parsed = undefined; }
            const status = res.statusCode ?? 0;
            if (status >= 400) {
              const detail = parsed?.detail ?? parsed?.error ?? data;
              const error: any = new Error(String(detail || `HTTP ${status}`));
              error.status = status;
              finish(error);
              return;
            }
            if (parsed === undefined) finish(new Error(data || "Invalid JSON response"));
            else finish(null, parsed);
          });
        }
      );
      req.on("timeout", () => req.destroy(new Error(`Request timed out after ${REQUEST_TIMEOUT_MS}ms`)));
      req.on("error", (err) => finish(err));
      req.end();
    });
  }
}
