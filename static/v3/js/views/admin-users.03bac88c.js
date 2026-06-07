/* ============================================================================
 * View: Admin · Users — workspace members and access.
 * Surfaces the membership roster and access summary for the active workspace.
 * Reads from /admin/summary + /admin/users (fallback-safe, badged) and never
 * invents backend mutations — actionable controls report unavailable write
 * operations when the backend does not expose them.
 * ========================================================================== */

import { timeAgo } from "../core/dom.a2773eb0.js";

const UNAVAILABLE = "not available from this read-only users view.";

export async function render(ctx) {
  const { h, icon, api, c, toast } = ctx;

  const statHost = h("div.lt3-statrow", c.loading({ lines: 1 }));
  const tableHost = h("div", c.loading({ lines: 4 }));
  const srcSlot = h("span", c.sourceBadge("pending"));

  const root = h("div.lt3-stack-6",
    c.viewHeader({
      eyebrow: "Administration",
      title: "Users",
      sub: "Workspace members and access.",
      actions: [
        h("button.lt3-btn.lt3-btn--primary",
          { on: { click: () => toast("Invite user is " + UNAVAILABLE, "info") } },
          icon("user-plus"), "Invite user"),
      ],
    }),
    statHost,
    c.panel({
      eyebrow: "Roster",
      head: h("div.lt3-row", { style: { "justify-content": "space-between", width: "100%" } },
        h("div",
          h("div.lt3-eyebrow", "Roster"),
          h("h3.lt3-panel__title", "Members"),
        ),
        srcSlot,
      ),
      children: tableHost,
    }),
  );

  async function load() {
    const [summary, users] = await Promise.all([api.adminSummary(), api.adminUsers()]);
    renderStats(summary);
    renderTable(users, c.sourceBadge(users.source));
  }

  function renderStats(res) {
    const s = normalizeSummary(res.data);
    statHost.replaceChildren(
      c.stat({ label: "Total users", value: c.fmtNum(s.total_users), icon: "users" }),
      c.stat({ label: "Active", value: c.fmtNum(s.active_users), icon: "user-check" }),
      c.stat({ label: "Admins", value: c.fmtNum(s.admin_users), icon: "shield-lock" }),
      c.stat({ label: "Messages", value: c.fmtNum(s.total_messages), icon: "message-2" }),
    );
  }

  function renderTable(res, badge) {
    srcSlot.replaceChildren(badge);
    const rows = normalizeUsers(res.data);
    if (!rows.length) {
      tableHost.replaceChildren(c.emptyState({
        icon: "user-off",
        title: "No members yet",
        body: "Invite teammates to give them access to this workspace.",
        action: h("button.lt3-btn.lt3-btn--subtle.lt3-btn--sm",
          { on: { click: () => toast("Invite user is " + UNAVAILABLE, "info") } },
          icon("user-plus"), "Invite user"),
      }));
      return;
    }
    tableHost.replaceChildren(c.table(columns(), rows));
  }

  function columns() {
    return [
      {
        key: "user", label: "User",
        render: (r) => h("div.lt3-row-2",
          h("span.lt3-avatar", initials(r.nickname, r.email)),
          h("div",
            h("div", { style: { "font-weight": "var(--lt3-weight-semi)" } }, r.nickname),
            h("div.lt3-faint", { style: { "font-size": "var(--lt3-text-xs)" } }, r.email),
          ),
        ),
      },
      {
        key: "role", label: "Role", width: "120px",
        render: (r) => c.pill(titleCase(r.role), roleVariant(r.role)),
      },
      {
        key: "status", label: "Status", width: "120px",
        render: (r) => c.statePill(r.disabled ? "disabled" : "active"),
      },
      {
        key: "last_seen", label: "Last seen", width: "130px",
        render: (r) => h("span.lt3-faint", { style: { "font-size": "var(--lt3-text-xs)" } },
          r.last_seen ? timeAgo(r.last_seen) : "—"),
      },
      {
        key: "actions", label: "", width: "48px",
        render: (r) => h("button.lt3-iconbtn.lt3-iconbtn--sm",
          {
            "aria-label": `Manage ${r.nickname || r.email}`,
            on: { click: () => toast(`Manage ${r.nickname || r.email} is ` + UNAVAILABLE, "info") },
          },
          icon("dots-vertical")),
      },
    ];
  }

  load();
  return root;
}

/* ── helpers ─────────────────────────────────────────────────────────────── */
function normalizeSummary(data) {
  const d = data && typeof data === "object" ? data : {};
  return {
    total_users: numOr(d.total_users),
    active_users: numOr(d.active_users),
    admin_users: numOr(d.admin_users),
    total_messages: numOr(d.total_messages),
  };
}

function normalizeUsers(data) {
  const list = Array.isArray(data) ? data : Array.isArray(data && data.users) ? data.users : [];
  return list
    .filter((u) => u && typeof u === "object")
    .map((u) => {
      const email = String(u.email || "").trim();
      return {
        email: email || "—",
        nickname: String(u.nickname || u.name || "").trim() || email.split("@")[0] || "Member",
        role: String(u.role || "member").trim().toLowerCase(),
        disabled: Boolean(u.disabled),
        last_seen: u.last_seen ?? u.lastSeen ?? null,
      };
    });
}

function numOr(v) {
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}

function roleVariant(role) {
  const r = String(role || "").toLowerCase();
  if (r === "owner" || r === "admin") return "info";
  return "";
}

function initials(nickname, email) {
  const base = String(nickname || email || "").trim();
  if (!base) return "?";
  const words = base.split(/[\s._-]+/).filter(Boolean);
  if (words.length >= 2) return (words[0][0] + words[1][0]).toUpperCase();
  return base.slice(0, 2).toUpperCase();
}

function titleCase(s) {
  const v = String(s || "");
  return v ? v.charAt(0).toUpperCase() + v.slice(1) : "Member";
}
