/**
 * Index status, embeddings, the model library and its install stream, host
 * sysinfo, documents and connected folders.
 *
 * Returns true when this module answered the request; false lets the entry
 * try the next module, in the same order the original if-chain ran.
 */
const { json } = require("./http.cjs");
const { graphNodes, graphEdges } = require("./fixtures.cjs");

module.exports = function handleModels({ req, res, url, pathname }) {
  // ── v3 future API surfaces (integration targets) ──────────────────────────
  if (pathname === "/api/index/status") return json(res, {
    generated_at: "2026-06-06T12:00:00",
    pipelines: {
      knowledge_graph: { state: "ready", entities: graphNodes.length, relations: graphEdges.length, coverage: 0.9 },
      vector_index: { state: "ready", vectors: 48230, dimensions: 1024, model: "bge-local", coverage: 0.87 },
      hybrid: { state: "ready", strategy: "reciprocal-rank-fusion", alpha: 0.5 },
    },
    sources: [
      { id: "src-notes", label: "Workspace Notes", files: 312, state: "indexed", progress: 1 },
      { id: "src-repo", label: "Connected Repo", files: 1840, state: "indexing", progress: 0.62 },
    ],
  });
  if (pathname === "/api/graph") return json(res, { nodes: graphNodes, edges: graphEdges });
  if (pathname === "/api/search/hybrid") return json(res, {
    query: (url.searchParams.get("query") || "retrieval"),
    results: graphNodes.slice(0, 4).map((n, i) => ({
      id: n.id, title: n.title, path: (n.metadata && n.metadata.relative_path) || `graph://${n.id}`,
      snippet: n.summary, vector: 0.9 - i * 0.1, lexical: 0.6 - i * 0.08, graph: 0.8 - i * 0.05,
      score: 0.85 - i * 0.09,
    })),
  });

  if (pathname === "/api/embeddings/status") return json(res, {
    provider: "ollama", requested_provider: "ollama", active_provider: "ollama",
    model: "nomic-embed-text", model_id: "ollama:nomic-embed-text:768", dimensions: 768,
    grade: "production", state: "production", fell_back: false,
    health: { status: "ok", detail: "Ollama reachable" },
    last_indexed_at: "2026-06-06T12:30:00", index: { status: "ready", indexed_items: 48230 },
  });
  if (pathname === "/api/embeddings/providers") return json(res, { active: "ollama", requested: "ollama", providers: [
    { id: "hash", label: "Local hash (fallback)", grade: "fallback" },
    { id: "mlx", label: "MLX (Apple Silicon)", grade: "production" },
    { id: "ollama", label: "Ollama", grade: "production" },
    { id: "openai", label: "OpenAI-compatible", grade: "production" },
    { id: "custom", label: "Custom", grade: "production" },
  ] });
  // ── v3.4.0 Platform Completion surfaces ───────────────────────────────────
  if (pathname === "/agents/api/run" && req.method === "POST") return json(res, {
    run: { id: "agent-run-live", agent_id: "agent:executor", status: "ok", created_at: "2026-06-07T10:06:00" },
    result: {
      agent_id: "agent:executor", status: "ok", retries: 0,
      output: "Completed the goal across planner -> executor -> reviewer. 3/3 steps approved.",
      roles_run: ["planner", "executor", "reviewer"],
      timeline: [
        { event: "start", role: "planner", status: "ok", timestamp: "2026-06-07T10:06:00" },
        { event: "role", role: "planner", status: "ok", result: "Decomposed the goal into 3 ordered steps.", timestamp: "2026-06-07T10:06:00" },
        { event: "handoff", role: "executor", status: "ok", timestamp: "2026-06-07T10:06:01" },
        { event: "role", role: "executor", status: "ok", result: "Executed 3/3 steps, invoking tools.", timestamp: "2026-06-07T10:06:01" },
        { event: "handoff", role: "reviewer", status: "ok", timestamp: "2026-06-07T10:06:02" },
        { event: "role", role: "reviewer", status: "ok", result: "Reviewed and approved the work.", timestamp: "2026-06-07T10:06:02" },
        { event: "end", status: "ok", retries: 0, timestamp: "2026-06-07T10:06:02" },
      ],
      plan: { steps: [{ step: "Plan" }, { step: "Execute" }, { step: "Review" }] },
      review: { verdict: "pass" }, handoffs: [],
    },
    pre_run_hooks: { ran: 1, blocked: false },
    post_run_hooks: { ran: 1, blocked: false },
  });
  // The one-click switcher on the model library calls this. Without it the
  // primary action of the screen's first card 404s in every captured frame.
  if (pathname === "/models/load" && req.method === "POST") return json(res, {
    status: "ok", loaded: true, model_id: "mlx-community/gemma-4-26b-a4b-it-4bit", engine: "local_mlx",
  });
  if (pathname === "/models") return json(res, {
    recommended: [
      {
        id: "mlx-community/Qwen3-VL-8B-Instruct-4bit",
        name: "Qwen3-VL 8B",
        display_name: "Qwen3-VL 8B",
        family: "Qwen3-VL",
        size: "4.8GB",
        modality: "multimodal",
        capabilities: ["vision", "text"],
        state: "loaded",
        pulled: true,
        download_required: false,
        load_available: true,
        load_status: "loaded",
        recommended_engine: "local_mlx",
        recommended_load_id: "mlx-community/Qwen3-VL-8B-Instruct-4bit",
        runtime_compatibility: { supported: true, status: "supported" },
      },
      {
        id: "mlx-community/gemma-4-12b-it-4bit",
        name: "Gemma 4 12B Instruct",
        display_name: "Gemma 4 12B Instruct",
        family: "Gemma 4",
        size: "7.6GB",
        capabilities: ["text"],
        state: "runtime_update_needed",
        pulled: true,
        download_required: false,
        load_available: false,
        load_status: "runtime_update_needed",
        unavailable_reason: "Gemma 4 12B uses the gemma4_unified MLX format. The installed MLX-VLM runtime does not include that loader, so this local model cannot load until MLX-VLM is updated.",
        recommended_engine: "local_mlx",
        recommended_load_id: "mlx-community/gemma-4-12b-it-4bit",
        runtime_label: "MLX-VLM",
        engine_options: [
          { engine: "local_mlx", model_id: "mlx-community/gemma-4-12b-it-4bit", load_id: "mlx-community/gemma-4-12b-it-4bit", runtime_label: "MLX-VLM", runtime_supported: false },
          { engine: "ollama", model_id: "hf.co/ggml-org/gemma-4-12B-it-GGUF:Q4_K_M", load_id: "ollama:hf.co/ggml-org/gemma-4-12B-it-GGUF:Q4_K_M", runtime_label: "Ollama GGUF" },
          { engine: "lmstudio", model_id: "ggml-org/gemma-4-12B-it-GGUF", load_id: "lmstudio:ggml-org/gemma-4-12B-it-GGUF", runtime_label: "LM Studio GGUF" },
        ],
        runtime_compatibility: {
          supported: false,
          status: "runtime_update_needed",
          action: "Runtime update needed",
          reason_code: "mlx_vlm_missing_gemma4_unified_model",
          model_type: "gemma4_unified",
          missing_components: ["mlx_vlm.models.gemma4_unified"],
          preferred_runtime: "MLX-VLM",
          user_message: "Gemma 4 12B uses the gemma4_unified MLX format. The installed MLX-VLM runtime does not include that loader, so this local model cannot load until MLX-VLM is updated.",
          recovery_guidance: ["Update MLX-VLM to 0.6.3 or newer.", "Use Gemma 4 26B A4B locally until then."],
          alternatives: [
            { id: "mlx-community/gemma-4-26b-a4b-it-4bit", name: "Gemma 4 26B A4B", engine: "local_mlx" },
            { id: "ollama:hf.co/ggml-org/gemma-4-12B-it-GGUF:Q4_K_M", name: "Ollama GGUF", engine: "ollama" },
          ],
        },
      },
      {
        id: "mlx-community/gemma-4-26b-a4b-it-4bit",
        name: "Gemma 4 26B A4B Instruct",
        display_name: "Gemma 4 26B A4B Instruct",
        family: "Gemma 4",
        size: "15.6GB",
        capabilities: ["vision", "text"],
        state: "ready",
        pulled: true,
        download_required: false,
        load_available: true,
        load_status: "ready",
        recommended_engine: "local_mlx",
        recommended_load_id: "mlx-community/gemma-4-26b-a4b-it-4bit",
        runtime_label: "MLX-VLM",
        runtime_compatibility: { supported: true, status: "supported", model_type: "gemma4", preferred_runtime: "MLX-VLM" },
      },
    ],
    cloud: [],
    engines: [{ id: "local_mlx", name: "MLX", kind: "local", installed: true }],
    loaded: ["mlx-community/Qwen3-VL-8B-Instruct-4bit"],
    current: "mlx-community/Qwen3-VL-8B-Instruct-4bit",
    compat_profiles: [{ model_id: "mlx-community/Qwen3-VL-8B-Instruct-4bit", engine: "local_mlx", quality_status: "ok", chat_compatible: true }],
    vision: { current_model: "mlx-community/Qwen3-VL-8B-Instruct-4bit", current_supports_vision: true, engine_available: true, enabled: true },
  });
  if (pathname === "/models/recommendations") return json(res, {
    profile: { os: "darwin", arch: "arm64", ram_mb: 65536, gpu: { vendor: "apple", vram_mb: 65536 } },
    recommendations: {
      engine: "local_mlx",
      engine_available: true,
      apple_silicon: true,
      ram_gb: 64,
      counts: { recommended: 2, compatible: 0, not_recommended: 1 },
      top_pick: { id: "mlx-community/gemma-4-26b-a4b-it-4bit", name: "Gemma 4 26B A4B Instruct", family: "Gemma 4", status: "recommended", size: "15.6GB" },
      families: [],
      models: [
        { id: "mlx-community/Qwen3-VL-8B-Instruct-4bit", name: "Qwen3-VL 8B", family: "Qwen3-VL", status: "recommended", reason: "현재 메모리에서 안정적으로 사용할 가능성이 높습니다", size: "4.8GB", runtime_compatibility: { supported: true, status: "supported" } },
        { id: "mlx-community/gemma-4-12b-it-4bit", name: "Gemma 4 12B Instruct", family: "Gemma 4", status: "not_recommended", reason: "Runtime update needed", size: "7.6GB", runtime_compatibility: { supported: false, status: "runtime_update_needed", action: "Runtime update needed" } },
        { id: "mlx-community/gemma-4-26b-a4b-it-4bit", name: "Gemma 4 26B A4B Instruct", family: "Gemma 4", status: "recommended", reason: "현재 메모리에서 안정적으로 사용할 가능성이 높습니다", size: "15.6GB", runtime_compatibility: { supported: true, status: "supported", model_type: "gemma4" } },
      ],
    },
  });
  // Install SSE stage tokens MUST match latticeai/services/model_loading.py
  // prepare stream wire protocol (B2): engine → download → load → smoke_test → done.
  // Frontend maps these to UI steps install/download/validate/load via
  // friendlyInstallStage — never invent mock-only stage names.
  if (pathname === "/engines/prepare-model/stream" && req.method === "POST") {
    res.writeHead(200, { "content-type": "text/event-stream; charset=utf-8", "cache-control": "no-store", connection: "keep-alive" });
    const send = (event, obj) => res.write(`event: ${event}\ndata: ${JSON.stringify(obj)}\n\n`);
    send("progress", { stage: "engine", message: "Execution engine is ready.", percent: 10 });
    setTimeout(() => send("progress", { stage: "download", message: "Already downloaded model files.", percent: 55 }), 100);
    setTimeout(() => send("progress", { stage: "load", message: "Loading model into memory.", percent: 92 }), 200);
    setTimeout(() => send("progress", { stage: "smoke_test", message: "Validating chat compatibility.", percent: 98 }), 300);
    setTimeout(() => {
      send("done", { status: "ok", model: "mlx-community/Qwen3-VL-8B-Instruct-4bit", current: "mlx-community/Qwen3-VL-8B-Instruct-4bit", ready_to_chat: true, compatibility_status: "ok" });
      res.end();
    }, 420);
    return true;
  }
  // readiness is backend-owned (roomy|tight|low). Mock max load is 61% → tight.
  if (pathname === "/local/sysinfo") return json(res, {
    cpu_pct: 34,
    ram_pct: 61,
    gpu_mem_pct: 48,
    gpu_mem_gb: 9.4,
    readiness: "tight",
  });
  if (pathname === "/knowledge-graph/documents") return json(res, {
    documents: [
      { id: "file:a1b2c3", filename: "retrieval-design.pdf", ext: ".pdf", mime_type: "application/pdf", bytes: 184320, sha256: "a1b2c3d4e5f6", uploader: "you@local", chars: 18240, chunks: 24, indexed: true, ingest_state: "indexed", created_at: "2026-06-07T10:00:00", updated_at: "2026-06-07T10:00:05" },
      { id: "file:d4e5f6", filename: "meeting-notes.md", ext: ".md", mime_type: "text/markdown", bytes: 4096, sha256: "d4e5f6a1b2c3", uploader: "you@local", chars: 3200, chunks: 4, indexed: true, ingest_state: "indexed", created_at: "2026-06-07T09:30:00", updated_at: "2026-06-07T09:30:02" },
      { id: "file:g7h8i9", filename: "q3-budget.xlsx", ext: ".xlsx", mime_type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", bytes: 20480, sha256: "g7h8i9j0k1l2", uploader: "you@local", chars: 980, chunks: 2, indexed: true, ingest_state: "indexed", created_at: "2026-06-06T16:10:00", updated_at: "2026-06-06T16:10:01" },
      { id: "file:m3n4o5", filename: "onboarding.docx", ext: ".docx", mime_type: "application/vnd.openxmlformats-officedocument.wordprocessingml.document", bytes: 51200, sha256: "m3n4o5p6q7r8", uploader: "you@local", chars: 0, chunks: 0, indexed: false, ingest_state: "ingested", created_at: "2026-06-07T10:01:00", updated_at: "2026-06-07T10:01:00" },
    ],
    total: 4,
    generated_at: "2026-06-07T10:00:10",
  });
  {
    const localSources = {
      sources: [
        { id: "src-docs", label: "Documents", root_path: "/Users/you/Documents", success_count: 312, failure_count: 0, status: "indexed", last_run_at: "2026-06-07T09:00:00", watch_enabled: true, watch_active: true, watch_status: { last_event_at: 1717740000, last_indexed_at: 1717740300, last_error: null } },
        { id: "src-proj", label: "lattice (project)", root_path: "/Users/you/code/lattice", success_count: 1840, failure_count: 2, status: "indexed", last_run_at: "2026-06-07T08:30:00", watch_enabled: false, watch_active: false, watch_status: null },
      ],
      watch: { available: true, error: "", debounce_seconds: 5, active: { "src-docs": { root_path: "/Users/you/Documents", last_event_at: 1717740000, last_indexed_at: 1717740300, last_error: null } } },
    };
    if (pathname === "/knowledge-graph/local/sources") return json(res, localSources);
    if (pathname === "/knowledge-graph/local/roots") return json(res, { roots: [{ path: "/Users/you/Documents", label: "Documents" }, { path: "/Users/you/Desktop", label: "Desktop" }, { path: "/Users/you/code", label: "code" }] });
    if (pathname === "/knowledge-graph/local/watch/status") return json(res, localSources.watch);
    // Per-folder memory state. The Sources screen now shows this card beside the
    // recent-documents panel, so it has to answer here or the second row of the
    // redesigned layout captures as a single half-empty column.
    if (pathname === "/knowledge-graph/local/health") return json(res, {
      count: 2,
      vector_freshness_global: { status: "fresh", pending_items: 0 },
      folders: [
        {
          id: "src-docs", label: "Documents", root_path: "/Users/you/Documents",
          status: "indexed", watch_active: true, coverage: 1,
          files: { total: 312, indexed: 312, failed: 0 }, recent_errors: [],
        },
        {
          id: "src-proj", label: "lattice (project)", root_path: "/Users/you/code/lattice",
          status: "indexed", watch_active: false, coverage: 0.9989,
          files: { total: 1842, indexed: 1840, failed: 2 },
          recent_errors: [
            { path: "/Users/you/code/lattice/assets/logo.psd", detail: "지원하지 않는 파일 형식이라 건너뛰었어요." },
          ],
        },
      ],
    });
    if (pathname === "/api/local-agent/status") return json(res, {
      agent: { id: "lattice-local-runtime", name: "Lattice Local Agent", kind: "on-device-runtime", online: true, platform: "macOS-15.5-arm64-arm-64bit", machine: "arm64", python: "3.12.4" },
      online: true, mode: "online", version: "3.4.1", pid: 31166,
      handshake: { ok: true, transport: "in-process", latency_ms: 0.7, detail: "Probed the in-process runtime (filesystem + graph); the local Lattice server is the on-device agent — no separate desktop process." },
      health: { status: "online", filesystem_access: true, graph_reachable: true, watcher_available: true },
      filesystem_access: true, watcher_available: true, connected_folders: 2, watched_folders: 1,
      folders: { connected: 2, watching: 1 },
      watch: localSources.watch,
      sources: localSources.sources,
      last_seen: "2026-06-08T21:21:34", error: null,
    });
  }
  return false;
};
