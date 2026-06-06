/* ============================================================================
 * View: Admin · Private VPC — network isolation and peering.
 * Lattice is local-first: by default everything runs on-prem with no external
 * network egress. Private VPC is an Enterprise networking extension for teams
 * that need cloud peering. Reads /vpc/status (fallback-safe, badged) and never
 * invents backend mutations — the peering control explains the write side is
 * pending so the surface stays integration-ready.
 * ========================================================================== */

import * as fx from "../core/fixtures.js";

const PENDING = "Peering configuration is pending backend integration.";

export async function render(ctx) {
  const { h, icon, api, c, toast } = ctx;

  const statusHost = h("div", c.loading({ lines: 4 }));
  const subnetsHost = h("div", c.loading({ lines: 3 }));
  const srcSlot = h("span", c.sourceBadge("pending"));

  const root = h("div.lt3-stack-6",
    c.viewHeader({
      eyebrow: "Administration",
      title: "Private VPC",
      sub: "Network isolation and peering.",
      actions: [
        h("button.lt3-btn.lt3-btn--primary",
          { on: { click: () => toast("Configure peering — " + PENDING, "info") } },
          icon("network"), "Configure peering"),
      ],
    }),

    c.banner(
      "Lattice is local-first. By default everything runs on this machine with no external network egress — Private VPC is an Enterprise networking extension for teams that need cloud peering.",
      "info", "shield-lock"),

    c.panel({
      eyebrow: "Network",
      head: h("div.lt3-row", { style: { "justify-content": "space-between", width: "100%" } },
        h("div",
          h("div.lt3-eyebrow", "Network"),
          h("h3.lt3-panel__title", "Connectivity status"),
        ),
        srcSlot,
      ),
      children: statusHost,
    }),

    c.panel({
      eyebrow: "Topology",
      title: "Private subnets",
      sub: "Peered subnets exposed to this workspace.",
      children: subnetsHost,
    }),

    buildPosture(ctx),
  );

  hydrate(ctx, { statusHost, subnetsHost, srcSlot });
  return root;
}

/* ── Network posture summary (always-true, local-first facts) ─────────────── */
function buildPosture({ h, icon, c }) {
  const items = [
    { icon: "plug-connected-x", label: "Egress", value: "None", variant: "ok", note: "No external network calls" },
    { icon: "cpu", label: "Inference", value: "Local", variant: "ok", note: "On-device MLX runtime" },
    { icon: "folder-lock", label: "Storage", value: "~/.ltcai", variant: "info", note: "Single-tenant on disk" },
  ];
  return h("section",
    c.sectionHead("Network posture"),
    h("div.lt3-grid-3",
      items.map((it) => c.card(
        h("div.lt3-stack-2",
          h("div.lt3-row", { style: { "justify-content": "space-between", "align-items": "flex-start" } },
            h("div.lt3-stat__label", icon(it.icon), it.label),
            c.pill(it.value, it.variant, { dot: true }),
          ),
          h("div.lt3-faint", { style: { "font-size": "var(--lt3-text-2xs)" } }, it.note),
        ),
        { flat: true },
      )),
    ),
  );
}

/* ── Hydration ────────────────────────────────────────────────────────────── */
async function hydrate(ctx, hosts) {
  const { h, icon, api, c } = ctx;
  const { statusHost, subnetsHost, srcSlot } = hosts;

  const res = await api.vpcStatus();
  const vpc = (res.data && typeof res.data === "object") ? res.data : fx.ADMIN.vpc;
  srcSlot.replaceChildren(c.sourceBadge(res.source));

  const subnets = Array.isArray(vpc.private_subnets) ? vpc.private_subnets : [];

  // Status key/value block.
  const rows = [
    { icon: "cloud", k: "Provider", v: vpc.provider || "local", mono: true },
    { icon: "map-pin", k: "Region", v: vpc.region || "on-prem", mono: true },
    { icon: "lock", k: "VPN status", node: c.statePill(vpc.vpn_status || "standby") },
    { icon: "arrows-transfer-up", k: "Peering status", node: c.statePill(vpc.peering_status || "not_configured") },
    { icon: "plug-connected-x", k: "Egress", node: c.pill("local-only", "ok", { dot: true }) },
    { icon: "subtask", k: "Subnets", v: String(subnets.length) },
  ];
  statusHost.replaceChildren(
    h("dl.lt3-keyval",
      rows.flatMap((r) => [
        h("dt", h("span.lt3-row-2", icon(r.icon), r.k)),
        h("dd", r.node ? r.node : (r.mono ? h("span.lt3-mono", String(r.v)) : String(r.v))),
      ]),
    ),
    !vpc.enabled && h("div.lt3-row-2", { style: { "margin-top": "var(--lt3-space-4)" } },
      c.pill("Enterprise extension", "info", { dot: true }),
      h("span.lt3-faint", { style: { "font-size": "var(--lt3-text-2xs)" } },
        "Private VPC is inactive — Lattice is running fully local."),
    ),
  );

  // Private subnets table / empty state.
  if (!subnets.length) {
    subnetsHost.replaceChildren(c.emptyState({
      icon: "network-off",
      title: "No private subnets",
      body: "Peering is not configured. Lattice runs fully local by default.",
    }));
    return;
  }

  const columns = [
    { key: "name", label: "Subnet", render: (s) => h("span.lt3-row-2", icon("subtask"), String(s.name || s.id || "subnet")) },
    { key: "cidr", label: "CIDR", render: (s) => h("span.lt3-mono", String(s.cidr || s.range || "—")) },
    { key: "zone", label: "Zone", render: (s) => String(s.zone || s.az || "—") },
    { key: "state", label: "State", width: "120px", render: (s) => c.statePill(s.state || "active") },
  ];
  subnetsHost.replaceChildren(c.table(columns, subnets));
}
