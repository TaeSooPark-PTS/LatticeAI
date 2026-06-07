/* ============================================================================
 * View: Admin · Security — sensitive-data signals and DLP.
 * Surfaces the workspace's data-loss-prevention posture: how many messages
 * tripped a sensitive-data signal, how severe, and which field patterns matched.
 * Calm and local-first by design — every scan runs on this machine, so the
 * reassurance ("nothing leaves the computer") is the headline, not a footnote.
 * Reads /admin/security/overview and renders unavailable state when it cannot
 * load.
 * ========================================================================== */

const SEVERITY = [
  { key: "high", label: "High", variant: "warn", icon: "alert-triangle", desc: "Strong sensitive-data match" },
  { key: "medium", label: "Medium", variant: "", icon: "alert-circle", desc: "Likely sensitive pattern" },
  { key: "low", label: "Low", variant: "ok", icon: "info-circle", desc: "Low-confidence signal" },
];

export async function render(ctx) {
  const { h, icon, c, navigate } = ctx;

  // Live DLP overview from /admin/security/overview.
  const res = await ctx.api.adminSecurity();
  const sec = normalizeSecurity(res.data);
  const source = res.source;

  const risky = num(sec.risky_messages);
  const compliant = num(sec.compliant_messages);
  const total = risky + compliant;
  const sev = sec.severity_counts || { high: 0, medium: 0, low: 0 };
  const sevMax = Math.max(1, sev.high || 0, sev.medium || 0, sev.low || 0);
  const dlp = Array.isArray(sec.dlp_fields) ? sec.dlp_fields : [];
  const dlpMax = Math.max(1, ...dlp.map((f) => num(f.hits)));

  const root = h("div.lt3-stack-6",
    c.viewHeader({
      eyebrow: "Administration",
      title: "Security",
      sub: "Sensitive-data signals and DLP",
      actions: [
        c.sourceBadge(source),
        h("button.lt3-btn.lt3-btn--ghost", {
          on: { click: () => ctx.toast("DLP rule editing is not available in this build; rules are enforced by the runtime defaults.", "warn") },
        }, icon("adjustments"), "Tune rules"),
        h("button.lt3-btn.lt3-btn--primary", {
          on: { click: () => navigate("admin/audit") },
        }, icon("history"), "View audit log"),
      ],
    }),

    c.banner("DLP scanning runs entirely on this machine. Messages are inspected locally before they ever reach a model — no content leaves your computer.", "info", "shield-lock"),

    // ── Headline stats ──────────────────────────────────────────────────────
    h("section",
      c.sectionHead("Last scan window", c.sourceBadge(source)),
      h("div.lt3-statrow",
        c.stat({ label: "Risk rate", value: pct(sec.risk_rate), icon: "gauge", delta: `${c.fmtNum(risky)} of ${c.fmtNum(total)}` }),
        c.stat({ label: "Risky messages", value: c.fmtNum(risky), icon: "alert-triangle" }),
        c.stat({ label: "Compliant messages", value: c.fmtNum(compliant), icon: "circle-check" }),
        c.stat({ label: "High severity", value: c.fmtNum(sev.high || 0), icon: "shield-exclamation", delta: (sev.high || 0) === 0 ? "All clear" : "Needs review", deltaDir: (sev.high || 0) === 0 ? "up" : "down" }),
      ),
    ),

    h("div.lt3-grid-2",
      buildSeverityPanel(ctx, sev, sevMax, source),
      buildDlpPanel(ctx, dlp, dlpMax, total, source),
    ),
  );

  return root;
}

