/**
 * The one coherent personal brain every capture screen is drawn from.
 *
 * Screens 05 and 11 have to look like the same computer, so node/edge counts,
 * workspace totals and pipeline stages all derive from these two arrays rather
 * than from per-route invented numbers.
 */
const path = require("path");

const { repoRoot } = require("./http.cjs");

const port = Number(process.env.LTCAI_VISUAL_PORT || 4927);
const appVersion = require(path.join(repoRoot, "package.json")).version;
const releaseRunId = `run-${appVersion.replace(/\D/g, "")}-product`;

// One coherent personal brain for release captures 05 + 11: pipeline counts
// (received/extracted/connected) must match this graph's scale so the two
// screenshots look like the same computer.
//
// Capture 05 types "workspace" into the graph search. Client-side filter is
// substring match on id/title/type/summary — at least 4 nodes and 3 edges
// among those nodes must survive that filter (B1). Labels below intentionally
// share the token "workspace" so the filtered subgraph stays a real map.
const graphNodes = [
  { id: "entity:lattice", type: "Topic", title: "Lattice Workspace", summary: "Local-first personal memory system for this workspace", importance_norm: 0.96, metadata: { graph_metrics: { degree: 6, importance_norm: 0.96, mention_count: 14, conversation_count: 5 } } },
  { id: "entity:workspace", type: "Concept", title: "Workspace Health", summary: "How this computer's Brain is doing in the workspace", importance_norm: 0.82, metadata: { graph_metrics: { degree: 4, importance_norm: 0.82 } } },
  { id: "entity:skills", type: "Task", title: "Skill Marketplace", summary: "Install, validate, and update skills", importance_norm: 0.72, metadata: { graph_metrics: { degree: 2, importance_norm: 0.72 } } },
  { id: "entity:enterprise", type: "Decision", title: "Enterprise Admin", summary: "Capability status without Community lockouts", importance_norm: 0.68, metadata: { graph_metrics: { degree: 2, importance_norm: 0.68 } } },
  { id: "entity:release", type: "Task", title: "릴리스 절차", summary: "How this release gets out the door", importance_norm: 0.88, metadata: { graph_metrics: { degree: 4, importance_norm: 0.88, mention_count: 9 } } },
  { id: "entity:memory", type: "Concept", title: "Workspace 개인 기억", summary: "Things kept only on this machine's workspace memory", importance_norm: 0.9, metadata: { graph_metrics: { degree: 3, importance_norm: 0.9 } } },
  { id: "entity:review", type: "Decision", title: "검토함", summary: "Changes waiting for a human yes", importance_norm: 0.7, metadata: { graph_metrics: { degree: 2, importance_norm: 0.7 } } },
  { id: "file:readme", type: "File", title: "workspace-README.md", summary: "Release documentation for this workspace", importance_norm: 0.58, metadata: { filename: "workspace-README.md", relative_path: "README.md", graph_metrics: { degree: 3, importance_norm: 0.58 } } },
  { id: "file:retrieval", type: "File", title: "retrieval-design.pdf", summary: "How search finds the right memory", importance_norm: 0.64, metadata: { filename: "retrieval-design.pdf", relative_path: "docs/retrieval-design.pdf", graph_metrics: { degree: 2, importance_norm: 0.64 } } },
  { id: "file:meeting", type: "File", title: "meeting-notes.md", summary: "Notes from last planning pass", importance_norm: 0.55, metadata: { filename: "meeting-notes.md", relative_path: "notes/meeting-notes.md", graph_metrics: { degree: 2, importance_norm: 0.55 } } },
  { id: "file:onboarding", type: "File", title: "onboarding.docx", summary: "How a new person starts with Lattice", importance_norm: 0.5, metadata: { filename: "onboarding.docx", relative_path: "docs/onboarding.docx", graph_metrics: { degree: 1, importance_norm: 0.5 } } },
  { id: "note:budget", type: "Note", title: "Q3 예산 메모", summary: "Personal note kept in the Brain", importance_norm: 0.48, metadata: { graph_metrics: { degree: 1, importance_norm: 0.48 } } },
];

// Edges among the four "workspace" search hits (lattice, workspace, memory,
// readme): lattice↔workspace, lattice↔memory, workspace→readme, memory→readme.
const graphEdges = [
  { from: "entity:lattice", to: "entity:workspace", type: "discusses", weight: 1.4 },
  { from: "entity:lattice", to: "entity:skills", type: "mentions", weight: 1.1 },
  { from: "entity:lattice", to: "entity:enterprise", type: "mentions", weight: 1.0 },
  { from: "entity:lattice", to: "entity:memory", type: "discusses", weight: 1.3 },
  { from: "entity:lattice", to: "entity:release", type: "mentions", weight: 1.2 },
  { from: "entity:workspace", to: "file:readme", type: "based_on", weight: 0.8 },
  { from: "entity:memory", to: "file:readme", type: "based_on", weight: 0.85 },
  { from: "entity:skills", to: "file:readme", type: "based_on", weight: 0.7 },
  { from: "entity:release", to: "file:retrieval", type: "based_on", weight: 0.9 },
  { from: "entity:review", to: "file:meeting", type: "based_on", weight: 0.75 },
];

