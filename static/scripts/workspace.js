const API_BASE = window.location.protocol === "file:" ? "http://localhost:4825" : "";

const state = {
  os: null,
  snapshots: [],
  activeWorkspace: null,
  registry: null,
  managingWorkspace: null,
  skillsPayload: null,
  skillTab: "recommended",
  entities: [],
  activeEntity: null,
};

// Skills that match common workspace needs are surfaced under "Recommended".
const RECOMMENDED_SKILL_HINTS = ["code", "review", "doc", "test", "security", "research", "changelog", "refactor", "debug"];

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
  if (state.activeWorkspace && !headers["X-Workspace-Id"]) headers["X-Workspace-Id"] = state.activeWorkspace;
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

function skillName(skill) {
  return skill.skill || skill.name || "skill";
}

// Compute the four marketplace tabs from the registry payload (machine-global
// registry + locally-installed state). "Updates" = installed skills whose
// registry version differs from the installed version.
function computeSkillTabs(payload) {
  const installed = payload.installed || [];
  const available = payload.available || [];
  const installedNames = new Set(installed.map(skillName));
  const notInstalled = available.filter((s) => !installedNames.has(skillName(s)));
  const availByName = new Map(available.map((s) => [skillName(s), s]));
  const updates = installed.filter((s) => {
    const remote = availByName.get(skillName(s));
    return remote && remote.version && s.version && remote.version !== s.version;
  });
  const recommended = notInstalled.filter((s) => {
    const hay = `${skillName(s)} ${s.category || ""} ${s.description || ""}`.toLowerCase();
    return RECOMMENDED_SKILL_HINTS.some((h) => hay.includes(h));
  });
  return { installed, popular: notInstalled, recommended, updates };
}

function renderSkillRow(skill, { installed }) {
  const name = skillName(skill);
  const enabled = skill.enabled !== false;
  const version = skill.version || (installed ? "local" : "registry");
  const source = skill.plugin || skill.source || (installed ? "installed" : "marketplace");
  const actions = installed
    ? `<button class="small-action" data-skill-action="${enabled ? "disable" : "enable"}" data-skill="${escapeHtml(name)}"><i class="ti ti-${enabled ? "toggle-left" : "toggle-right"}"></i>${enabled ? "Disable" : "Enable"}</button>`
    : `<button class="small-action" data-skill-action="install" data-skill="${escapeHtml(name)}"><i class="ti ti-download"></i>Install</button>`;
  return `
    <div class="list-item">
      <div class="list-title">
        <span>${escapeHtml(name)}</span>
        <span class="status-pill ${installed ? (enabled ? "status-complete" : "status-failed") : ""}">${installed ? (enabled ? "enabled" : "disabled") : "available"}</span>
      </div>
      <div class="meta-line">${escapeHtml(skill.description || "No description")}</div>
      <div class="tag-row">
        <span class="tag">v${escapeHtml(version)}</span>
        ${skill.category ? `<span class="tag">${escapeHtml(skill.category)}</span>` : ""}
        <span class="tag">${escapeHtml(source)}</span>
      </div>
      <div class="item-actions">${actions}</div>
    </div>`;
}

function renderSkills(payload) {
  if (payload) state.skillsPayload = payload;
  const data = state.skillsPayload || { installed: [], available: [] };
  const tabs = computeSkillTabs(data);
  const updatesCount = $("skill-updates-count");
  if (updatesCount) updatesCount.textContent = tabs.updates.length ? String(tabs.updates.length) : "";
  document.querySelectorAll("[data-skill-tab]").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.skillTab === state.skillTab);
  });
  const tab = state.skillTab;
  const rows = (tab === "installed" || tab === "updates")
    ? (tabs[tab] || []).map((s) => renderSkillRow(s, { installed: true }))
    : (tabs[tab] || []).slice(0, 24).map((s) => renderSkillRow(s, { installed: false }));
  const empty = {
    recommended: "No recommended skills right now.",
    popular: "Marketplace is empty.",
    installed: "No skills installed yet.",
    updates: "All installed skills are up to date.",
  }[tab];
  $("skill-list").innerHTML = rows.length ? rows.join("") : `<div class="list-item"><div class="meta-line">${escapeHtml(empty)}</div></div>`;
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

