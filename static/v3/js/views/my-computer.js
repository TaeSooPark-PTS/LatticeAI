/* ============================================================================
 * View: My Computer — local hardware, memory, and runtime.
 * Reinforces the local-first promise: every gauge, model, and byte of memory
 * lives on this machine. Live-reads /local/sysinfo + /models and degrades to
 * clearly-badged sample data when those endpoints aren't available yet.
 * ========================================================================== */

const GAUGES = [
  { key: "cpu_pct", label: "CPU", icon: "cpu", variant: "graph", sub: () => "Compute cores" },
  { key: "ram_pct", label: "RAM", icon: "device-desktop-analytics", variant: "vector", sub: () => "Unified memory" },
  { key: "gpu_mem_pct", label: "GPU (MLX)", icon: "brand-apple", variant: "hybrid", sub: (d) => `${fmtGb(d.gpu_mem_gb)} GB in use` },
];

const RECENT_ACTIVITY = [
  { icon: "search", title: "Hybrid search ran locally", meta: "MLX runtime · on-device", state: "ok" },
  { icon: "vector-bezier-2", title: "Vector index refreshed", meta: "48k vectors · ~/.ltcai", state: "ok" },
  { icon: "chart-dots-3", title: "Knowledge graph rebuilt", meta: "1.2k entities · on-device", state: "ok" },
];

export async function render(ctx) {
  const { h, icon, api, c } = ctx;

  const state = { memoryOn: false };

  // Hydrated after the async reads land.
  const srcSlot = h("span", c.sourceBadge("pending"));
  const gaugeHost = h("div.lt3-grid-3", c.loading({ lines: 0, block: true }));
  const runtimeHost = h("div", c.loading({ lines: 4 }));

  const root = h("div.lt3-stack-6",
    c.viewHeader({
      eyebrow: "Compute",
      title: "My Computer",
      sub: "The local hardware and MLX runtime powering this workspace. Inference and indexing run here — on Apple Silicon — never on an external server.",
      actions: [
        srcSlot,
        h("button.lt3-btn.lt3-btn--ghost", { on: { click: () => load() } }, icon("refresh"), "Refresh"),
      ],
    }),

    c.banner("All inference and indexing happen on this computer. Nothing you index, ask, or remember is sent to external servers.", "info", "shield-lock"),

    h("section",
      c.sectionHead("Live utilization"),
      gaugeHost,
    ),

    h("div.lt3-grid-2",
      c.panel({
        eyebrow: "Runtime",
        title: "Local runtime",
        sub: "Where this workspace runs and where it keeps its data.",
        children: runtimeHost,
      }),
      buildMemoryPanel(ctx, state),
    ),
  );

  async function load() {
    gaugeHost.replaceChildren(c.loading({ lines: 0, block: true }));
    runtimeHost.replaceChildren(c.loading({ lines: 4 }));

    const [sys, models] = await Promise.all([api.sysinfo(), api.models()]);

    srcSlot.replaceChildren(c.sourceBadge(sys.source));
    gaugeHost.replaceChildren(buildGauges(ctx, sys));
    runtimeHost.replaceChildren(buildRuntime(ctx, sys, models));
  }

  load();
  return root;
}

/* ── Gauges ──────────────────────────────────────────────────────────────── */
function buildGauges({ h, icon, c }, sys) {
  const data = (sys && sys.data) || {};
  return h("div.lt3-grid-3",
    GAUGES.map((g) => {
      const raw = Number(data[g.key]);
      const pct = Number.isFinite(raw) ? raw : null;
      return c.card(
        h("div.lt3-stack-3",
          h("div.lt3-row", { style: { "justify-content": "space-between" } },
            h("div.lt3-stat__label", icon(g.icon), g.label),
            c.statePill(pct == null ? "idle" : pct >= 90 ? "warn" : "active"),
          ),
          h("div.lt3-stat__value", { style: { "font-size": "var(--lt3-text-3xl)" } },
            pct == null ? "—" : `${roundPct(pct)}%`),
          c.meter(pct == null ? 0 : pct / 100, g.variant),
          h("div.lt3-faint", { style: { "font-size": "var(--lt3-text-2xs)" } }, g.sub(data)),
        ),
      );
    }),
  );
}

