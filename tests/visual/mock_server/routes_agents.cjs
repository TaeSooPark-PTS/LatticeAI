/**
 * Hooks, the agent runtime and registry, marketplace/plugins/skills,
 * workflows and their automation recipes, memory panels, and MCP.
 *
 * Returns true when this module answered the request; false lets the entry
 * try the next module, in the same order the original if-chain ran.
 */
const { json } = require("./http.cjs");
const { graphNodes, graphEdges, workspaceOs } = require("./fixtures.cjs");

let installedRecipeWorkflow = null;

module.exports = function handleAgents({ req, res, url, pathname }) {
  if (pathname === "/api/hooks/runs") return json(res, {
    runs: [
      { hook_id: "builtin:redact-secrets", name: "Redact secrets", kind: "pre_run", status: "ok", detail: "", output: "redacted 1 field(s)", duration_ms: 1, blocked: false, target_event: "agent.run", target_kind: "pre_run", started_at: "2026-06-07T10:00:01" },
      { hook_id: "builtin:audit-agent-run", name: "Audit agent run", kind: "post_run", status: "ok", detail: "", output: "audited run agent-run-9", duration_ms: 2, blocked: false, target_event: "agent.run", target_kind: "post_run", started_at: "2026-06-07T10:00:02" },
      { hook_id: "builtin:pipeline-index-status", name: "Pipeline index status", kind: "post_index", status: "ok", detail: "", output: "pipeline document.index: indexed=true", duration_ms: 0, blocked: false, target_event: "document.index", target_kind: "post_index", started_at: "2026-06-07T09:58:00" },
      { hook_id: "user:notify-slack", name: "Notify Slack on release", kind: "post_run", status: "ok", detail: "", output: "posted to #releases", duration_ms: 142, blocked: false, target_event: "agent.run", target_kind: "post_run", started_at: "2026-06-07T09:55:00" },
      { hook_id: "user:policy-gate", name: "Policy gate", kind: "pre_tool", status: "blocked", detail: "write to /etc denied by policy", output: "", duration_ms: 8, blocked: true, target_event: "tool.write_file", target_kind: "pre_tool", started_at: "2026-06-07T09:50:00" },
    ],
    total: 5,
    generated_at: "2026-06-07T10:00:10",
  });
  if (pathname === "/agents/api/runtime/status") return json(res, {
    runtime: { ready: true, version: "2.2.0", execution_mode: "synchronous", default_pipeline: ["planner", "executor", "reviewer"], total_runs: 3, active_runs: 0 },
    health: { status: "ok", checks: { run_store: { status: "ok" }, orchestrator: { status: "ok" } } },
    roles: [{ role: "planner", agent_id: "agent:planner" }, { role: "executor", agent_id: "agent:executor" }, { role: "reviewer", agent_id: "agent:reviewer" }],
    agents: [
      { id: "agent:planner", name: "Planner", role: "Decomposes the goal into an ordered plan.", state: "available", runs: 2, handoffs: ["agent:executor"] },
      { id: "agent:executor", name: "Executor", role: "Executes each planned step.", state: "available", runs: 1, handoffs: ["agent:reviewer"] },
      { id: "agent:reviewer", name: "Reviewer", role: "Reviews and approves the work.", state: "available", runs: 1, handoffs: [] },
    ],
    runs: [
      { id: "agent-run-1", agent_id: "agent:executor", status: "ok", input: "Summarize release", output: "Completed 3/3 steps", created_at: "2026-06-06T12:30:00" },
      { id: "agent-run-2", agent_id: "agent:executor", status: "retried_ok", input: "Build index", output: "Completed after 1 retry", created_at: "2026-06-06T11:05:00" },
    ],
  });
  if (pathname === "/agents/api/runtime/health") return json(res, { status: "ok", checks: { run_store: { status: "ok" }, orchestrator: { status: "ok" } } });
  if (pathname === "/agents/api/runtime/config") return json(res, { version: "2.2.0", roles: ["researcher", "planner", "executor", "reviewer", "release"], default_pipeline: ["planner", "executor", "reviewer"], max_retries_cap: 5, execution_mode: "synchronous" });
  if (pathname === "/agents/api/registry") return json(res, {
    agents: [
      { id: "agent:researcher", name: "Researcher", type: "researcher", version: "3.3.1", description: "Gathers workspace context.", capabilities: ["context-retrieval", "hybrid-search"], source: "builtin", enabled: true, removable: false, config: {} },
      { id: "agent:planner", name: "Planner", type: "planner", version: "3.3.1", description: "Builds bounded plans.", capabilities: ["task-decomposition", "delegation"], source: "builtin", enabled: true, removable: false, config: {} },
      { id: "agent:executor", name: "Executor", type: "executor", version: "3.3.1", description: "Executes tools and workflows.", capabilities: ["tool-use", "workflow-run"], source: "builtin", enabled: true, removable: false, config: {} },
      { id: "agent:reviewer", name: "Reviewer", type: "reviewer", version: "3.3.1", description: "Reviews execution.", capabilities: ["verification", "approval"], source: "builtin", enabled: true, removable: false, config: {} },
      { id: "agent:release", name: "Release", type: "release", version: "3.3.1", description: "Finalizes approved outcomes.", capabilities: ["summarize"], source: "builtin", enabled: true, removable: false, config: {} },
    ],
    types: ["planner", "researcher", "executor", "reviewer", "release", "custom"],
    counts: { planner: 1, researcher: 1, executor: 1, reviewer: 1, release: 1 },
    total: 5,
    version: "3.3.1",
    default_pipeline: ["planner", "executor", "reviewer"],
  });
  if (pathname === "/agents/api/registry/capabilities") return json(res, {
    capabilities: {
      "tool-use": ["agent:executor"],
      "workflow-run": ["agent:executor"],
      "verification": ["agent:reviewer"],
      "task-decomposition": ["agent:planner"],
      "hybrid-search": ["agent:researcher"],
    },
  });

  if (pathname === "/marketplace/templates") return json(res, {
    marketplace_version: "3.3.1",
    kinds: ["plugin", "workflow", "agent"],
    templates: [
      { id: "agent-research-assistant", kind: "agent", name: "Research Assistant", version: "1.0.0", description: "Retrieves workspace context and synthesizes a reviewed answer.", metadata: { category: "research" }, definition: { roles: ["researcher", "planner", "reviewer"], capabilities: ["hybrid-search", "memory-recall"] } },
      { id: "agent-coding-assistant", kind: "agent", name: "Coding Assistant", version: "1.0.0", description: "Plans a code change, executes it, and reviews the result.", metadata: { category: "coding" }, definition: { roles: ["planner", "executor", "reviewer"], capabilities: ["tool-use", "verification"] } },
      { id: "workflow-agent-plugin-review", kind: "workflow", name: "Agent Plugin Review Workflow", version: "1.0.0", description: "Trigger into agent chain, plugin, and output.", metadata: { category: "agent-ops" }, definition: { roles: ["planner", "executor", "reviewer"] } },
    ],
    total: 3,
  });
  if (pathname === "/marketplace/templates/registry") return json(res, { registry: {} });
  if (pathname === "/plugins/registry") return json(res, { plugins: [{ id: "hello-world", name: "Hello World", version: "1.0.0", description: "Demo plugin", installed: true, enabled: true }] });
  if (pathname === "/plugins/directory") return json(res, { plugins: [{ id: "git-insights", name: "Git Insights", description: "Repository summary plugin", version: "1.0.0", author: "Lattice" }], categories: ["dev"] });
  if (pathname === "/skills/marketplace") return json(res, { skills: [{ skill: "visual_regression", name: "visual_regression", description: "Capture and compare workspace UI", version: "1.2.0", author: "Lattice", category: "test", installed: false }], categories: ["test"] });
  if (pathname === "/workflows/api/definitions") return json(res, { workflows: [
    { id: "wf-agent-review", name: "Agent Review Workflow", nodes: [
      { id: "trigger", type: "trigger", name: "Trigger", config: { trigger: "manual" }, next: "agent" },
      { id: "agent", type: "agent", name: "Agent chain", next: "tool" },
      { id: "tool", type: "tool", name: "Tool", next: "output" },
      { id: "output", type: "output", name: "Result", next: null },
    ] },
    ...(installedRecipeWorkflow ? [installedRecipeWorkflow] : []),
  ] });
  if (pathname === "/workflows/api/automation/recipes") return json(res, {
    recipes: [{
      id: "follow-up-radar",
      name: "Follow-up Radar",
      summary: "Looks for follow-up candidates when new knowledge enters the Brain.",
      user_value: "Gentle reminders for loose ends without a noisy task system.",
      cadence: "when new memory is saved",
      creates: ["follow-up suggestions", "approval-ready task drafts"],
      consent: { requires_user_enable: true },
    }],
    principles: { local_first: true, drafts_before_automation: true },
  });
  if (pathname === "/workflows/api/automation/recipes/follow-up-radar" && req.method === "POST") {
    const enabled = Boolean(installedRecipeWorkflow);
    installedRecipeWorkflow = {
      id: "wf-follow-up-radar",
      name: "Follow-up Radar",
      nodes: [{ id: "trigger", type: "trigger", config: { trigger: "brain_event", enabled }, next: "draft" }],
      metadata: { created_from: "brain_automation_recipe", recipe_id: "follow-up-radar", automation_state: enabled ? "enabled" : "draft_disabled" },
    };
    return json(res, { workflow: installedRecipeWorkflow, recipe: installedRecipeWorkflow.metadata, enabled, already_installed: enabled });
  }
  if (pathname.startsWith("/workflows/api/definitions/") && req.method === "PATCH") return json(res, { workflow: { id: "wf-agent-review", name: "Agent Review Workflow" } });
  if (pathname === "/workflows/api/triggers") return json(res, { running: true, tick_seconds: 5, armed: [{ workflow_id: "wf-agent-review", name: "Agent Review Workflow", kind: "brain_event", config: { source_type: "upload" }, last_fired_at: 1780300800, recent_events: [{ type: "fired", trigger: "brain_event" }] }] });
  if (pathname === "/workflows/api/runs") return json(res, { runs: [
    { id: "wf-run-approval", workflow_id: "wf-agent-review", workflow_name: "Agent Review Workflow", status: "awaiting_approval", mode: "live", pause: { node: "tool" }, timeline: [{ event: "workflow_started", status: "running" }, { event: "approval_required", status: "awaiting_approval" }], created_at: "2026-06-06T12:05:00" },
    { id: "wf-run-1", workflow_id: "wf-agent-review", workflow_name: "Agent Review Workflow", status: "ok", mode: "live", created_at: "2026-06-06T12:00:00" },
  ] });
  if (pathname.startsWith("/workflows/api/runs/") && pathname.endsWith("/stop") && req.method === "POST") return json(res, { stopped: true, run_id: pathname.split("/")[4] });
  if (pathname.startsWith("/workflows/api/runs/") && pathname.endsWith("/resume") && req.method === "POST") return json(res, { run: { id: "wf-run-resumed", status: "ok" }, result: { status: "ok" }, resumed_from: pathname.split("/")[4] });
  if (pathname === "/api/memory/manager") return json(res, {
    sources: [
      { id: "workspace", type: "workspace", label: "Workspace Memory", count: 3, size_bytes: 2048, health: "ok", detail: "Personal workspace memory." },
      { id: "project", type: "project", label: "Project Memory", count: 1, size_bytes: 0, health: "ok", detail: "Organization memory." },
      { id: "agent", type: "agent", label: "Agent Memory", count: 2, size_bytes: 0, health: "ok", detail: "Agent memory snapshots." },
      { id: "conversation", type: "conversation", label: "Conversation Memory", count: 2, size_bytes: 1024, health: "ok", detail: "Chat history." },
      { id: "graph", type: "graph", label: "Graph Memory", count: graphNodes.length, size_bytes: 4096, health: "ok", detail: "Knowledge graph entities.", edges: graphEdges.length },
      { id: "vector", type: "vector", label: "Vector Memory", count: 8, size_bytes: 0, health: "ok", detail: "Vector index." },
    ],
    tiers: ["workspace", "project", "agent", "conversation", "graph", "vector"],
    usage: { total_items: 21, total_bytes: 7168, sources: 6 },
    brain_readiness: {
      score: 100,
      state: "alive",
      depth: 5,
      title_key: "brain.readiness.alive",
      action_key: "brain.readiness.map",
      source: "memory_service",
      signals: { memory_count: 8, concept_count: graphNodes.length, relationship_count: graphEdges.length, healthy_sources: 6 },
    },
    health: "ok",
  });
  if (pathname === "/api/memory/brain-quality") return json(res, {
    score: 100,
    state: "alive",
    depth: 5,
    title_key: "brain.readiness.alive",
    action_key: "brain.readiness.map",
    source: "memory_service",
    signals: { memory_count: 8, concept_count: graphNodes.length, relationship_count: graphEdges.length, healthy_sources: 6 },
  });
  if (pathname === "/api/memory/brain-proof") return json(res, {
    status: "alive",
    model_continuity: {
      active_model: workspaceOs.models.current_model,
      brain_owner: "lattice_brain",
      capability: true,
      survives_model_switch: true,
      proven: true,
      context_store: "workspace + conversation + graph + vector",
    },
    proofs: {
      durable_items: 13,
      has_durable_evidence: true,
      workspace_memories: 3,
      conversations: 2,
      graph_concepts: graphNodes.length,
      vector_items: 8,
      healthy_sources: 6,
    },
    recall: {
      query: url.searchParams.get("q") || "first Brain proof",
      count: 2,
      items: [
        { id: "mem:release", source: "workspace", title: "Release memory", snippet: "The Brain recalls saved release decisions with source evidence.", score: 0.94 },
        { id: "file:readme", source: "graph", title: "README.md", snippet: "Release documentation anchors the answer.", score: 0.88 },
      ],
    },
    claims: {
      can_recall_user_context: true,
      keeps_context_across_models: true,
      is_knowledge_store: true,
    },
  });
  if (pathname === "/api/memory/brain-brief") return json(res, {
    status: "alive",
    score: 100,
    headline_key: "brain.brief.headline.alive",
    body_key: "brain.brief.body.alive",
    focus: {
      kind: "graph",
      title: "Lattice AI",
      detail: "Local-first workspace graph grounded in saved release decisions.",
      source: "Knowledge Graph",
      score: 0.96,
      empty: false,
    },
    next_actions: [
      { id: "inspect_topics", label_key: "brain.brief.action.topics", detail_key: "brain.brief.action.topics.detail", route: "/knowledge-graph", priority: 9 },
    ],
    suggested_questions: [
      { id: "focus_next", label_key: "brain.suggestion.focus.label", detail_key: "brain.suggestion.focus.detail", prompt_key: "brain.suggestion.focus.prompt", params: { focus: "Lattice AI" }, priority: 10 },
      { id: "evidence_check", label_key: "brain.suggestion.evidence.label", detail_key: "brain.suggestion.evidence.detail", prompt_key: "brain.suggestion.evidence.prompt", params: { focus: "Lattice AI" }, priority: 9 },
    ],
    proactive_actions: [
      { id: "proactive_evidence_review", intent: "ask", label_key: "brain.proactive.evidence.label", detail_key: "brain.proactive.evidence.detail", prompt: "Review the evidence Brain has for Lattice AI.", route: "", priority: 100, context: { focus: "Lattice AI" } },
      { id: "proactive_delegate", intent: "delegate", label_key: "brain.proactive.delegate.label", detail_key: "brain.proactive.delegate.detail", prompt: "Turn Lattice AI into an evidence-backed execution plan.", route: "", priority: 95, context: { focus: "Lattice AI" } },
      { id: "proactive_review_draft", intent: "review", label_key: "brain.proactive.review.label", detail_key: "brain.proactive.review.detail", prompt: "Create a reviewable task from Lattice AI.", route: "", priority: 90, context: { focus: "Lattice AI" } },
    ],
    evidence: [
      { id: "durable", label_key: "brain.brief.evidence.durable", value: 13, detail_key: "brain.brief.evidence.durable.detail" },
      { id: "graph", label_key: "brain.brief.evidence.graph", value: graphNodes.length, detail_key: "brain.brief.evidence.graph.detail" },
      { id: "sources", label_key: "brain.brief.evidence.sources", value: 6, detail_key: "brain.brief.evidence.sources.detail" },
    ],
    generated_at: "2026-06-07T10:05:00Z",
  });
  if (pathname === "/api/memory/inspect") return json(res, { source: url.searchParams.get("source"), items: [{ id: "mem-demo", kind: "workspace", title: "Demo memory", content: "Release memory" }], count: 1, available: true, stats: workspaceOs.graph, index: { status: "ready" } });
  if (pathname === "/api/hooks/run" && req.method === "POST") return json(res, {
    hook_id: "builtin:redact-secrets", name: "Redact secrets", kind: "pre_run",
    status: "ok", detail: "", output: "redacted 1 field(s)", duration_ms: 1, blocked: false,
    source: "builtin", binding: "multi_agent._redact", started_at: "2026-06-07T10:05:00",
  });
  if (pathname === "/api/hooks/fire" && req.method === "POST") return json(res, { kind: "pre_run", event: "manual", ran: 1, blocked: false, block_reason: "", results: [], generated_at: "2026-06-07T10:05:00" });
  if (pathname === "/api/hooks") return json(res, { hooks: [
    { id: "builtin:redact-secrets", name: "Redact secrets", kind: "pre_run", order: 10, description: "Strip secret-like fields from agent context before a run.", binding: "multi_agent._redact", managed: "platform", source: "builtin", enabled: true, removable: false, executable: true, advisory: false },
    { id: "builtin:research-memory-snapshot", name: "Research memory snapshot", kind: "agent", order: 20, description: "Capture a short-term memory snapshot after the researcher stage.", binding: "multi_agent.default_role_runner", managed: "platform", source: "builtin", enabled: true, removable: false, executable: true, advisory: false },
    { id: "builtin:tool-permission-gate", name: "Tool permission gate", kind: "pre_tool", order: 10, description: "Evaluate + record the governance policy for each tool call.", binding: "tool_registry.permission", managed: "platform", source: "builtin", enabled: true, removable: false, executable: true, advisory: false },
    { id: "builtin:sensitive-data-guard", name: "Sensitive-data guard", kind: "pre_tool", order: 20, description: "Classify outgoing content for sensitive data before tool execution.", binding: "server_app.classify_sensitive_message", managed: "platform", source: "builtin", enabled: true, removable: false, executable: true, advisory: false },
    { id: "builtin:audit-agent-run", name: "Audit agent run", kind: "post_run", order: 10, description: "Append every completed agent run to the workspace audit log.", binding: "AgentRuntime.start", managed: "platform", source: "builtin", enabled: true, removable: false, executable: true, advisory: false },
    { id: "builtin:pipeline-index-status", name: "Pipeline index status", kind: "post_index", order: 10, description: "Publish ingest / embed / graph-build pipeline state.", binding: "api.search", managed: "platform", source: "builtin", enabled: true, removable: false, executable: true, advisory: false },
    { id: "builtin:workflow-replay-log", name: "Workflow replay log", kind: "post_workflow", order: 10, description: "Record each workflow run's timeline so it can be replayed.", binding: "api.workflow_designer", managed: "platform", source: "builtin", enabled: true, removable: false, executable: true, advisory: false },
    { id: "user:notify-slack", name: "Notify Slack on release", kind: "post_run", order: 100, description: "Posts a message to #releases after an agent run.", command: "python3 scripts/notify.py", managed: "user", source: "user", enabled: true, removable: true, executable: true, advisory: false },
  ], kinds: ["pre_run", "post_run", "pre_tool", "post_tool", "pre_workflow", "post_workflow", "pre_upload", "post_upload", "pre_index", "post_index", "agent"], counts: { pre_run: { total: 1, enabled: 1 }, post_run: { total: 2, enabled: 2 }, pre_tool: { total: 2, enabled: 2 }, post_index: { total: 1, enabled: 1 }, post_workflow: { total: 1, enabled: 1 }, agent: { total: 1, enabled: 1 } }, total: 8, enabled: 8 });
  if (pathname === "/tools/permissions") return json(res, { status: "ok", permissions: [
    { tool: "read_file", risk: "low", requires_approval: false, network: false },
    { tool: "write_file", risk: "medium", requires_approval: true, network: false },
    { tool: "run_command", risk: "high", requires_approval: true, network: false },
  ] });
  if (pathname === "/mcp/tools") return json(res, { status: "ok", installed_mcps: [{ id: "mcp-files", name: "Files", description: "File MCP", category: "local", installed: true }], tools: [
    { name: "read_file", description: "Read workspace files.", permission: { tool: "read_file", risk: "low", requires_approval: false, network: false }, governance: { risk: "read", destructive: false, shell: false, network: false, auto_approve: true, sandbox: "workspace", rollback: "none" } },
    { name: "write_file", description: "Write workspace files.", permission: { tool: "write_file", risk: "medium", requires_approval: true, network: false }, governance: { risk: "write", destructive: false, shell: false, network: false, auto_approve: false, sandbox: "workspace", rollback: "git" } },
    { name: "run_command", description: "Run allowlisted commands.", permission: { tool: "run_command", risk: "high", requires_approval: true, network: false }, governance: { risk: "exec", destructive: false, shell: true, network: false, auto_approve: false, sandbox: "workspace", rollback: "none" } },
  ] });
  if (pathname === "/mcp/installed") return json(res, { installed: [{ id: "mcp-files", name: "Files", installed: true }] });
  if (pathname === "/mcp/claude-code-servers") return json(res, { servers: [{ id: "claude-code:filesystem", name: "filesystem", description: "Claude Code MCP", package: "npx filesystem", category: "Claude Code", source: "claude-code", installed: true, env_vars: [] }] });
  if (pathname === "/mcp/custom") return json(res, { custom: [{ id: "custom:docs", name: "Docs", description: "Docs MCP", package: "npx docs", category: "custom", source: "custom", installed: false, env_vars: [] }] });
  return false;
};