function renderWorkspaceRegistry(registry, edition) {
  state.registry = registry;
  if (!state.activeWorkspace) state.activeWorkspace = registry.active_workspace || "personal";
  const workspaces = registry.workspaces || [];
  const select = $("workspace-select");
  if (select) {
    select.innerHTML = workspaces.map((ws) => `
      <option value="${escapeHtml(ws.workspace_id)}" ${ws.workspace_id === state.activeWorkspace ? "selected" : ""}>
        ${escapeHtml(ws.name)}${ws.type === "organization" ? " (org)" : ""}${ws.status === "archived" ? " · archived" : ""}
      </option>
    `).join("");
  }
  const active = workspaces.find((ws) => ws.workspace_id === state.activeWorkspace);
  const rolePill = $("workspace-role");
  if (rolePill) rolePill.textContent = active ? (active.your_role || "—") : "";
  if (edition) {
    const pill = $("edition-pill");
    if (pill) pill.textContent = edition.edition || "community";
  }
  const list = $("workspace-list");
  if (list) {
    list.innerHTML = workspaces.length ? workspaces.map((ws) => `
      <div class="list-item">
        <div class="list-title">
          <span>${escapeHtml(ws.name)}</span>
          <span class="status-pill">${escapeHtml(ws.type)}</span>
        </div>
        <div class="meta-line">id: ${escapeHtml(ws.workspace_id)} · members: ${escapeHtml(ws.member_count)} · role: ${escapeHtml(ws.your_role || "—")} · ${escapeHtml(ws.status || "active")}</div>
        <div class="item-actions">
          ${ws.workspace_id === state.activeWorkspace ? `<span class="status-pill">active</span>` : `<button class="small-action" data-ws-action="activate" data-ws="${escapeHtml(ws.workspace_id)}"><i class="ti ti-switch-horizontal"></i>Switch</button>`}
          ${ws.type === "organization" ? `<button class="small-action" data-ws-action="members" data-ws="${escapeHtml(ws.workspace_id)}"><i class="ti ti-users"></i>Members</button>` : ""}
          ${ws.type === "organization" && ws.status !== "archived" ? `<button class="small-action" data-ws-action="archive" data-ws="${escapeHtml(ws.workspace_id)}"><i class="ti ti-archive"></i>Archive</button>` : ""}
        </div>
      </div>
    `).join("") : `<div class="list-item"><div class="meta-line">No workspaces.</div></div>`;
  }
  renderMembers(workspaces);
}

function renderMembers(workspaces) {
  const panel = $("member-panel");
  if (!panel) return;
  if (!state.managingWorkspace) {
    panel.hidden = true;
    return;
  }
  const ws = (workspaces || []).find((item) => item.workspace_id === state.managingWorkspace);
  if (!ws || ws.type !== "organization") {
    panel.hidden = true;
    return;
  }
  panel.hidden = false;
  const title = $("member-panel-title");
  if (title) title.textContent = `Members — ${ws.name}`;
  const members = ws.members || [];
  $("member-list").innerHTML = members.length ? members.map((m) => `
    <div class="list-item">
      <div class="list-title"><span>${escapeHtml(m.user_id)}</span><span class="status-pill">${escapeHtml(m.role)}</span></div>
      <div class="item-actions">
        <select class="small-action" data-member-role="${escapeHtml(m.user_id)}" data-ws="${escapeHtml(ws.workspace_id)}">
          ${["owner", "admin", "member", "viewer"].map((r) => `<option value="${r}" ${m.role === r ? "selected" : ""}>${r}</option>`).join("")}
        </select>
        <button class="small-action" data-member-remove="${escapeHtml(m.user_id)}" data-ws="${escapeHtml(ws.workspace_id)}"><i class="ti ti-user-minus"></i>Remove</button>
      </div>
    </div>
  `).join("") : `<div class="list-item"><div class="meta-line">No members yet.</div></div>`;
}

async function activateWorkspace(workspaceId) {
  await api("/workspace/activate", { method: "POST", body: JSON.stringify({ workspace_id: workspaceId }) });
  state.activeWorkspace = workspaceId;
  toast(`Switched to ${workspaceId}`);
  await refreshAll();
}

