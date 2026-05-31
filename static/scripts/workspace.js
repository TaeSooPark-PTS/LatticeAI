const API_BASE = window.location.protocol === "file:" ? "http://localhost:4825" : "";

const state = {
  os: null,
  snapshots: [],
};

function $(id) {
  return document.getElementById(id);
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

async function api(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (options.body && !headers["Content-Type"]) headers["Content-Type"] = "application/json";
  const response = await fetch(API_BASE + path, { credentials: "include", ...options, headers });
  const text = await response.text();
  let data = {};
  try { data = text ? JSON.parse(text) : {}; } catch { data = { detail: text }; }
  if (!response.ok) {
    throw new Error(data.detail || `${response.status} ${response.statusText}`);
  }
  return data;
}

function toast(message) {
  const node = $("toast");
  node.textContent = message;
  node.classList.add("show");
  clearTimeout(node._timer);
  node._timer = setTimeout(() => node.classList.remove("show"), 2200);
}

function renderMetrics(os) {
  const counts = os?.counts || {};
  const graph = os?.graph || {};
  const nodeTotal = Object.values(graph.nodes || {}).reduce((sum, value) => sum + Number(value || 0), 0);
  const edgeTotal = Object.values(graph.edges || {}).reduce((sum, value) => sum + Number(value || 0), 0);
  const items = [
    ["Graph Nodes", nodeTotal, "ti-chart-dots-3"],
    ["Graph Edges", edgeTotal, "ti-git-branch"],
    ["Snapshots", counts.snapshots || 0, "ti-stack-2"],
    ["Memories", counts.memories || 0, "ti-book-2"],
  ];
  $("metric-grid").innerHTML = items.map(([label, value, icon]) => `
    <div class="metric-card">
      <i class="ti ${icon}"></i>
      <span>${escapeHtml(label)}</span>
      <strong>${escapeHtml(value)}</strong>
    </div>
  `).join("");
}

function renderOnboarding(payload) {
  const steps = payload.steps || [];
  $("onboarding-steps").innerHTML = steps.map((step) => {
    const status = step.status || "pending";
    const label = step.id.replaceAll("_", " ");
    return `
      <button class="step-chip" data-step="${escapeHtml(step.id)}" title="Mark ${escapeHtml(label)} complete">
        <span>${escapeHtml(label)}</span>
        <span class="status-pill status-${escapeHtml(status)}">${escapeHtml(status)}</span>
      </button>
    `;
  }).join("");
}

function renderTraces(payload) {
  const traces = payload.traces || [];
  $("trace-list").innerHTML = traces.length ? traces.map((trace) => `
    <div class="list-item">
      <div class="list-title">
        <span>${escapeHtml(trace.question || "Trace")}</span>
        <span class="status-pill">${Math.round((trace.confidence || 0) * 100)}%</span>
      </div>
      <div class="meta-line">${escapeHtml(trace.created_at || "")} · ${escapeHtml(trace.conversation_id || "workspace")}</div>
      <div class="tag-row">
        ${(trace.graph_nodes || []).slice(0, 5).map((node) => `<a class="tag" href="/graph?node=${encodeURIComponent(node.id)}">${escapeHtml(node.title || node.id)}</a>`).join("")}
      </div>
      <div class="mini-row">${escapeHtml((trace.source_files || []).map((source) => source.source).slice(0, 3).join(" · ") || "No source files")}</div>
    </div>
  `).join("") : `<div class="list-item"><div class="meta-line">No answer traces yet.</div></div>`;
}

function renderIndexing(payload) {
  const sources = payload.sources || [];
  $("indexing-list").innerHTML = sources.length ? sources.map((source) => `
    <div class="list-item">
      <div class="list-title">
        <span>${escapeHtml(source.label || source.root_path)}</span>
        <span class="status-pill ${source.watch_active ? "status-complete" : ""}">${source.watch_active ? "watching" : source.status || "idle"}</span>
      </div>
      <div class="meta-line">${escapeHtml(source.root_path || "")}</div>
      <div class="tag-row">
        <span class="tag">${Number(source.success_count || 0)} indexed</span>
        <span class="tag">${Number(source.failure_count || 0)} failed</span>
        <span class="tag">${escapeHtml(source.last_run_at || "not scanned")}</span>
      </div>
      <div class="item-actions">
        <button class="small-action" data-index-action="resume" data-source="${escapeHtml(source.id)}"><i class="ti ti-player-play"></i>Resume</button>
        <button class="small-action" data-index-action="pause" data-source="${escapeHtml(source.id)}"><i class="ti ti-player-pause"></i>Pause</button>
        <button class="small-action danger-action" data-index-action="remove" data-source="${escapeHtml(source.id)}"><i class="ti ti-trash"></i>Remove</button>
      </div>
    </div>
  `).join("") : `<div class="list-item"><div class="meta-line">No indexed folders.</div></div>`;
}

function renderSnapshots(payload) {
  const snapshots = payload.snapshots || [];
  state.snapshots = snapshots;
  $("snapshot-list").innerHTML = snapshots.length ? snapshots.map((snapshot) => `
    <div class="list-item">
      <div class="list-title">
        <span>${escapeHtml(snapshot.name)}</span>
        <span class="status-pill">${escapeHtml(snapshot.node_count || 0)} nodes</span>
      </div>
      <div class="meta-line">${escapeHtml(snapshot.created_at)} · ${escapeHtml(snapshot.id)}</div>
      <div class="item-actions">
        <button class="small-action" data-export-snapshot="${escapeHtml(snapshot.id)}"><i class="ti ti-package-export"></i>Export</button>
      </div>
    </div>
  `).join("") : `<div class="list-item"><div class="meta-line">No snapshots.</div></div>`;

  const options = snapshots.map((snapshot) => `<option value="${escapeHtml(snapshot.id)}">${escapeHtml(snapshot.name)}</option>`).join("");
  $("snapshot-before").innerHTML = options;
  $("snapshot-after").innerHTML = options;
  if (snapshots[1]) $("snapshot-before").value = snapshots[1].id;
  if (snapshots[0]) $("snapshot-after").value = snapshots[0].id;
}

function renderMemories(payload) {
  const memories = payload.memories || [];
  $("memory-list").innerHTML = memories.length ? memories.map((memory) => `
    <div class="list-item">
      <div class="list-title">
        <span>${escapeHtml(memory.kind || "memory")}</span>
        <span class="status-pill">${escapeHtml(memory.updated_at || "")}</span>
      </div>
      <div>${escapeHtml(memory.content || "")}</div>
      <div class="tag-row">${(memory.tags || []).map((tag) => `<span class="tag">${escapeHtml(tag)}</span>`).join("")}</div>
    </div>
  `).join("") : `<div class="list-item"><div class="meta-line">No personal memory yet.</div></div>`;
}

function renderComputerMemory(payload) {
  const config = payload?.computer_memory || payload || {};
  $("computer-memory-toggle").checked = Boolean(config.enabled);
  $("computer-memory-state").textContent = JSON.stringify({
    enabled: Boolean(config.enabled),
    approved: Boolean(config.approved),
    scopes: config.scopes || [],
    activities: (config.activities || []).length,
    notice: config.notice,
  }, null, 2);
}

function renderAgents(payload) {
  const agents = payload.agents || [];
  $("agent-list").innerHTML = agents.map((agent) => `
    <div class="list-item">
      <div class="list-title">
        <span>${escapeHtml(agent.name)}</span>
        <span class="status-pill status-complete">${escapeHtml(agent.status || "available")}</span>
      </div>
      <div class="meta-line">${escapeHtml(agent.role || "")}</div>
      <div class="tag-row">${(agent.relationships || []).map((rel) => `<span class="tag">${escapeHtml(rel)}</span>`).join("")}</div>
    </div>
  `).join("");
}

function renderWorkflows(payload) {
  const workflows = payload.workflows || [];
  $("workflow-list").innerHTML = workflows.length ? workflows.map((workflow) => `
    <div class="list-item">
      <div class="list-title">
        <span>${escapeHtml(workflow.name)}</span>
        <span class="status-pill">${(workflow.steps || []).length} steps</span>
      </div>
      <div class="meta-line">${escapeHtml(workflow.created_at || "")}</div>
      <div class="tag-row">${(workflow.steps || []).slice(0, 4).map((step) => `<span class="tag">${escapeHtml(step.action || step.name || "step")}</span>`).join("")}</div>
    </div>
  `).join("") : `<div class="list-item"><div class="meta-line">No workflows.</div></div>`;
}

function renderSkills(payload) {
  const installed = payload.installed || [];
  const available = (payload.available || []).filter((skill) => !installed.some((item) => item.name === (skill.skill || skill.name))).slice(0, 8);
  const rows = [
    ...installed.map((skill) => ({ ...skill, marketplace: false })),
    ...available.map((skill) => ({ name: skill.skill || skill.name, description: skill.description, version: skill.version || "remote", enabled: skill.enabled, marketplace: true })),
  ];
  $("skill-list").innerHTML = rows.length ? rows.map((skill) => `
    <div class="list-item">
      <div class="list-title">
        <span>${escapeHtml(skill.name)}</span>
        <span class="status-pill ${skill.enabled === false ? "status-failed" : "status-complete"}">${skill.enabled === false ? "disabled" : "enabled"}</span>
      </div>
      <div class="meta-line">${escapeHtml(skill.description || "")}</div>
      <div class="tag-row">
        <span class="tag">${escapeHtml(skill.version || "local")}</span>
        <span class="tag">${skill.marketplace ? "marketplace" : "installed"}</span>
      </div>
      <div class="item-actions">
        <button class="small-action" data-skill-action="enable" data-skill="${escapeHtml(skill.name)}"><i class="ti ti-toggle-right"></i>Enable</button>
        <button class="small-action" data-skill-action="disable" data-skill="${escapeHtml(skill.name)}"><i class="ti ti-toggle-left"></i>Disable</button>
      </div>
    </div>
  `).join("") : `<div class="list-item"><div class="meta-line">No skills found.</div></div>`;
}

function renderTimeline(payload) {
  const events = payload.events || [];
  $("timeline-list").innerHTML = events.length ? events.slice(0, 40).map((event) => `
    <div class="timeline-item">
      <div class="list-title"><span>${escapeHtml(event.event_type || "event")}</span><span class="status-pill">${escapeHtml(event.area || "workspace")}</span></div>
      <div class="meta-line">${escapeHtml(event.timestamp || "")}</div>
    </div>
  `).join("") : `<div class="timeline-item"><div class="meta-line">No timeline events.</div></div>`;
}

async function refreshAll() {
  const [os, onboarding, traces, indexing, snapshots, memories, computerMemory, agents, workflows, skills, timeline] = await Promise.all([
    api("/workspace/os"),
    api("/workspace/onboarding/status"),
    api("/workspace/traces"),
    api("/workspace/indexing"),
    api("/workspace/snapshots"),
    api("/workspace/memories"),
    api("/workspace/computer-memory"),
    api("/workspace/agents"),
    api("/workspace/workflows"),
    api("/workspace/skills"),
    api("/workspace/time-machine"),
  ]);
  state.os = os;
  renderMetrics(os);
  renderOnboarding(onboarding);
  renderTraces(traces);
  renderIndexing(indexing);
  renderSnapshots(snapshots);
  renderMemories(memories);
  renderComputerMemory(computerMemory);
  renderAgents(agents);
  renderWorkflows(workflows);
  renderSkills(skills);
  renderTimeline(timeline);
}

async function createSnapshot() {
  const name = $("snapshot-name").value || "Workspace snapshot";
  const payload = await api("/workspace/snapshots", {
    method: "POST",
    body: JSON.stringify({ name }),
  });
  toast(`Snapshot saved: ${payload.snapshot.id}`);
  await refreshAll();
}

async function compareSnapshots() {
  const beforeId = $("snapshot-before").value;
  const afterId = $("snapshot-after").value;
  if (!beforeId || !afterId) return;
  const diff = await api("/workspace/snapshots/compare", {
    method: "POST",
    body: JSON.stringify({ before_id: beforeId, after_id: afterId }),
  });
  $("snapshot-diff").textContent = JSON.stringify(diff.summary, null, 2);
}

async function saveMemory() {
  const content = $("memory-content").value.trim();
  if (!content) return;
  await api("/workspace/memories", {
    method: "POST",
    body: JSON.stringify({
      kind: $("memory-kind").value,
      content,
      tags: [],
    }),
  });
  $("memory-content").value = "";
  toast("Memory saved");
  await refreshAll();
}

async function createDemoWorkflow() {
  await api("/workspace/workflows", {
    method: "POST",
    body: JSON.stringify({
      name: "Upload -> Summarize -> Generate -> Export",
      steps: [
        { action: "upload" },
        { action: "summarize" },
        { action: "generate" },
        { action: "export" },
      ],
    }),
  });
  toast("Workflow created");
  await refreshAll();
}

async function configureComputerMemory(enabled) {
  const consent = enabled
    ? { approved: true, reason: "Enabled from Workspace OS UI", approved_at: new Date().toISOString() }
    : { approved: false };
  await api("/workspace/computer-memory", {
    method: "POST",
    body: JSON.stringify({ enabled, consent }),
  });
  toast(enabled ? "Computer Memory enabled" : "Computer Memory disabled");
  await refreshAll();
}

document.addEventListener("click", async (event) => {
  const step = event.target.closest("[data-step]");
  if (step) {
    await api("/workspace/onboarding/step", {
      method: "POST",
      body: JSON.stringify({ step: step.dataset.step, status: "complete" }),
    });
    toast("Onboarding step saved");
    await refreshAll();
    return;
  }

  const indexBtn = event.target.closest("[data-index-action]");
  if (indexBtn) {
    const action = indexBtn.dataset.indexAction;
    const source = indexBtn.dataset.source;
    await api(`/workspace/indexing/${encodeURIComponent(source)}/${action}`, { method: "POST" });
    toast(`Index ${action} complete`);
    await refreshAll();
    return;
  }

  const exportBtn = event.target.closest("[data-export-snapshot]");
  if (exportBtn) {
    const result = await api(`/workspace/snapshots/${encodeURIComponent(exportBtn.dataset.exportSnapshot)}/export`, { method: "POST" });
    toast(`Exported ${result.bytes} bytes`);
    return;
  }

  const skillBtn = event.target.closest("[data-skill-action]");
  if (skillBtn) {
    await api(`/workspace/skills/${skillBtn.dataset.skillAction}`, {
      method: "POST",
      body: JSON.stringify({ skill: skillBtn.dataset.skill }),
    });
    toast(`Skill ${skillBtn.dataset.skillAction}`);
    await refreshAll();
  }
});

document.addEventListener("DOMContentLoaded", () => {
  $("refresh-btn").addEventListener("click", () => refreshAll().catch((err) => toast(err.message)));
  $("snapshot-now").addEventListener("click", () => createSnapshot().catch((err) => toast(err.message)));
  $("create-snapshot").addEventListener("click", () => createSnapshot().catch((err) => toast(err.message)));
  $("complete-onboarding").addEventListener("click", async () => {
    await api("/workspace/onboarding/complete", { method: "POST", body: JSON.stringify({ data: { ui: "workspace" } }) });
    toast("Onboarding complete");
    await refreshAll();
  });
  $("reload-traces").addEventListener("click", () => refreshAll().catch((err) => toast(err.message)));
  $("compare-snapshots").addEventListener("click", () => compareSnapshots().catch((err) => toast(err.message)));
  $("save-memory").addEventListener("click", () => saveMemory().catch((err) => toast(err.message)));
  $("computer-memory-toggle").addEventListener("change", (event) => configureComputerMemory(event.target.checked).catch((err) => {
    event.target.checked = false;
    toast(err.message);
  }));
  $("create-demo-workflow").addEventListener("click", () => createDemoWorkflow().catch((err) => toast(err.message)));
  $("reload-skills").addEventListener("click", () => refreshAll().catch((err) => toast(err.message)));
  refreshAll().catch((err) => toast(err.message));
});
