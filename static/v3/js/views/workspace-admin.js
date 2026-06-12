import { t } from "../core/i18n.js";

export async function render(ctx) {
  const { h, icon, api, store, c, toast } = ctx;
  const host = h("div.lt3-stack-6", c.loading({ lines: 5, block: true }));

  async function load() {
    const [registry, invites] = await Promise.all([api.workspaceRegistry(), api.invitations()]);
    const data = registry.data || {};
    const workspaces = Array.isArray(data.workspaces) ? data.workspaces : [];
    store.setWorkspaces(workspaces.length ? workspaces : store.get().workspaces);
    host.replaceChildren(
      c.viewHeader({
        eyebrow: t("workspace.eyebrow"),
        title: t("workspace.title"),
        sub: t("workspace.sub"),
        actions: [c.sourceBadge(registry.source)],
      }),
      createOrgPanel(),
      workspaceGrid(workspaces, data),
      invitationsPanel(invites),
    );
  }

  function createOrgPanel() {
    const name = h("input.lt3-input", { type: "text", "aria-label": t("workspace.orgName"), placeholder: t("workspace.orgName") });
    return c.panel({
      title: t("workspace.createOrg"),
      children: h("div.lt3-row-2",
        name,
        h("button.lt3-btn.lt3-btn--primary", { on: { click: async () => {
          if (!name.value.trim()) return;
          const res = await api.createOrg(name.value.trim());
          toast(resultText(res, t("workspace.createOrg")), res.ok ? "ok" : "err");
          if (res.ok) { name.value = ""; load(); }
        } } }, icon("plus"), t("workspace.createOrg")),
      ),
    });
  }

  function workspaceGrid(workspaces, registry) {
    if (!workspaces.length) {
      return c.emptyState({ icon: "building-community", title: t("workspace.title"), body: t("common.unavailable") });
    }
    return h("div.lt3-grid-auto", workspaces.map((ws) => workspaceCard(ws, registry)));
  }

  function workspaceCard(ws, registry) {
    const roleOptions = registry.roles || ["owner", "admin", "member", "viewer"];
    const members = Array.isArray(ws.members) ? ws.members : [];
    const userId = h("input.lt3-input", { type: "text", placeholder: t("workspace.userId"), "aria-label": t("workspace.userId") });
    const role = h("select.lt3-select", { "aria-label": t("common.role") }, roleOptions.map((r) => h("option", { value: r }, roleLabel(r))));
    return c.card(h("div.lt3-stack-4",
      h("div.lt3-row", { style: { "justify-content": "space-between", "align-items": "flex-start", gap: "var(--lt3-space-3)" } },
        h("div",
          h("b", ws.name || ws.workspace_id),
          h("div.lt3-faint", { style: { "font-size": "var(--lt3-text-2xs)", "font-family": "var(--lt3-font-mono)" } }, ws.workspace_id),
        ),
        c.pill(roleLabel(ws.your_role || "member"), "info"),
      ),
      h("dl.lt3-keyval",
        h("dt", t("common.type")), h("dd", ws.type || "—"),
        h("dt", t("common.status")), h("dd", c.statePill(ws.status || "active")),
        h("dt", t("workspace.members")), h("dd", String(ws.member_count ?? members.length)),
      ),
      h("div.lt3-row-2",
        h("button.lt3-btn.lt3-btn--subtle.lt3-btn--sm", { on: { click: () => activate(ws.workspace_id) } }, icon("selector"), t("workspace.activate")),
        ws.type === "organization" ? h("button.lt3-btn.lt3-btn--ghost.lt3-btn--sm", { on: { click: () => archive(ws.workspace_id) } }, icon("archive"), t("workspace.archive")) : null,
      ),
      ws.type === "organization" ? h("div.lt3-stack-3",
        h("div.lt3-eyebrow", t("workspace.members")),
        members.length ? c.table([
          { key: "user", label: t("workspace.userId"), render: (m) => h("span.lt3-mono", m.user_id || "—") },
          { key: "role", label: t("common.role"), width: "1%", render: (m) => c.pill(roleLabel(m.role)) },
          { key: "act", label: "", width: "1%", render: (m) => h("button.lt3-iconbtn.lt3-iconbtn--sm", { "aria-label": t("common.cancel"), on: { click: () => removeMember(ws.workspace_id, m.user_id) } }, icon("trash")) },
        ], members) : c.emptyState({ icon: "users", title: t("workspace.members"), body: t("common.none") }),
        h("div.lt3-row-2", userId, role, h("button.lt3-btn.lt3-btn--primary.lt3-btn--sm", { on: { click: () => addMember(ws.workspace_id, userId.value.trim(), role.value) } }, icon("user-plus"), t("workspace.addMember"))),
      ) : null,
    ), { interactive: false });
  }

  function invitationsPanel(invites) {
    const rows = Array.isArray(invites.data?.invitations) ? invites.data.invitations : [];
    const email = h("input.lt3-input", { type: "email", placeholder: t("workspace.inviteEmail"), "aria-label": t("workspace.inviteEmail") });
    const workspace = h("input.lt3-input", { type: "text", placeholder: "workspace_id", "aria-label": "workspace_id", value: store.get().workspaceId || "personal" });
    const role = h("select.lt3-select", { "aria-label": t("common.role") },
      ["member", "viewer", "admin"].map((r) => h("option", { value: r }, roleLabel(r))));
    const token = h("input.lt3-input", { type: "text", placeholder: t("workspace.inviteToken"), "aria-label": t("workspace.inviteToken") });

    return c.panel({
      title: t("workspace.invitations"),
      actions: [c.sourceBadge(invites.source)],
      children: h("div.lt3-stack-4",
        h("div.lt3-grid-2",
          h("div.lt3-field", h("label.lt3-label", t("workspace.inviteEmail")), email),
          h("div.lt3-field", h("label.lt3-label", "workspace_id"), workspace),
        ),
        h("div.lt3-row-2", role, h("button.lt3-btn.lt3-btn--primary", { on: { click: () => createInvite(email.value.trim(), workspace.value.trim(), role.value) } }, icon("mail-plus"), t("workspace.invitations"))),
        rows.length ? c.table([
          { key: "email", label: t("account.email"), render: (r) => r.email || "—" },
          { key: "role", label: t("common.role"), width: "1%", render: (r) => c.pill(roleLabel(r.role)) },
          { key: "token", label: t("workspace.inviteToken"), render: (r) => h("span.lt3-mono", r.token || r.id || "—") },
          { key: "status", label: t("common.status"), width: "1%", render: (r) => c.statePill(r.status || (r.accepted_at ? "ready" : "pending")) },
        ], rows.slice(0, 20)) : c.emptyState({ icon: "mail", title: t("workspace.invitations"), body: t("common.none") }),
        h("hr.lt3-divider"),
        h("div.lt3-row-2", token, h("button.lt3-btn.lt3-btn--ghost", { on: { click: () => acceptInvite(token.value.trim()) } }, icon("circle-check"), t("workspace.acceptInvite"))),
      ),
    });
  }

  async function activate(workspace_id) {
    const res = await api.activateWorkspace(workspace_id);
    toast(resultText(res, t("workspace.activated")), res.ok ? "ok" : "err");
    if (res.ok) { store.setWorkspace(workspace_id); load(); }
  }
  async function archive(workspace_id) {
    const res = await api.archiveWorkspace(workspace_id);
    toast(resultText(res, t("workspace.archived")), res.ok ? "ok" : "err");
    if (res.ok) load();
  }
  async function addMember(workspace_id, user_id, role) {
    if (!user_id) return;
    const res = await api.addWorkspaceMember(workspace_id, user_id, role);
    toast(resultText(res, t("workspace.memberAdded")), res.ok ? "ok" : "err");
    if (res.ok) load();
  }
  async function removeMember(workspace_id, user_id) {
    const res = await api.removeWorkspaceMember(workspace_id, user_id);
    toast(resultText(res, t("workspace.memberAdded")), res.ok ? "ok" : "err");
    if (res.ok) load();
  }
  async function createInvite(email, workspace_id, role) {
    const res = await api.createInvitation({ email: email || null, workspace_id: workspace_id || null, role, expires_hours: 168 });
    toast(resultText(res, t("workspace.inviteCreated")), res.ok ? "ok" : "err");
    if (res.ok) load();
  }
  async function acceptInvite(token) {
    if (!token) return;
    const res = await api.acceptInvitation(token);
    toast(resultText(res, t("workspace.inviteAccepted")), res.ok ? "ok" : "err");
    if (res.ok) load();
  }

  await load();
  return host;
}

function roleLabel(role) {
  return t(`common.${String(role || "member")}`) || String(role || "member");
}

function resultText(res, okText) {
  if (res && res.ok) return okText;
  const data = (res && res.data) || {};
  return String(data.detail || data.error || res?.error || t("common.unavailable"));
}