/* ── Runtime key/value panel ─────────────────────────────────────────────── */
function buildRuntime({ h, icon, c }, sys, models) {
  const md = (models && models.data) || {};
  const current =
    md.current ||
    (md.catalog || []).find((m) => m.state === "loaded")?.id ||
    "mlx-community/local-model-4bit";

  const rows = [
    { k: "Platform", v: "Apple Silicon · MLX", icon: "brand-apple" },
    { k: "Loaded model", v: current, mono: true, icon: "cpu" },
    { k: "Local storage", v: "~/.ltcai", mono: true, icon: "folder" },
    { k: "Memory model", v: "Unified memory (CPU + GPU shared)", icon: "stack-2" },
    { k: "Network", v: "Local-only — no external inference", icon: "wifi-off" },
  ];

  return h("div",
    h("dl.lt3-keyval",
      rows.flatMap((r) => [
        h("dt", h("span.lt3-row-2", icon(r.icon), r.k)),
        h("dd", r.mono ? h("span.lt3-mono", r.v) : r.v),
      ]),
    ),
    h("div.lt3-row-2", { style: { "margin-top": "var(--lt3-space-4)" } },
      c.sourceBadge((models && models.source) || (sys && sys.source)),
      h("span.lt3-faint", { style: { "font-size": "var(--lt3-text-2xs)" } }, "Derived from local runtime"),
    ),
  );
}

/* ── Local memory panel ──────────────────────────────────────────────────── */
function buildMemoryPanel(ctx, state) {
  const { h, icon, c } = ctx;
  const notify = ctx.toast || c.toast;

  const activityHost = h("div", renderActivity(ctx, state.memoryOn));

  // Built from the frozen .lt3-switch markup (input + span); no shared file touched.
  const sw = h("label.lt3-switch", { title: "Enable local computer memory" },
    h("input", {
      type: "checkbox",
      "aria-label": "Enable local computer memory",
      checked: state.memoryOn,
      on: {
        change: (e) => {
          state.memoryOn = e.target.checked;
          activityHost.replaceChildren(renderActivity(ctx, state.memoryOn));
          notify(
            state.memoryOn
              ? "Local memory enabled — the assistant can persist context on this computer. Backend persistence integration is pending."
              : "Local memory disabled. Nothing will be persisted on this computer.",
            state.memoryOn ? "ok" : "info",
          );
        },
      },
    }),
    h("span"),
  );

  return c.panel({
    eyebrow: "On-device",
    title: "Local memory",
    sub: "Let the assistant remember context across sessions — stored only on this computer, never uploaded.",
    children: h("div.lt3-stack-4",
      h("div.lt3-row", { style: { "justify-content": "space-between", "align-items": "flex-start" } },
        h("div.lt3-stack-2", { style: { "max-width": "40ch" } },
          h("div", { style: { "font-weight": "var(--lt3-weight-semi)", "font-size": "var(--lt3-text-sm)" } },
            "Enable local computer memory"),
          h("div.lt3-faint", { style: { "font-size": "var(--lt3-text-2xs)" } },
            "Persists to ~/.ltcai. Off by default."),
        ),
        sw,
      ),
      h("div",
        h("div.lt3-eyebrow", { style: { "margin-bottom": "var(--lt3-space-2)" } }, "Recent local activity"),
        activityHost,
      ),
    ),
  });
}

function renderActivity({ h, icon, c }, on) {
  if (!on) {
    return c.emptyState({
      icon: "database-off",
      title: "Memory is off",
      body: "Enable local memory to let the assistant retain context on this computer.",
    });
  }
  return h("div.lt3-stack-2",
    h("div.lt3-list",
      RECENT_ACTIVITY.map((a) => h("div.lt3-list__item",
        icon(a.icon),
        h("div.lt3-list__body",
          h("div.lt3-list__title", a.title),
          h("div.lt3-list__meta", a.meta),
        ),
        c.statePill(a.state),
      )),
    ),
    h("div.lt3-row-2", c.sourceBadge("placeholder"),
      h("span.lt3-faint", { style: { "font-size": "var(--lt3-text-2xs)" } }, "Sample activity — local persistence pending")),
  );
}

/* ── helpers ─────────────────────────────────────────────────────────────── */
function roundPct(n) { return Math.round(Number(n) * 10) / 10; }
function fmtGb(n) {
  const v = Number(n);
  return Number.isFinite(v) ? (Math.round(v * 10) / 10).toString() : "—";
}