async function createOrg() {
  const name = ($("org-name").value || "").trim();
  if (!name) return;
  const result = await api("/workspace/orgs", { method: "POST", body: JSON.stringify({ name, settings: {} }) });
  $("org-name").value = "";
  toast(`Created ${result.workspace.workspace_id}`);
  state.managingWorkspace = result.workspace.workspace_id;
  await refreshAll();
}

async function addMember(workspaceId) {
  const userId = ($("member-user").value || "").trim();
  if (!userId) return;
  await api(`/workspace/orgs/${encodeURIComponent(workspaceId)}/members`, {
    method: "POST",
    body: JSON.stringify({ user_id: userId, role: $("member-role").value }),
  });
  $("member-user").value = "";
  toast(`Added ${userId}`);
  await refreshAll();
}

// ── Workspace summary (Phase 3) ──────────────────────────────────────────────
function renderWorkspaceSummary(os) {
  const reg = os?.workspace_registry || {};
  const workspaces = reg.workspaces || [];
  const activeId = state.activeWorkspace || reg.active_workspace;
  const active = workspaces.find((w) => w.workspace_id === activeId) || workspaces[0] || { name: "Personal Workspace", type: "personal", your_role: "owner", member_count: 1 };
  const counts = os?.counts || {};
  const scopePill = $("summary-scope-pill");
  if (scopePill) scopePill.textContent = active.type || "personal";
  const summary = $("workspace-summary");
  if (summary) {
    const stats = [["Snapshots", counts.snapshots], ["Memories", counts.memories], ["Agent runs", counts.agent_runs], ["Workflows", counts.workflows], ["Traces", counts.traces], ["Timeline", counts.timeline]];
    summary.innerHTML = `
      <div class="summary-main">
        <div class="summary-icon"><i class="ti ${active.type === "organization" ? "ti-building-community" : "ti-user"}"></i></div>
        <div class="summary-id">
          <div class="summary-name">${escapeHtml(active.name || "Personal Workspace")}</div>
          <div class="meta-line">${escapeHtml(active.type || "personal")} workspace · your role <strong>${escapeHtml(active.your_role || "owner")}</strong> · ${escapeHtml(active.member_count ?? 1)} member(s)</div>
        </div>
      </div>
      <div class="summary-stats">
        ${stats.map(([l, v]) => `<div class="summary-stat"><strong>${escapeHtml(v || 0)}</strong><span>${escapeHtml(l)}</span></div>`).join("")}
      </div>`;
  }
  const quick = $("workspace-quickswitch");
  if (quick) {
    quick.innerHTML = workspaces.map((w) => `
      <button class="switch-chip ${w.workspace_id === activeId ? "active" : ""}" data-ws-action="activate" data-ws="${escapeHtml(w.workspace_id)}">
        <i class="ti ${w.type === "organization" ? "ti-building-community" : "ti-user"}"></i>
        <span>${escapeHtml(w.name)}</span>${w.workspace_id === activeId ? ' <i class="ti ti-check"></i>' : ""}
      </button>`).join("");
  }
}

// ── Knowledge Graph explorer (Phase 2) ───────────────────────────────────────
const ENTITY_ICONS = { Person: "ti-user", Concept: "ti-bulb", Document: "ti-file-text", File: "ti-file", Code: "ti-code", Chat: "ti-message", Conversation: "ti-messages", Message: "ti-message-dots", Task: "ti-checklist", Decision: "ti-gavel", Error: "ti-alert-triangle", Model: "ti-cpu", Tool: "ti-tool", Project: "ti-folders", Feature: "ti-star", AIResponse: "ti-robot", Chunk: "ti-file-stack" };
function entityIcon(type) { return ENTITY_ICONS[type] || "ti-point"; }
function prettyId(id) { return String(id || "").split(":").slice(1).join(":") || String(id || ""); }

async function loadGraphExplorer() {
  try {
    const data = await api("/knowledge-graph/graph?limit=150");
    const nodes = (data.nodes || []).slice();
    nodes.sort((a, b) => (b.importance ?? b.metadata?.graph_metrics?.importance_raw ?? 0) - (a.importance ?? a.metadata?.graph_metrics?.importance_raw ?? 0));
    state.entities = nodes;
    renderEntities();
  } catch (e) {
    const el = $("entity-list");
    if (el) el.innerHTML = `<div class="list-item"><div class="meta-line">Knowledge graph unavailable: ${escapeHtml(e.message)}</div></div>`;
  }
}

