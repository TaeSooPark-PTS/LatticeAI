/**
 * Admin console, security, proposals and the automation review inbox.
 *
 * Returns true when this module answered the request; false lets the entry
 * try the next module, in the same order the original if-chain ran.
 */
const { json } = require("./http.cjs");
const { port, appVersion, releaseRunId, workspaceOs, enterpriseOverview } = require("./fixtures.cjs");

module.exports = function handleAdmin({ req, res, url, pathname }) {
  if (pathname === "/admin/summary") return json(res, { total_users: 2, active_users: 2, admin_users: 1, total_messages: 42, user_messages: 21, assistant_messages: 21 });
  // Calm admin header (layout rebuild screen 10). ``attention`` so the
  // non-ok layout is what the release capture actually shows.
  if (pathname === "/admin/health-summary") return json(res, {
    status: "attention",
    issue_count: 1,
    issues: [
      { area: "security", severity: "warning", message: "1 medium-risk event awaiting review" },
    ],
  });
  if (pathname === "/admin/users") return json(res, [{ email: "admin@example.com", nickname: "Admin", role: "admin", disabled: false }, { email: "member@example.com", nickname: "Member", role: "user", disabled: false }]);
  if (pathname === "/admin/sensitivity") return json(res, { summary: { risky_messages: 1, compliant_messages: 41, risk_rate: 2, severity_counts: { high: 0 }, field_counts: {}, user_counts: {} }, risk_fields: [], compliance_fields: [] });
  if (pathname === "/admin/invite-link") return json(res, { invite_url: `http://127.0.0.1:${port}/`, invite_code: "visual", gate_enabled: false });
  if (pathname === "/admin/stats") return json(res, { daily: [{ date: "2026-06-01", user: 8, assistant: 8 }] });
  if (pathname === "/admin/audit") return json(res, { summary: { total_events: 12, chat_events: 6, user_messages: 3, assistant_messages: 3, document_uploads: 2, clear_events: 1, sensitive_events: 1, high_sensitive_events: 0 }, filters: { matched_events: 5, scoped_events: 12, limit: 50 }, graph: workspaceOs.graph, per_user: [], recent_events: [
    { ts: "2026-06-06T09:12:00", actor: "admin@example.com", action: "policy.update", target: "local_file_access", severity: "notice" },
    { ts: "2026-06-06T10:40:00", actor: "member@example.com", action: "search.hybrid", target: "q: retrieval design", severity: "informational" },
    { ts: "2026-06-06T11:05:00", actor: "admin@example.com", action: "user.invite", target: "guest@example.com", severity: "notice" },
    { ts: "2026-06-06T12:30:00", actor: "system", action: "index.rebuild", target: "vector_index", severity: "informational" },
    { ts: "2026-06-06T13:15:00", actor: "member@example.com", action: "file.access.denied", target: "secrets/.env", severity: "warning" },
  ] });
  if (pathname === "/admin/sso") return json(res, { enabled: false, provider_name: "Okta", discovery_url: "", client_id: "", redirect_uri: "", scopes: "openid email profile" });
  if (pathname === "/admin/enterprise") return json(res, enterpriseOverview);
  if (pathname === "/admin/enterprise/siem-export") return json(res, enterpriseOverview.siem_export);
  if (pathname === "/admin/roles") return json(res, { roles: [
    { role: "admin", members: 1, caps: ["users", "policies", "audit", "security", "chat", "search", "files", "pipeline"] },
    { role: "user", members: 1, caps: ["chat", "search", "files", "pipeline"] },
  ] });
  if (pathname === "/admin/policies") return json(res, { policies: [
    { id: "local_file_access", label: "Local file access", value: "Approval-token gated (per path/user/action)", enforced: true },
    { id: "package_install", label: "Package install", value: "Admin-only with audit trail", enforced: true },
    { id: "data_residency", label: "Data residency", value: "Single-tenant local storage (~/.ltcai)", enforced: true },
    { id: "model_egress", label: "Model egress", value: "Local-only by default", enforced: true },
    { id: "log_retention", label: "Log retention", value: "90 day local audit window", enforced: true },
  ] });
  if (pathname === "/admin/log-retention") return json(res, {
    mode: "local-first",
    retention_days: 90,
    total_events: 12,
    retained_events: 12,
    prune_candidates: 0,
    export_before_prune: true,
    editable: false,
  });
  if (pathname === "/admin/product-hardening") return json(res, {
    version: "4.3.3",
    startup: { local_only_default: true, host: "127.0.0.1", port: 4825, network_exposed: false },
    privacy: { local_only_default: true, integrations: { telegram: { enabled: false, credential_present: false, opt_in_required: true } } },
    storage: { active: { engine: "sqlite", available: true } },
    backup: { available: true, count: 1 },
    device_identity: { fingerprint: "sha256:LOCAL", algorithm: "ed25519", storage: "file" },
    permissions: { export_requires_admin: true, import_requires_admin: true, destructive_restore_requires_confirmation: true },
  });
  if (pathname === "/admin/security/overview") return json(res, {
    generated_at: "2026-06-06T12:00:00", risk_rate: 2,
    cards: { events_today: 5, high_risk_events: 0, risky_chats: 1, review_required: 1 },
    severity_counts: { high: 0, medium: 1, low: 2 }, field_counts: { email: 4, api_key: 1 },
  });
  if (pathname.startsWith("/admin/security/")) return json(res, { cards: {}, users: [], events: [], files: [], field_counts: {} });
  // Keeps the Review Center's pending-proposal badge consistent with the one
  // change_proposal in the reviews fixture below.
  if (pathname === "/api/proposals/counts") return json(res, { pending: 1 });
  // The runs tab promoted "설치된 자동화" to its second tier, between the
  // approval inbox and the run history. This route did not exist, so the client
  // fell back to its `installed: []` default and the promoted panel was
  // captured as an empty state — the screenshot showed the new hierarchy with
  // its middle tier blank. Two entries so the lg:grid-cols-2 layout is exercised,
  // one already dry-run and one never run, which is what the card's two-step
  // control is there to distinguish.
  if (pathname === "/api/automation/overview") {
    return json(res, {
      suggestions: [],
      questions_scanned: 12,
      installed: [
        {
          id: "wf-daily-digest",
          name: "매일 기억 요약",
          enabled: true,
          requires_user_enable: false,
          creates: ["note"],
          last_execution: {
            mode: "dry_run",
            status: "ok",
            summary: "3개 항목을 요약할 예정입니다",
            run_id: "run-digest-1",
            finished_at: "2026-06-22T09:00:00",
          },
        },
        {
          id: "wf-weekly-review",
          name: "주간 되돌아보기",
          enabled: false,
          requires_user_enable: true,
          creates: ["document"],
          last_execution: null,
        },
      ],
    });
  }
  if (pathname === "/automation/reviews") {
    const items = [
      {
        id: "rev-7-8-release",
        status: "pending",
        effective_status: "pending",
        title: `Approve ${appVersion} product readiness evidence`,
        summary: "Review generated screenshots, exact artifacts, and product readiness gates before release.",
        source: "workflow_run",
        kind: "release_review",
        payload: { last_run_id: releaseRunId },
        provenance: { workflow_id: "wf-release", run_id: releaseRunId, source_detail: `${appVersion} release workflow` },
        created_at: "2026-06-22T12:00:00Z",
        updated_at: "2026-06-22T12:05:00Z",
      },
      {
        // A change proposal with a real diff. The Review Center card puts the
        // evidence on the left and the approve/reject decision on the right,
        // and without a proposal in the fixture the left column is empty — the
        // release screenshot would show the layout with nothing in it.
        id: "rev-proposal-readme",
        status: "pending",
        effective_status: "pending",
        title: "README 릴리스 표를 최신 버전으로 고칩니다",
        summary: "릴리스 기록 표에 이번 버전 줄을 추가합니다. 승인하면 파일에 그대로 적용됩니다.",
        source: "change_proposal",
        kind: "file_write",
        payload: {
          path: "README.md",
          tier: "small",
          diff: [
            "--- a/README.md",
            "+++ b/README.md",
            "@@ -18,6 +18,7 @@",
            " ## Release History",
            " ",
            " | Version | Theme |",
            " | --- | --- |",
            `+| ${appVersion} | First Things |`,
            " | 10.6.0 | Promoted Panels |",
          ],
        },
        provenance: {
          risk: "low",
          change_class: "docs",
          tool: "write_file",
          proposed_by: "Brain",
          source_detail: "문서 정리 자동화",
        },
        created_at: "2026-06-22T12:01:00Z",
        updated_at: "2026-06-22T12:01:00Z",
      },
      {
        id: "rev-kg-digest",
        status: "pending",
        effective_status: "pending",
        title: "Review new Knowledge Graph digest",
        summary: "Three new project memories are ready to become durable context.",
        source: "kg_change_digest",
        kind: "memory_digest",
        payload: {},
        provenance: { source_detail: "Brain ingestion pipeline" },
        created_at: "2026-06-22T12:02:00Z",
        updated_at: "2026-06-22T12:02:00Z",
      },
    ];
    const status = url.searchParams.get("status");
    const source = url.searchParams.get("source");
    return json(res, {
      items: items.filter((item) => (!status || item.effective_status === status) && (!source || item.source === source)),
    });
  }
  if (pathname.startsWith("/automation/reviews/")) {
    return json(res, {
      id: "rev-7-8-release",
      status: "pending",
      effective_status: "pending",
      title: `Approve ${appVersion} product readiness evidence`,
      summary: "Action preview completed.",
      source: "workflow_run",
      kind: "release_review",
      payload: { last_run_id: releaseRunId },
      provenance: { workflow_id: "wf-release", run_id: releaseRunId, source_detail: `${appVersion} release workflow` },
    });
  }
  return false;
};
