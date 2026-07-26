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

/** In-memory cache of approval tokens for runs this extension started (or was given). */
const approvalTokenCache = new Map<string, string>();

export class LatticeAIClient {
  constructor(private baseUrl: string) { }

  /** Remember a token so List/Approve can resume runs after a pause. */
  cacheApprovalToken(runId: string, token: string): void {
    if (runId && token) approvalTokenCache.set(runId, token);
  }

  takeCachedApprovalToken(runId: string): string | undefined {
    return approvalTokenCache.get(runId);
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

  async switchModel(modelId: string): Promise<any> {
    return this._post(`/models/switch/${encodeURIComponent(modelId)}`, {});
  }

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

  // ── HTTP Helpers ──────────────────────────────────────────────────────────

  private _get(path: string): Promise<any> {
    return new Promise((resolve, reject) => {
      const url = new URL(this.baseUrl + path);
      const mod = url.protocol === "https:" ? https : http;
      mod.get(url.toString(), (res) => {
        let data = "";
        res.on("data", (c) => (data += c));
        res.on("end", () => {
          try { resolve(JSON.parse(data)); } catch { reject(new Error(data)); }
        });
      }).on("error", reject);
    });
  }

  private _post(path: string, body: any): Promise<any> {
    return new Promise((resolve, reject) => {
      const bodyStr = JSON.stringify(body);
      const url = new URL(this.baseUrl + path);
      const mod = url.protocol === "https:" ? https : http;
      const req = mod.request(
        {
          hostname: url.hostname,
          port: url.port || (url.protocol === "https:" ? 443 : 80),
          path: url.pathname + url.search,
          method: "POST",
          headers: { "Content-Type": "application/json", "Content-Length": Buffer.byteLength(bodyStr) },
        },
        (res) => {
          let data = "";
          res.on("data", (c) => (data += c));
          res.on("end", () => {
            let parsed: any;
            try { parsed = JSON.parse(data); } catch { parsed = undefined; }
            const status = res.statusCode ?? 0;
            if (status >= 400) {
              // A 409 (proposal conflict) or 4xx must not look like success.
              const detail = parsed?.detail ?? parsed?.error ?? data;
              const error: any = new Error(String(detail || `HTTP ${status}`));
              error.status = status;
              reject(error);
              return;
            }
            if (parsed === undefined) reject(new Error(data));
            else resolve(parsed);
          });
        }
      );
      req.on("error", reject);
      req.write(bodyStr);
      req.end();
    });
  }

  /**
   * SSE frames from a POST, preserving the `event:` name.
   *
   * `_streamPost` below only understands anonymous `data:` chat chunks, so a
   * named `agent_step` frame would be misread as a chunk. This parser keeps
   * the event name, which is what the live step timeline needs.
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

    const req = mod.request(
      {
        hostname: url.hostname,
        port: url.port || (url.protocol === "https:" ? 443 : 80),
        path: url.pathname + url.search,
        method: "POST",
        headers: { "Content-Type": "application/json", "Content-Length": Buffer.byteLength(body) },
      },
      (res) => {
        if ((res.statusCode ?? 0) >= 400) {
          failure = new Error(`HTTP ${res.statusCode}`);
          (failure as any).status = res.statusCode;
          done = true;
          resolve?.();
          resolve = null;
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
                // A malformed frame is dropped, never guessed at.
              }
            }
            resolve?.();
            resolve = null;
          }
        });
        res.on("end", () => { done = true; resolve?.(); resolve = null; });
      }
    );

    req.on("error", (err) => { failure = err; done = true; resolve?.(); resolve = null; });
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

    const req = mod.request(
      {
        hostname: url.hostname,
        port: url.port || (url.protocol === "https:" ? 443 : 80),
        path: url.pathname,
        method: "POST",
        headers: { "Content-Type": "application/json", "Content-Length": Buffer.byteLength(body) },
      },
      (res) => {
        let buffer = "";
        res.on("data", (raw: Buffer) => {
          buffer += raw.toString();
          const lines = buffer.split("\n");
          buffer = lines.pop() ?? "";
          for (const line of lines) {
            if (line.startsWith("data: ")) {
              const payload = line.slice(6).trim();
              if (payload === "[DONE]") { done = true; }
              else {
                try {
                  const parsed = JSON.parse(payload);
                  chunks.push(parsed.chunk);
                } catch { }
              }
              resolve?.();
              resolve = null;
            }
          }
        });
        res.on("end", () => { done = true; resolve?.(); resolve = null; });
      }
    );

    req.on("error", () => { done = true; resolve?.(); resolve = null; });
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
  }

  private _delete(path: string): Promise<any> {
    return new Promise((resolve, reject) => {
      const url = new URL(this.baseUrl + path);
      const mod = url.protocol === "https:" ? https : http;
      const req = mod.request(
        { hostname: url.hostname, port: url.port, path: url.pathname, method: "DELETE" },
        (res) => {
          let data = "";
          res.on("data", (c) => (data += c));
          res.on("end", () => {
            try { resolve(JSON.parse(data)); } catch { reject(new Error(data)); }
          });
        }
      );
      req.on("error", reject);
      req.end();
    });
  }
}