function renderEntities() {
  const el = $("entity-list");
  if (!el) return;
  const q = ($("entity-search")?.value || "").toLowerCase().trim();
  const filtered = q ? state.entities.filter((n) => `${n.title || ""} ${n.type || ""} ${n.id || ""}`.toLowerCase().includes(q)) : state.entities;
  const list = filtered.slice(0, 40);
  el.innerHTML = list.length ? list.map((n) => {
    const m = n.metadata?.graph_metrics || {};
    const imp = Math.round((n.importance_norm ?? m.importance_norm ?? 0) * 100);
    return `
    <button class="list-item entity-card ${n.id === state.activeEntity ? "selected" : ""}" data-entity="${escapeHtml(n.id)}">
      <div class="list-title"><span><i class="ti ${entityIcon(n.type)}"></i> ${escapeHtml(n.title || prettyId(n.id))}</span><span class="status-pill">${escapeHtml(n.type || "node")}</span></div>
      ${n.summary ? `<div class="meta-line">${escapeHtml(String(n.summary).slice(0, 110))}</div>` : ""}
      <div class="tag-row"><span class="tag">${escapeHtml(m.degree ?? 0)} links</span><span class="tag">importance ${imp}%</span></div>
      <div class="importance-bar"><span style="width:${imp}%"></span></div>
    </button>`;
  }).join("") : `<div class="list-item"><div class="meta-line">No matching entities.</div></div>`;
}

async function selectEntity(id) {
  state.activeEntity = id;
  renderEntities();
  const detail = $("entity-detail");
  const title = $("entity-detail-title");
  if (title) title.textContent = "Loading…";
  try {
    const d = await api(`/workspace/relationships/${encodeURIComponent(id)}`);
    const node = d.node || {};
    const related = d.related_entities || [];
    const relMap = new Map(related.map((r) => [r.id, r]));
    const labelFor = (nodeId) => { const r = relMap.get(nodeId); return r ? (r.title || prettyId(nodeId)) : prettyId(nodeId); };
    const edgeRow = (e, dir) => {
      const other = dir === "out" ? e.to : e.from;
      return `<div class="rel-row"><span class="rel-dir">${dir === "out" ? "→" : "←"}</span><span class="tag">${escapeHtml(e.type || "related")}</span><span class="rel-node">${escapeHtml(labelFor(other))}</span></div>`;
    };
    const inbound = (d.inbound || []).slice(0, 8);
    const outbound = (d.outbound || []).slice(0, 8);
    const path = Array.isArray(d.shortest_path) ? d.shortest_path : [];
    if (title) title.textContent = node.title || prettyId(id);
    detail.innerHTML = `
      <div class="list-item">
        <div class="list-title"><span><i class="ti ${entityIcon(node.type)}"></i> ${escapeHtml(node.title || prettyId(id))}</span><span class="status-pill">${escapeHtml(node.type || "node")}</span></div>
        ${node.summary ? `<div class="meta-line">${escapeHtml(node.summary)}</div>` : ""}
        <div class="tag-row"><span class="tag">importance ${Math.round((node.importance_norm || 0) * 100)}%</span><span class="tag">${inbound.length + outbound.length} relationships</span></div>
      </div>
      <div class="list-item"><div class="list-title"><span>Outbound</span><span class="status-pill">${outbound.length}</span></div>${outbound.map((e) => edgeRow(e, "out")).join("") || '<div class="meta-line">None</div>'}</div>
      <div class="list-item"><div class="list-title"><span>Inbound</span><span class="status-pill">${inbound.length}</span></div>${inbound.map((e) => edgeRow(e, "in")).join("") || '<div class="meta-line">None</div>'}</div>
      ${related.length ? `<div class="list-item"><div class="list-title"><span>Related entities</span><span class="status-pill">${related.length}</span></div><div class="tag-row">${related.slice(0, 10).map((r) => `<span class="tag"><i class="ti ${entityIcon(r.type)}"></i> ${escapeHtml(r.title || prettyId(r.id))}</span>`).join("")}</div></div>` : ""}
      ${path.length ? `<div class="list-item"><div class="list-title"><span>Path to you</span><span class="status-pill">${path.length} hops</span></div><div class="meta-line">${path.map((p) => escapeHtml(typeof p === "string" ? prettyId(p) : (p.title || prettyId(p.id)))).join(" → ")}</div></div>` : ""}
      <div class="item-actions"><a class="small-action" href="/graph?node=${encodeURIComponent(id)}"><i class="ti ti-network"></i>Open in Graph Canvas</a></div>`;
  } catch (e) {
    if (title) title.textContent = "Relationships";
    detail.innerHTML = `<div class="list-item"><div class="meta-line">No relationships available: ${escapeHtml(e.message)}</div></div>`;
  }
}

