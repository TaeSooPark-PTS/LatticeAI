/* ============================================================================
 * View: Permissions — Administration · roles and capability mapping (RBAC).
 * Renders the role → capability matrix and per-role summaries from the admin
 * roles fixture. Capabilities map to product areas; "all" grants everything.
 * Role editing is integration-ready (no backend logic here) — actions surface
 * a clearly-labeled "pending backend" toast.
 *
 * View contract (shared by all views):
 *   export async function render(ctx) -> single DOM node
 *   ctx = { h, icon, api, store, c, route, params, navigate, toast }
 * ========================================================================== */

/* Capability columns, in product-area order. Each maps to a routable surface
 * and a Tabler icon so the matrix reads at a glance. */
const CAPS = [
  { key: "chat", label: "Chat", icon: "message-2", route: "chat" },
  { key: "search", label: "Search", icon: "arrows-join", route: "hybrid-search" },
  { key: "files", label: "Files", icon: "folders", route: "files" },
  { key: "pipeline", label: "Pipeline", icon: "git-branch", route: "pipeline" },
  { key: "users", label: "Users", icon: "users", route: "admin/users" },
  { key: "policies", label: "Policies", icon: "shield-lock", route: "admin/policies" },
  { key: "audit", label: "Audit", icon: "history", route: "admin/audit" },
  { key: "security", label: "Security", icon: "shield-check", route: "admin/security" },
];

const ROLE_META = {
  owner: { icon: "crown", variant: "ok" },
  admin: { icon: "shield-check", variant: "info" },
  member: { icon: "user-check", variant: "" },
  viewer: { icon: "eye", variant: "warn" },
};
const metaFor = (role) => ROLE_META[String(role).toLowerCase()] || { icon: "user", variant: "" };

/** A role grants a capability when it holds "all" or that specific cap. */
const grants = (caps, key) => Array.isArray(caps) && (caps.includes("all") || caps.includes(key));
const capLabel = (key) => (CAPS.find((cc) => cc.key === key)?.label) || key;

export async function render(ctx) {
  const { h, icon, c, navigate, toast } = ctx;

  // Live RBAC roles from /admin/roles; clearly-badged sample data on fallback.
  const res = await ctx.api.adminRoles();
  const roles = Array.isArray(res.data && res.data.roles) ? res.data.roles
    : (Array.isArray(res.data) ? res.data : []);
  const source = res.source;
  const totalMembers = roles.reduce((sum, r) => sum + (r.members || 0), 0);

  const root = h("div.lt3-stack-6",
    c.viewHeader({
      eyebrow: "Administration",
      title: "Permissions",
      sub: "Roles and capability mapping.",
      actions: [
        c.sourceBadge(source),
        h("button.lt3-btn.lt3-btn--ghost", { on: { click: () => navigate("admin/users") } }, icon("users"), "Members"),
        h("button.lt3-btn.lt3-btn--primary", { on: { click: () => pendingToast(toast, "Creating a role") } }, icon("plus"), "New role"),
      ],
    }),

    c.banner(
      "Access is role-based (RBAC): every member holds exactly one role, and each role grants a set of capabilities that map to product areas. The owner role grants all capabilities.",
      "info",
      "shield-lock",
    ),

    h("div.lt3-statrow",
      c.stat({ label: "Roles", value: roles.length, icon: "id-badge-2" }),
      c.stat({ label: "Members", value: c.fmtNum(totalMembers), icon: "users" }),
      c.stat({ label: "Capabilities", value: CAPS.length, icon: "key" }),
      c.stat({ label: "Full-access roles", value: roles.filter((r) => (r.caps || []).includes("all")).length, icon: "crown" }),
    ),

    c.panel({
      eyebrow: "RBAC",
      title: "Capability matrix",
      sub: "Which product areas each role can reach. Scroll horizontally to see every capability.",
      children: buildMatrix(ctx, roles),
    }),

    h("section",
      c.sectionHead("Roles", c.sourceBadge(source)),
      buildRoleGrid(ctx, roles),
    ),
  );

  return root;
}