/* ── Severity breakdown ──────────────────────────────────────────────────── */
function buildSeverityPanel(ctx, sev, sevMax, source) {
  const { h, icon, c } = ctx;
  const totalSignals = (sev.high || 0) + (sev.medium || 0) + (sev.low || 0);

  const rows = SEVERITY.map((s) => {
    const count = num(sev[s.key]);
    return h("div.lt3-stack-2",
      h("div.lt3-row", { style: { "justify-content": "space-between", "align-items": "center" } },
        h("div.lt3-row-2",
          h("span.lt3-stat__label", { style: { margin: "0" } }, icon(s.icon), s.label),
          h("span.lt3-faint", { style: { "font-size": "var(--lt3-text-2xs)" } }, s.desc),
        ),
        h("span.lt3-mono", { style: { "font-size": "var(--lt3-text-sm)", "font-weight": "var(--lt3-weight-semi)" } }, c.fmtNum(count)),
      ),
      c.meter(count / sevMax, s.variant),
    );
  });

  return c.panel({
    eyebrow: "Signals",
    title: "Severity breakdown",
    sub: totalSignals === 0
      ? "No sensitive-data signals in this window — the workspace is clean."
      : `${c.fmtNum(totalSignals)} ${totalSignals === 1 ? "signal" : "signals"} flagged, normalized to the busiest tier.`,
    children: h("div.lt3-stack-4", rows),
  });
}

/* ── DLP field hits ──────────────────────────────────────────────────────── */
function buildDlpPanel(ctx, dlp, dlpMax, total, source) {
  const { h, c } = ctx;

  const columns = [
    {
      key: "field",
      label: "Field",
      render: (row) => h("span.lt3-mono", { style: { "font-size": "var(--lt3-text-sm)" } }, String(row.field || "—")),
    },
    {
      key: "hits",
      label: "Hits",
      width: "72px",
      render: (row) => h("span.lt3-weight-semi", c.fmtNum(num(row.hits))),
    },
    {
      key: "share",
      label: "Relative",
      render: (row) => h("div", { style: { "min-width": "120px" } }, c.meter(num(row.hits) / dlpMax, "vector")),
    },
  ];

  const totalHits = dlp.reduce((sum, f) => sum + num(f.hits), 0);

  return c.panel({
    eyebrow: "Patterns",
    title: "DLP field hits",
    sub: "Sensitive field patterns matched during local inspection.",
    actions: c.sourceBadge(source),
    children: h("div.lt3-stack-3",
      dlp.length
        ? c.table(columns, dlp)
        : c.emptyState({ icon: "shield-check", title: "No field hits", body: "No sensitive field patterns matched in this window." }),
      dlp.length
        ? h("div.lt3-row-2", { style: { "justify-content": "space-between" } },
            h("span.lt3-faint", { style: { "font-size": "var(--lt3-text-2xs)" } }, `${c.fmtNum(totalHits)} total ${totalHits === 1 ? "hit" : "hits"} across ${c.fmtNum(dlp.length)} ${dlp.length === 1 ? "pattern" : "patterns"}`),
            c.sourceBadge(source),
          )
        : null,
    ),
  });
}

/* ── helpers ─────────────────────────────────────────────────────────────── */
// Normalize the live /admin/security/overview payload into one
// shape: { risk_rate, risky_messages, compliant_messages, severity_counts, dlp_fields }.
function normalizeSecurity(data) {
  const d = data || {};
  const cards = d.cards || {};
  const risky = num(d.risky_messages != null ? d.risky_messages : cards.risky_chats);
  const riskRate = num(d.risk_rate);
  let compliant = num(d.compliant_messages);
  if (d.compliant_messages == null && riskRate > 0) {
    const total = Math.round(risky / (riskRate / 100));
    compliant = Math.max(0, total - risky);
  }
  let dlp = Array.isArray(d.dlp_fields) ? d.dlp_fields : null;
  if (!dlp && d.field_counts && typeof d.field_counts === "object") {
    dlp = Object.entries(d.field_counts).map(([field, hits]) => ({ field, hits: num(hits) }));
  }
  return {
    risk_rate: riskRate,
    risky_messages: risky,
    compliant_messages: compliant,
    severity_counts: d.severity_counts || { high: 0, medium: 0, low: 0 },
    dlp_fields: dlp || [],
  };
}

function num(v) {
  const n = Number(v);
  return Number.isFinite(n) ? n : 0;
}

function pct(v) {
  const n = Number(v);
  if (!Number.isFinite(n)) return "—";
  // Trim a trailing .0 so "1.2%" stays clean and "0%" doesn't read "0.0%".
  return `${(Math.round(n * 10) / 10).toString().replace(/\.0$/, "")}%`;
}