// ── Recent activity feed (Phase 2), built from already-fetched data ───────────
function renderActivity({ traces, snapshots, memories, workflows, timeline }) {
  const items = [];
  (traces.traces || []).forEach((t) => items.push({ ts: t.created_at, icon: "ti-search", label: `Answer trace: ${t.question || "query"}`, tag: "graph rag" }));
  (snapshots.snapshots || []).forEach((s) => items.push({ ts: s.created_at, icon: "ti-stack-2", label: `Snapshot: ${s.name}`, tag: "snapshot" }));
  (memories.memories || []).forEach((m) => items.push({ ts: m.updated_at, icon: "ti-book-2", label: `Memory: ${(m.content || m.kind || "").slice(0, 60)}`, tag: m.kind || "memory" }));
  (workflows.workflows || []).forEach((w) => items.push({ ts: w.created_at, icon: "ti-git-branch", label: `Workflow: ${w.name}`, tag: "workflow" }));
  (timeline.events || []).forEach((e) => items.push({ ts: e.timestamp, icon: "ti-timeline-event", label: e.event_type || "event", tag: e.area || "workspace" }));
  items.sort((a, b) => String(b.ts || "").localeCompare(String(a.ts || "")));
  const el = $("activity-list");
  if (!el) return;
  el.innerHTML = items.length ? items.slice(0, 18).map((it) => `
    <div class="list-item activity-item">
      <div class="list-title"><span><i class="ti ${it.icon}"></i> ${escapeHtml(it.label)}</span><span class="status-pill">${escapeHtml(it.tag)}</span></div>
      <div class="meta-line">${escapeHtml(it.ts || "")}</div>
    </div>`).join("") : `<div class="list-item"><div class="meta-line">No recent activity yet — index a folder or ask a question to get started.</div></div>`;
}

function renderMemoryFeed(payload) {
  const memories = payload.memories || [];
  const el = $("memory-feed");
  if (!el) return;
  el.innerHTML = memories.length ? memories.slice(0, 8).map((m) => `
    <div class="list-item">
      <div class="list-title"><span><i class="ti ti-book-2"></i> ${escapeHtml(m.kind || "memory")}</span><span class="status-pill">${escapeHtml(m.updated_at || "")}</span></div>
      <div class="meta-line">${escapeHtml(String(m.content || "").slice(0, 140))}</div>
    </div>`).join("") : `<div class="list-item"><div class="meta-line">No workspace memory yet.</div></div>`;
}