/* ── Capability matrix ──────────────────────────────────────────────────── */
function buildMatrix(ctx, roles) {
  const { h, icon, c } = ctx;

  if (!roles.length) {
    return c.emptyState({ icon: "lock-off", title: "No roles defined", body: "Define a role to start mapping capabilities." });
  }

  const columns = [
    {
      key: "role",
      label: "Role",
      width: "180px",
      render: (r) => {
        const m = metaFor(r.role);
        return h("div.lt3-row-2", { style: { "align-items": "center" } },
          h("span.lt3-result__rank", { style: { color: "var(--accent)" } }, icon(m.icon)),
          h("div.lt3-stack",
            h("b", { style: { "font-size": "var(--lt3-text-sm)", "text-transform": "capitalize" } }, r.role),
            c.pill(`${c.fmtNum(r.members || 0)} ${(r.members === 1) ? "member" : "members"}`, m.variant || "", { dot: true }),
          ),
        );
      },
    },
    ...CAPS.map((cap) => ({
      key: cap.key,
      label: cap.label,
      render: (r) => cell(ctx, grants(r.caps, cap.key)),
    })),
  ];

  return c.table(columns, roles);
}

/** A matrix cell: accent check when granted, muted dash when not. */
function cell({ h, icon }, granted) {
  return h("div", { style: { display: "grid", "place-items": "center" }, "aria-label": granted ? "granted" : "not granted" },
    granted
      ? h("span", { style: { color: "var(--accent)", "font-size": "var(--lt3-text-lg)", "line-height": "1" } }, icon("check"))
      : h("span.lt3-faint", { style: { "font-size": "var(--lt3-text-lg)", "line-height": "1" }, "aria-hidden": "true" }, "–"),
  );
}

/* ── Per-role summary cards ─────────────────────────────────────────────── */
function buildRoleGrid(ctx, roles) {
  const { h } = ctx;
  if (!roles.length) {
    return ctx.c.emptyState({ icon: "lock-off", title: "No roles defined", body: "Define a role to map capabilities." });
  }
  return h("div.lt3-grid-auto", roles.map((r) => roleCard(ctx, r)));
}

function roleCard(ctx, r) {
  const { h, icon, c, toast } = ctx;
  const m = metaFor(r.role);
  const isAll = (r.caps || []).includes("all");
  const grantedKeys = isAll ? CAPS.map((cc) => cc.key) : CAPS.filter((cc) => (r.caps || []).includes(cc.key)).map((cc) => cc.key);

  return c.card(
    h("div.lt3-stack-3",
      h("div.lt3-row", { style: { "justify-content": "space-between", "align-items": "flex-start" } },
        h("div.lt3-row-2", { style: { "align-items": "center" } },
          h("span.lt3-quick__icon", icon(m.icon)),
          h("div.lt3-stack",
            h("b", { style: { "font-size": "var(--lt3-text-md)", "text-transform": "capitalize" } }, r.role),
            h("div.lt3-faint", { style: { "font-size": "var(--lt3-text-2xs)" } }, `${c.fmtNum(r.members || 0)} ${(r.members === 1) ? "member" : "members"}`),
          ),
        ),
        c.pill(isAll ? "Full access" : `${grantedKeys.length}/${CAPS.length}`, m.variant || "info"),
      ),

      h("div.lt3-cluster", { "aria-label": `${r.role} capabilities` },
        isAll
          ? h("span.lt3-chip", { dataset: { active: "true" } }, icon("infinity"), "All capabilities")
          : (grantedKeys.length
              ? grantedKeys.map((k) => h("span.lt3-chip", { dataset: { active: "true" } }, icon(CAPS.find((cc) => cc.key === k).icon), capLabel(k)))
              : h("span.lt3-faint", { style: { "font-size": "var(--lt3-text-xs)" } }, "No capabilities")),
      ),

      h("button.lt3-btn.lt3-btn--subtle.lt3-btn--sm", { style: { "align-self": "flex-start" }, on: { click: () => pendingToast(toast, `Editing the ${r.role} role`) } },
        icon("edit"), "Edit role"),
    ),
    { attrs: { "data-role": r.role } },
  );
}

/* ── Pending-backend affordance ─────────────────────────────────────────── */
function pendingToast(toast, what) {
  toast(`${what} is not available in this build — roles are a fixed RBAC model (owner · admin · member · viewer).`, "warn");
}
