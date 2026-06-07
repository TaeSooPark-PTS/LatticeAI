/* ============================================================================
 * View: Policies — administration · governance and enforcement.
 * Surfaces the local-first guardrails the workspace enforces and the open-core
 * seam where Enterprise governance packs extend them. Policy state reflects the
 * documented future contract (fx.ADMIN.policies); toggles are integration-ready
 * and clearly defer their enforcement to the backend.
 * ========================================================================== */

import * as fx from "../core/fixtures.js";

// Governance capabilities that live behind the open-core Enterprise seam. These
// are extension points, not implemented backend logic — labeled as sample data.
const PACKS = [
  { id: "siem", icon: "broadcast", title: "SIEM export", desc: "Stream the audit trail to an external SIEM (Splunk, Elastic, Sentinel)." },
  { id: "retention", icon: "archive", title: "Compliance retention", desc: "Configurable retention windows and legal-hold for messages and traces." },
  { id: "isolation", icon: "wall", title: "Tenant isolation", desc: "Hard multi-tenant boundaries with per-tenant keys and storage." },
];

export async function render(ctx) {
  const { h, icon, c, toast } = ctx;

  // Sourced from the documented admin contract; badged as sample until live.
  const policies = fx.ADMIN.policies || [];
  const source = "placeholder";

  const root = h("div.lt3-stack-6",
    c.viewHeader({
      eyebrow: "Administration",
      title: "Policies",
      sub: "Governance and enforcement.",
      actions: [
        h("button.lt3-btn.lt3-btn--primary", {
          type: "button",
          on: { click: () => toast("New policy — drafting and persistence land with the backend (pending).", "info") },
        }, icon("plus"), "New policy"),
      ],
    }),

    c.banner(
      "Policies enforce Lattice's local-first guardrails. Enterprise packs extend them with org-wide governance.",
      "info",
      "shield-lock",
    ),

    h("section.lt3-stack-3",
      c.sectionHead(
        "Active guardrails",
        c.sourceBadge(source),
      ),
      policies.length
        ? h("div.lt3-stack-3", policies.map((p) => policyRow(ctx, p)))
        : c.emptyState({ icon: "shield-off", title: "No policies defined", body: "Policies appear once the governance backend is connected." }),
    ),

    packsPanel(ctx),
  );

  return root;
}

/* ── One policy row: description, live state, and an enforce toggle ───────── */
function policyRow({ h, icon, c, toast }, p) {
  const inputId = `lt3-pol-${p.id}`;
  const stateSlot = h("div", c.statePill(p.enforced ? "active" : "idle"));

  const onToggle = (e) => {
    const enforced = e.target.checked;
    stateSlot.replaceChildren(c.statePill(enforced ? "active" : "idle"));
    toast(`Policy ${p.label} ${enforced ? "enforced" : "relaxed"} — pending backend`, "info");
  };

  return c.card(
    h("div.lt3-row", { style: { "justify-content": "space-between", "align-items": "flex-start", "gap": "var(--lt3-space-4)", "flex-wrap": "wrap" } },
      h("div.lt3-stack-2", { style: { "min-width": "0", "flex": "1 1 320px" } },
        h("div.lt3-row-2",
          h("span.lt3-card__icon", { style: { color: "var(--accent)" } }, icon("shield-check")),
          h("h3", { style: { "font-size": "var(--lt3-text-base)", "font-weight": "var(--lt3-weight-semibold)", "margin": "0" } }, p.label),
          stateSlot,
        ),
        h("p.lt3-muted", { style: { "font-size": "var(--lt3-text-sm)", "margin": "0" } }, p.value),
      ),
      // Token-native toggle (.lt3-switch markup: label > input + span).
      h("label.lt3-switch", { for: inputId, title: p.enforced ? "Enforced" : "Relaxed" },
        h("input", { id: inputId, type: "checkbox", checked: p.enforced, "aria-label": `Enforce policy: ${p.label}`, on: { change: onToggle } }),
        h("span"),
      ),
    ),
  );
}

/* ── Enterprise governance packs (open-core extension points) ────────────── */
function packsPanel({ h, icon, c }) {
  return c.panel({
    eyebrow: "Open core",
    title: "Policy packs",
    sub: "Governance capabilities available as Enterprise extension points on top of the local-first core.",
    actions: c.sourceBadge("placeholder"),
    children: h("div.lt3-stack-2",
      PACKS.map((pk) => h("div.lt3-card.lt3-card--flat",
        h("div.lt3-row", { style: { "justify-content": "space-between", "align-items": "center", "gap": "var(--lt3-space-4)", "flex-wrap": "wrap" } },
          h("div.lt3-row-2", { style: { "min-width": "0", "flex": "1 1 320px" } },
            h("span.lt3-card__icon", { style: { color: "var(--muted)" } }, icon(pk.icon)),
            h("div.lt3-stack-2", { style: { "min-width": "0" } },
              h("div", { style: { "font-weight": "var(--lt3-weight-medium)" } }, pk.title),
              h("div.lt3-faint", { style: { "font-size": "var(--lt3-text-xs)" } }, pk.desc),
            ),
          ),
          h("div.lt3-row-2", { style: { "flex": "none" } },
            c.pill("Enterprise", "info"),
            h("span.lt3-faint", { style: { "font-size": "var(--lt3-text-2xs)" } }, "Available as an extension point"),
          ),
        ),
      )),
    ),
  });
}