// ── Enterprise capability panel (Phase 6) ─────────────────────────────────────
const CAPABILITY_LABELS = {
  sso_advanced: "Advanced SSO", idp_provisioning: "IdP Provisioning", scim: "SCIM",
  rbac_abac_advanced: "Advanced RBAC/ABAC", tenant_isolation: "Tenant Isolation",
  compliance_retention: "Compliance Retention", siem_export: "SIEM Export",
  private_vpc: "Private VPC", air_gapped_deployment: "Air-gapped Deploy",
  dlp_policy: "DLP Policy", ediscovery: "eDiscovery", admin_policy_packs: "Admin Policy Packs",
};
function renderEnterprise(edition) {
  edition = edition || {};
  const caps = edition.capabilities || {};
  const editionName = edition.edition || "community";
  const pill = $("enterprise-edition-pill");
  if (pill) { pill.textContent = editionName; pill.className = `status-pill ${edition.is_enterprise ? "status-complete" : ""}`; }
  const note = $("enterprise-note");
  if (note) note.textContent = edition.community_notice || "Community edition: every Enterprise capability below is an extension point and is disabled. Nothing here gates a Community feature.";
  const grid = $("capability-grid");
  if (!grid) return;
  const keys = Object.keys(caps).length ? Object.keys(caps) : Object.keys(CAPABILITY_LABELS);
  grid.innerHTML = keys.map((k) => {
    const on = Boolean(caps[k]);
    return `
    <div class="capability-card ${on ? "on" : "off"}">
      <i class="ti ${on ? "ti-circle-check" : "ti-lock"}"></i>
      <span class="cap-name">${escapeHtml(CAPABILITY_LABELS[k] || k)}</span>
      <span class="status-pill ${on ? "status-complete" : "status-failed"}">${on ? "enabled" : "disabled"}</span>
    </div>`;
  }).join("");
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
  if (os.workspace_registry) renderWorkspaceRegistry(os.workspace_registry, os.edition);
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
  renderWorkspaceSummary(os);
  renderEnterprise(os.edition);
  renderActivity({ traces, snapshots, memories, workflows, timeline });
  renderMemoryFeed(memories);
  loadGraphExplorer();
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
  const entityBtn = event.target.closest("[data-entity]");
  if (entityBtn) {
    selectEntity(entityBtn.dataset.entity).catch((err) => toast(err.message));
    return;
  }

  const skillTab = event.target.closest("[data-skill-tab]");
  if (skillTab) {
    state.skillTab = skillTab.dataset.skillTab;
    renderSkills();
    return;
  }

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
    return;
  }

  const wsBtn = event.target.closest("[data-ws-action]");
  if (wsBtn) {
    const action = wsBtn.dataset.wsAction;
    const ws = wsBtn.dataset.ws;
    if (action === "activate") {
      await activateWorkspace(ws);
    } else if (action === "members") {
      state.managingWorkspace = ws;
      if (state.registry) renderMembers(state.registry.workspaces);
    } else if (action === "archive") {
      await api(`/workspace/orgs/${encodeURIComponent(ws)}/archive`, { method: "POST" });
      toast(`Archived ${ws}`);
      await refreshAll();
    }
    return;
  }

  const removeBtn = event.target.closest("[data-member-remove]");
  if (removeBtn) {
    await api(`/workspace/orgs/${encodeURIComponent(removeBtn.dataset.ws)}/members/${encodeURIComponent(removeBtn.dataset.memberRemove)}`, { method: "DELETE" });
    toast("Member removed");
    await refreshAll();
  }
});

document.addEventListener("change", async (event) => {
  const roleSelect = event.target.closest("[data-member-role]");
  if (roleSelect) {
    try {
      await api(`/workspace/orgs/${encodeURIComponent(roleSelect.dataset.ws)}/members/${encodeURIComponent(roleSelect.dataset.memberRole)}`, {
        method: "PATCH",
        body: JSON.stringify({ role: roleSelect.value }),
      });
      toast("Role updated");
      await refreshAll();
    } catch (err) {
      toast(err.message);
    }
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
  const entitySearch = $("entity-search");
  if (entitySearch) entitySearch.addEventListener("input", () => renderEntities());
  const reloadEntities = $("reload-entities");
  if (reloadEntities) reloadEntities.addEventListener("click", () => loadGraphExplorer().catch((err) => toast(err.message)));
  const reloadActivity = $("reload-activity");
  if (reloadActivity) reloadActivity.addEventListener("click", () => refreshAll().catch((err) => toast(err.message)));
  $("workspace-select").addEventListener("change", (event) => activateWorkspace(event.target.value).catch((err) => toast(err.message)));
  $("create-org").addEventListener("click", () => createOrg().catch((err) => toast(err.message)));
  $("new-org-btn").addEventListener("click", () => $("org-name").focus());
  $("add-member").addEventListener("click", () => {
    if (state.managingWorkspace) addMember(state.managingWorkspace).catch((err) => toast(err.message));
  });
  refreshAll().catch((err) => toast(err.message));
});