function shortestPath(start, target) {
  if (!start || !target) return [];
  const adjacency = new Map();
  for (const edge of graphEdges) {
    if (!adjacency.has(edge.from)) adjacency.set(edge.from, []);
    if (!adjacency.has(edge.to)) adjacency.set(edge.to, []);
    adjacency.get(edge.from).push(edge.to);
    adjacency.get(edge.to).push(edge.from);
  }
  const queue = [[start]];
  const seen = new Set([start]);
  while (queue.length) {
    const path = queue.shift();
    const node = path[path.length - 1];
    if (node === target) return path;
    for (const next of adjacency.get(node) || []) {
      if (!seen.has(next)) {
        seen.add(next);
        queue.push([...path, next]);
      }
    }
  }
  return [];
}

const workspaceOs = {
  version: "1.7.0",
  updated_at: "2026-06-01T12:00:00",
  counts: { snapshots: 2, traces: 3, memories: 7, agent_runs: 4, workflows: 2, skills: 3, timeline: 8 },
  // Matches graphNodes (12) + graphEdges (10) used by capture 05 / pipeline 11.
  graph: {
    nodes: { Topic: 1, Concept: 2, Task: 2, Decision: 2, File: 4, Note: 1 },
    edges: { discusses: 2, mentions: 3, based_on: 5 },
  },
  models: { current_model: "mlx-community/Qwen3-VL-8B-Instruct-4bit", loaded_models: ["mlx-community/Qwen3-VL-8B-Instruct-4bit"], local_model: "mlx-community/Qwen3-VL-8B-Instruct-4bit" },
  workspace_registry: {
    active_workspace: "personal",
    workspaces: [
      { workspace_id: "personal", name: "Personal Workspace", type: "personal", your_role: "owner", member_count: 1, status: "active", members: [] },
      { workspace_id: "org-demo", name: "Design Org", type: "organization", your_role: "owner", member_count: 2, status: "active", members: [{ user_id: "admin@example.com", role: "owner" }, { user_id: "teammate@example.com", role: "member" }] },
    ],
  },
  edition: {
    edition: "community",
    is_enterprise: false,
    community_notice: "Community edition: Enterprise capabilities are extension points and do not gate Community features.",
    capabilities: {
      sso_advanced: false,
      idp_provisioning: false,
      scim: false,
      rbac_abac_advanced: false,
      tenant_isolation: false,
      compliance_retention: false,
      siem_export: false,
      private_vpc: false,
      dlp_policy: false,
      admin_policy_packs: false,
    },
  },
};

const snapshots = [
  { id: "snapshot-demo", name: "v4 checkpoint", created_at: "2026-06-01T12:00:00", node_count: 5, edge_count: 5, chat_count: 2, workspace_id: "personal" },
  { id: "snapshot-prev", name: "previous checkpoint", created_at: "2026-05-30T12:00:00", node_count: 3, edge_count: 2, chat_count: 1, workspace_id: "personal" },
];

const peers = [
  { peer_id: "peer-studio", name: "Studio Mac", base_url: "http://studio.local:8765", fingerprint: "sha256:VISUAL", public_key: "-----BEGIN PUBLIC KEY-----\\nvisual\\n-----END PUBLIC KEY-----" },
];

const enterpriseOverview = {
  edition: workspaceOs.edition,
  admin_policies: {
    capability: "admin_policy_packs",
    enabled: false,
    enforced: false,
    effective_policy: {
      base_roles: ["owner", "admin", "member", "viewer"],
      local_file_access: "approval-token gated (per path/user/action)",
      package_install: "admin-only with audit trail",
    },
    note: "Community features remain available.",
  },
  audit_export: {
    local_export: { available: true, endpoint: "/admin/security/export", formats: ["json", "csv", "xlsx", "txt", "pdf"] },
    siem_streaming: { enabled: false },
    compliance_retention: { enabled: false },
  },
  siem_export: {
    capability: "siem_export",
    enabled: false,
    streamed: false,
    destination: null,
    preview_envelope: { format: "ltcai.siem.v1", encoding: "ndjson", records: [{ ts: "2026-06-01T12:00:00", actor: "admin@example.com", act: "visual_smoke", sev: "informational" }] },
  },
  organization_settings: {
    community_baseline: { workspaces: ["personal", "organization"], roles: ["owner", "admin", "member", "viewer"], data_isolation: "single-tenant local storage (~/.ltcai)" },
    governance_capabilities: workspaceOs.edition.capabilities,
    note: "Enterprise governance is disabled in Community.",
  },
};

module.exports = {
  port,
  appVersion,
  releaseRunId,
  graphNodes,
  graphEdges,
  shortestPath,
  workspaceOs,
  snapshots,
  peers,
  enterpriseOverview,
};
