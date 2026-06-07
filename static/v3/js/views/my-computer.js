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

export async function render(ctx) {
  const { h, icon, api, c } = ctx;

  const state = { memoryOn: false, activities: [], memSource: "pending" };

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

  // Reflect real local-memory state (enabled + recorded activity) from the backend.
  async function loadMemory() {
    const res = await api.computerMemory();
    const cfg = (res && res.ok && res.data) ? res.data : null;
    state.memSource = cfg ? "live" : "placeholder";
    state.memoryOn = !!(cfg && cfg.enabled);
    state.activities = (cfg && Array.isArray(cfg.activities)) ? cfg.activities.slice().reverse() : [];
    if (state._refreshMemory) state._refreshMemory();
  }

  load();
  loadMemory();
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

/* ── Local memory panel (wired to /workspace/computer-memory) ─────────────── */
function buildMemoryPanel(ctx, state) {
  const { h, icon, c } = ctx;
  const notify = ctx.toast || c.toast;

  const activityHost = h("div", renderActivity(ctx, state));
  const input = h("input", {
    type: "checkbox",
    "aria-label": "Enable local computer memory",
    checked: state.memoryOn,
    on: {
      change: async (e) => {
        const want = e.target.checked;
        input.disabled = true;
        const res = await ctx.api.setComputerMemory(want);
        input.disabled = false;
        if (res && res.ok) {
          state.memoryOn = want;
          state.memSource = "live";
          const cfg = res.data || {};
          state.activities = Array.isArray(cfg.activities) ? cfg.activities.slice().reverse() : state.activities;
          refresh();
          notify(
            want
              ? "Local memory enabled — context persists on this computer (~/.ltcai)."
              : "Local memory disabled. Nothing will be persisted on this computer.",
            want ? "ok" : "info",
          );
        } else {
          // Revert the toggle; report the real reason (e.g. 403 consent, no backend).
          e.target.checked = state.memoryOn;
          const detail = (res && res.data && (res.data.detail || res.data.error)) || "the runtime is unavailable";
          notify(`Could not change local memory — ${detail}.`, "warn");
        }
      },
    },
  });

  // Built from the frozen .lt3-switch markup (input + span); no shared file touched.
  const sw = h("label.lt3-switch", { title: "Enable local computer memory" }, input, h("span"));

  function refresh() {
    input.checked = state.memoryOn;
    activityHost.replaceChildren(renderActivity(ctx, state));
  }
  state._refreshMemory = refresh;

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
        h("div.lt3-row", { style: { "justify-content": "space-between", "align-items": "center", "margin-bottom": "var(--lt3-space-2)" } },
          h("div.lt3-eyebrow", "Recent local activity"),
          h("span", { "data-mem-src": "1" }, c.sourceBadge(state.memSource)),
        ),
        activityHost,
      ),
    ),
  });
}

function renderActivity({ h, icon, c }, state) {
  if (!state.memoryOn) {
    return c.emptyState({
      icon: "database-off",
      title: "Memory is off",
      body: "Enable local memory to let the assistant retain context on this computer.",
    });
  }
  const items = Array.isArray(state.activities) ? state.activities : [];
  if (!items.length) {
    return c.emptyState({
      icon: "history-off",
      title: "No activity recorded yet",
      body: "Once memory is on, on-device actions the assistant takes will be logged here.",
    });
  }
  return h("div.lt3-list",
    items.slice(0, 8).map((a) => h("div.lt3-list__item",
      icon(a.icon || "activity"),
      h("div.lt3-list__body",
        h("div.lt3-list__title", a.title || a.action || a.kind || "Activity"),
        h("div.lt3-list__meta", a.meta || a.detail || a.timestamp || ""),
      ),
      c.statePill(a.state || "ok"),
    )),
  );
}

/* ── helpers ─────────────────────────────────────────────────────────────── */
function roundPct(n) { return Math.round(Number(n) * 10) / 10; }
function fmtGb(n) {
  const v = Number(n);
  return Number.isFinite(v) ? (Math.round(v * 10) / 10).toString() : "—";
}
