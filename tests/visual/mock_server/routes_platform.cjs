/**
 * Workspace OS, the three opt-in dials (features, permission mode, network
 * boundary), permissions and peers. The dial catalogs are captured for the
 * release evidence, so their shape has to be the server's, honesty included.
 *
 * Returns true when this module answered the request; false lets the entry
 * try the next module, in the same order the original if-chain ran.
 */
const { repoRoot, json } = require("./http.cjs");
const { appVersion, graphNodes, graphEdges, shortestPath, workspaceOs, snapshots, peers } = require("./fixtures.cjs");

module.exports = function handlePlatform({ req, res, url, pathname }) {
  if (pathname === "/health") return json(res, { status: "ok", version: appVersion, mode: "visual" });
  if (pathname === "/vpc/status") return json(res, { provider: "local", region: "visual", vpn_status: "standby", peering_status: "not_configured", private_subnets: [] });
  if (pathname === "/workspace/os") return json(res, workspaceOs);
  if (pathname === "/workspace/registry") return json(res, workspaceOs.workspace_registry);
  if (pathname === "/workspace/activate" && req.method === "POST") return json(res, { workspace: workspaceOs.workspace_registry.workspaces[0] });
  if (pathname.startsWith("/workspace/orgs/") && pathname.endsWith("/archive") && req.method === "POST") return json(res, { workspace: { workspace_id: pathname.split("/")[3], status: "archived" } });
  if (pathname.startsWith("/workspace/orgs/") && pathname.endsWith("/members") && req.method === "POST") return json(res, { workspace: workspaceOs.workspace_registry.workspaces[1] });
  if (pathname.startsWith("/workspace/orgs/") && (req.method === "PATCH" || req.method === "DELETE")) return json(res, { workspace: workspaceOs.workspace_registry.workspaces[1] });
  if (pathname === "/workspace/onboarding/status") return json(res, { current_step: "complete", steps: ["account", "admin", "hardware", "model_recommendation", "folder_connection", "complete"].map((id) => ({ id, status: "complete" })) });
  if (pathname === "/setup/scan") return json(res, {
    environment: {
      os: "darwin",
      arch: "arm64",
      ram_mb: 65536,
      gpu: { vendor: "apple", vram_mb: 65536 },
      installed_runtimes: ["local acceleration"],
      local_models: ["mlx-community/Qwen3-VL-8B-Instruct-4bit"],
    },
    recommendations: {
      summary: {
        zero_config: {
          model_id: "mlx-community/gemma-4-26b-a4b-it-4bit",
          rationale: ["Apple Silicon and 64 GB memory detected."],
        },
      },
    },
    zero_config: {
      recommend: { model_id: "mlx-community/gemma-4-26b-a4b-it-4bit" },
    },
  });
  if (pathname === "/workspace/traces") return json(res, { traces: [{ question: "What changed in v1.7.0?", confidence: 0.92, created_at: "2026-06-01T12:00:00", graph_nodes: graphNodes.slice(0, 2), source_files: [{ source: "README.md" }] }] });
  if (pathname === "/workspace/indexing") return json(res, { sources: [{ id: "source-demo", label: "Demo Repo", root_path: repoRoot, status: "indexed", success_count: 128, failure_count: 0, last_run_at: "2026-06-01T12:00:00", watch_active: true, file_status: { indexed: 128 } }] });
  if (pathname === "/workspace/snapshots") {
    if (req.method === "POST") return json(res, { snapshot: snapshots[0] });
    return json(res, { snapshots });
  }
  if (pathname === "/workspace/snapshots/compare" && req.method === "POST") return json(res, { summary: { nodes_added: 2, nodes_removed: 0, edges_added: 3, edges_removed: 0, decisions_changed: 1 } });
  if (pathname.startsWith("/workspace/snapshots/") && pathname.endsWith("/export") && req.method === "POST") return json(res, { snapshot_id: pathname.split("/")[3], export_path: "/tmp/snapshot.zip", bytes: 4096 });
  if (pathname.startsWith("/workspace/snapshots/") && pathname.endsWith("/restore") && req.method === "POST") return json(res, { restored: true, restore: { id: "restore-demo", mode: "merge", graph: { imported: true, nodes: 5, edges: 5 } } });
  if (pathname === "/workspace/memories") return json(res, { memories: [{ id: "mem-demo", kind: "decisions", content: "Ship graph and collaboration UX", updated_at: "2026-06-01T12:00:00", tags: ["release"] }] });
  if (pathname === "/workspace/computer-memory") return json(res, { enabled: false, approved: false, scopes: [], activities: [], notice: "disabled" });
  if (pathname === "/workspace/agents") return json(res, { agents: [{ id: "agent:planner", name: "Planner", role: "Plans release work", status: "available", relationships: ["agent:reviewer"] }] });
  if (pathname === "/workspace/workflows") return json(res, { workflows: [{ id: "wf-demo", name: "Validate -> Build -> Release", created_at: "2026-06-01T12:00:00", steps: [{ action: "validate" }, { action: "build" }] }] });
  if (pathname === "/workspace/skills") return json(res, {
    installed: [{ name: "code_review", description: "Review code changes", version: "1.0.0", enabled: true, installed: true, install_status: "ready", validation_status: "ready", source: "local" }],
    available: [
      { skill: "visual_regression", name: "visual_regression", description: "Capture and compare workspace UI", version: "1.2.0", category: "test", downloads: 2400, install_status: "available", validation_status: "not_installed", source: "marketplace" },
      { skill: "security_report", name: "security_report", description: "Summarize audit and policy risk", version: "1.1.0", category: "security", downloads: 1900, install_status: "available", validation_status: "not_installed", source: "marketplace" },
    ],
    total_installed: 1,
    total_available: 2,
  });
  if (pathname === "/workspace/time-machine") return json(res, { events: [{ event_type: "release_ready", area: "workspace", timestamp: "2026-06-01T12:00:00" }] });
  if (pathname === "/invitations") {
    if (req.method === "POST") return json(res, { invitation: { id: "invite-demo", token: "invite-token-demo", email: "new@example.com", role: "member", status: "pending" } });
    return json(res, { invitations: [{ id: "invite-demo", token: "invite-token-demo", email: "new@example.com", role: "member", status: "pending" }] });
  }
  if (pathname.startsWith("/invitations/") && pathname.endsWith("/accept") && req.method === "POST") return json(res, { invitation: { id: "invite-demo", status: "accepted" } });
  if (pathname === "/realtime/feed") return json(res, { events: [{ id: "evt-1", area: "workflow", event_type: "workflow_started", timestamp: "2026-06-01T12:00:00", payload: { run_id: "wf-run-approval" } }], stats: { events: 1 } });
  if (pathname === "/realtime/presence") return json(res, { presence: [{ client_id: "visual-client", user: "admin@example.com", workspace_id: "personal", last_seen: "2026-06-01T12:00:00" }], stats: { subscribers: 1 } });
  // The opt-in switchboard (v11.2.0). Same reason as the two dials below: the
  // 기능 drawer is captured for the release evidence, and a mock without this
  // route would photograph an empty panel for a feature that works (10.6.0).
  // The shape is the server's — `source`, `caution`, and an `available: false`
  // option with its reason all have to be real here or the capture would not
  // show the honesty this panel exists for.
  if (pathname === "/api/features" || pathname.startsWith("/api/features/")) {
    const features = [
      { id: "allow_multimodal", kind: "toggle", label: "사진·녹음도 기억하기", summary: "폴더를 읽을 때 글뿐 아니라 사진과 녹음도 함께 저장합니다.", default: false, current: true, source: "user", env_var: "LATTICEAI_ALLOW_MULTIMODAL", live: true, restart_required: false, caution: null, parent: null, choices: [] },
      { id: "video_ingest", kind: "toggle", label: "영상도 함께", summary: "사진·녹음을 켠 상태에서, 영상은 장면과 자막으로 저장합니다.", default: true, current: true, source: "default", env_var: "LATTICEAI_ALLOW_VIDEO", live: true, restart_required: false, caution: null, parent: "allow_multimodal", choices: [] },
      { id: "vault_watch", kind: "toggle", label: "노트 보관함 지켜보기", summary: "밖에 있는 노트 보관함이 바뀌면 알아서 다시 읽어옵니다.", default: false, current: true, source: "env", env_var: "LATTICEAI_VAULT_WATCH", live: true, restart_required: false, caution: null, parent: null, choices: [] },
      { id: "brain_network", kind: "toggle", label: "골라서 나누기", summary: "내가 고른 기억 묶음만 다른 기기로 내보내고 받아올 수 있습니다.", default: false, current: false, source: "default", env_var: "LATTICEAI_BRAIN_NETWORK", live: true, restart_required: false, caution: "이 기능만 기억을 이 컴퓨터 밖으로 내보냅니다. 받은 내용은 바로 합쳐지지 않고 검토함으로 갑니다.", parent: null, choices: [] },
      { id: "synthesis", kind: "toggle", label: "스스로 정리하기", summary: "자료가 쌓이면 알아서 훑어보고, 고칠 거리를 검토함에 제안합니다.", default: true, current: true, source: "default", env_var: "LATTICEAI_SYNTHESIS", live: true, restart_required: false, caution: null, parent: null, choices: [] },
      { id: "auto_vector_index", kind: "toggle", label: "넣자마자 검색 준비", summary: "새 자료를 넣으면 바로 의미 검색까지 준비합니다.", default: true, current: true, source: "default", env_var: "LATTICEAI_AUTO_VECTOR_INDEX", live: true, restart_required: false, caution: null, parent: null, choices: [] },
      { id: "auto_late_fusion", kind: "toggle", label: "글로 사진 찾기", summary: "글로 물어봐도 사진까지 함께 찾습니다.", default: false, current: false, source: "default", env_var: "LATTICEAI_TEXT_IMAGE_FUSION", live: true, restart_required: false, caution: null, parent: null, choices: [] },
      { id: "graph_expansion", kind: "toggle", label: "옆에 있는 기억까지 보기", summary: "찾은 기억과 바로 이어진 기억도 후보로 넣습니다.", default: false, current: false, source: "default", env_var: "LATTICEAI_GRAPH_EXPANSION", live: true, restart_required: false, caution: null, parent: null, choices: [] },
      { id: "vector_backend", kind: "choice", label: "의미 검색 방식", summary: "빠르기와 정확함 사이에서 고릅니다. 기본값은 전부 훑어보는 정확한 방식입니다.", default: "brute", current: "brute", source: "default", env_var: "LATTICEAI_VECTOR_INDEX", live: true, restart_required: false, caution: null, parent: null, choices: [
        { id: "brute", label: "전부 비교 (정확)", available: true, detail: null },
        { id: "quantized", label: "간추려 비교 (빠름)", available: true, detail: null },
        { id: "hnsw", label: "근사 검색 (가장 빠름)", available: false, detail: "설치 필요 — hnswlib is not available" },
      ] },
    ];
    if (req.method === "POST") {
      const id = decodeURIComponent(pathname.split("/")[3] || "");
      const entry = features.find((feature) => feature.id === id) || features[0];
      return json(res, { ...entry, current: entry.kind === "choice" ? entry.current : !entry.current, source: "user" });
    }
    return json(res, { features, note: "모두 지금 바로 적용됩니다. 다시 시작하지 않아도 됩니다." });
  }
  // Autonomy dial (v9.9.8). The settings screenshot renders this panel, so the
  // mock must serve the same catalog shape the real API does — otherwise the
  // release evidence would show an "unavailable" state for a working feature.
  if (pathname === "/api/permission-mode" || pathname === "/api/permission-mode/catalog") {
    const catalog = [
      { id: "strict", label: "Strict", label_ko: "엄격", summary: "Reads auto; writes and exec need approval or review proposals.", summary_ko: "읽기는 자동, 쓰기·실행은 승인 또는 변경 제안.", risk: "low", requires_ack: false },
      { id: "trusted", label: "Trusted", label_ko: "신뢰", summary: "Workspace writes and knowledge reads auto-run; exec/desktop control still gated.", summary_ko: "워크스페이스 쓰기·지식 읽기 자동. 실행·데스크톱 제어는 승인 필요.", risk: "medium", requires_ack: false },
      { id: "bypass", label: "Bypass", label_ko: "바이패스", summary: "YOLO inside the agent workspace. Hard circuit breakers still apply.", summary_ko: "에이전트 워크스페이스 안에서 전부 자동. 하드 차단만 남음.", risk: "high", requires_ack: true, warning: "Bypass skips routine approval prompts. Destructive system paths, root/home wipes, and blocked prefixes remain denied.", warning_ko: "바이패스는 일상 승인 프롬프트를 건너뜁니다. 시스템 경로 파괴, 루트/홈 삭제, 차단 접두사는 계속 거부됩니다." },
    ];
    if (pathname.endsWith("/catalog")) return json(res, { modes: catalog });
    const mode = req.method === "POST" ? "trusted" : "strict";
    const entry = catalog.find((item) => item.id === mode);
    return json(res, {
      mode, label: entry.label, label_ko: entry.label_ko, risk: entry.risk,
      requires_ack: entry.requires_ack, proposal_first: mode === "strict",
      workspace_writes_auto: mode !== "strict", knowledge_reads_auto: mode !== "strict",
      exec_auto: mode === "bypass", computer_observation_auto: mode !== "strict",
      computer_control_auto: mode === "bypass", circuit_breakers: true,
      catalog, scope: { user_email: "admin@example.com", workspace_id: null },
    });
  }
  // Network boundary dial (v10.1.1). Same reason as the autonomy dial above:
  // the settings screenshot renders this panel, so the mock has to serve the
  // real catalog shape or the evidence would show a working feature as broken.
  if (pathname.startsWith("/api/network-boundary")) {
    const catalog = [
      { id: "local_only", label: "Local only", label_ko: "로컬만", summary: "Nothing leaves this machine. Answers use local models and the local Brain only.", summary_ko: "이 컴퓨터를 벗어나지 않습니다. 로컬 모델과 로컬 Brain만 사용합니다.", risk: "low", requires_ack: false },
      { id: "cloud_allowed", label: "Cloud streaming allowed", label_ko: "클라우드 스트리밍 허용", summary: "Minimal related Knowledge Graph nodes may be sent to a cloud LLM. The streamed answer is written back into the local Brain with provenance.", summary_ko: "관련된 최소 Knowledge Graph 노드만 클라우드 LLM으로 전송될 수 있습니다. 스트리밍 답변은 provenance와 함께 로컬 Brain에 다시 기록됩니다.", risk: "medium", requires_ack: true, warning: "Cloud mode sends a compact summary of selected local nodes to an external provider. Sensitive nodes remain blocked.", warning_ko: "클라우드 모드는 선택된 로컬 노드의 압축 요약을 외부 제공자에게 전송합니다. 민감 노드는 계속 차단됩니다." },
    ];
    const policy = {
      blocked_node_types: [], blocked_metadata_flags: ["do_not_share", "local_only", "private", "sensitive"],
      auto_commit: false, allow_multimodal: false, min_extraction_confidence: 0.55,
    };
    if (pathname.endsWith("/catalog")) return json(res, { modes: catalog });
    if (pathname.endsWith("/policy")) return json(res, policy);
    if (pathname.endsWith("/preview")) {
      return json(res, {
        mode: "local_only", allows_cloud: false,
        node_ids: ["node-release", "node-checklist"], keywords: ["release", "checklist"],
        titles: ["릴리스 절차 정리", "배포 전 확인 목록"], types: ["Document", "Note"],
        token_estimate: 412, quality: "ok",
        compact_preview: "릴리스 절차 정리 · 배포 전 확인 목록",
        token_budget: { turn_limit: 2500, session_limit: 50000, session_used: 0 },
        would_block: null,
      });
    }
    // POST /api/network-boundary is the mode switch; the panel re-reads state
    // afterwards, so returning the acknowledged mode is enough.
    const mode = req.method === "POST" ? "cloud_allowed" : "local_only";
    const entry = catalog.find((item) => item.id === mode);
    return json(res, {
      mode, label: entry.label, label_ko: entry.label_ko, risk: entry.risk,
      requires_ack: entry.requires_ack, allows_cloud: mode === "cloud_allowed",
      warning_ko: entry.warning_ko || null,
      policy, token_budget: { turn_limit: 2500, session_limit: 50000, session_used: 0 },
      catalog, scope: { user_email: "admin@example.com", workspace_id: null },
    });
  }
  // Match latticeai/api/permissions.py _PERMISSION_ACTION_LABELS:
  // mapped actions use Korean labels; unmapped actions fall back to the raw key.
  // Capture 09 must exercise both paths so a Korean-label UI regression is not
  // masked by an English-only mock (and vice versa for the fallback path).
  if (pathname === "/permissions/pending") return json(res, {
    pending: {
      "perm-token": {
        path: "/tmp/report.md",
        action: "read",
        action_label: "파일 읽기",
        user_email: "admin@example.com",
        approved: false,
        expires_in: 300,
      },
      "perm-token-delete": {
        path: "/tmp/legacy-cache.bin",
        action: "delete",
        action_label: "delete",
        user_email: "admin@example.com",
        approved: false,
        expires_in: 240,
      },
    },
    count: 2,
  });
  if (pathname.startsWith("/permissions/approve/") && req.method === "POST") return json(res, { ok: true, token: pathname.split("/").pop() });
  if (pathname.startsWith("/permissions/deny/") && req.method === "POST") return json(res, { ok: true, denied: true, token: pathname.split("/").pop() });
  if (pathname === "/network/identity") return json(res, { device_id: "device-visual", fingerprint: "sha256:LOCAL", public_key: "-----BEGIN PUBLIC KEY-----\\nlocal\\n-----END PUBLIC KEY-----" });
  if (pathname === "/network/peers") {
    if (req.method === "POST") return json(res, { status: "paired", peer: peers[0] });
    return json(res, { peers });
  }
  if (pathname.startsWith("/network/peers/") && req.method === "DELETE") return json(res, { removed: true, peer_id: pathname.split("/").pop() });
  if (pathname.startsWith("/network/push/") && req.method === "POST") return json(res, { status: "ok", pushed: true, peer_id: pathname.split("/").pop() });
  if (pathname.startsWith("/workspace/relationships/")) {
    const id = pathname.replace("/workspace/relationships/", "");
    return json(res, { node_id: id, node: graphNodes.find((node) => node.id === id) || { id }, inbound: graphEdges.filter((edge) => edge.to === id), outbound: graphEdges.filter((edge) => edge.from === id), related_entities: graphNodes, shortest_path: shortestPath(id, url.searchParams.get("target_id")) });
  }
  return false;
};
