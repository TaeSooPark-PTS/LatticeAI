/* ============================================================================
 * View: Memory — the long-term memory platform + Memory Manager.
 * Reads /api/memory/manager (usage / sources / health / size / type) and offers
 * recall, inspect, compact, and rebuild. Every number comes from a real store;
 * tiers with no backing report unavailable. (Destructive prune/clear are
 * available over the API but intentionally not surfaced as one-click UI here.)
 * ========================================================================== */

const TIER_ICON = {
  workspace: "building-warehouse", project: "folders", agent: "robot",
  conversation: "messages", graph: "chart-dots-3", vector: "grid-dots",
};

export async function render(ctx) {
  const { h, c } = ctx;

  const srcBadge = h("span", c.sourceBadge("pending"));
  const statHost = h("div.lt3-statrow", c.loading({ lines: 1 }));
  const sourcesHost = h("div", c.loading({ lines: 3, block: true }));
  const recallHost = h("div");
  const inspectHost = h("div", h("p.lt3-faint", { style: { margin: 0 } }, "Pick a tier to inspect its contents."));

  const recallInput = h("input.lt3-input", { type: "text", placeholder: "Recall from workspace + graph memory…", "aria-label": "Recall memory" });

  const root = h("div.lt3-stack-6",
    c.viewHeader({
      eyebrow: "Retrieval",
      title: "Memory",
      sub: "Long-term memory unified across workspace, project, agent, conversation, graph, and vector tiers — recall, inspect, and maintain it without leaving /app.",
      actions: [srcBadge],
    }),
    statHost,
    c.panel({
      title: "Recall", sub: "Searches your workspace and knowledge-graph memory.",
      children: h("div.lt3-stack-3",
        h("div.lt3-row-2",
          recallInput,
          h("button.lt3-btn.lt3-btn--primary", { on: { click: doRecall } }, c.icon("search"), "Recall"),
        ),
        recallHost,
      ),
    }),
    h("section",
      c.sectionHead("Memory sources", h("div.lt3-row-2",
        h("button.lt3-btn.lt3-btn--subtle.lt3-btn--sm", { on: { click: () => act("compact") } }, c.icon("arrows-minimize"), "Compact"),
        h("button.lt3-btn.lt3-btn--subtle.lt3-btn--sm", { on: { click: () => act("rebuild") } }, c.icon("refresh"), "Rebuild vectors"),
      )),
      sourcesHost,
    ),
    c.panel({ title: "Inspect tier", children: h("div.lt3-stack-3", tierTabs(ctx, inspectHost), inspectHost) }),
  );

  hydrate();
  return root;

  async function hydrate() {
    const res = await ctx.api.memoryManager();
    const data = res.data || {};
    const sources = Array.isArray(data.sources) ? data.sources : [];
    srcBadge.replaceChildren(c.sourceBadge(res.source));

    if (!sources.length) {
      statHost.replaceChildren(c.stat({ label: "Memory", value: "—", icon: "brain" }));
      sourcesHost.replaceChildren(c.emptyState({ icon: "database-off", title: "Memory unavailable", body: "Start the backend to read the memory platform." }));
      return;
    }
    const usage = data.usage || {};
    statHost.replaceChildren(
      c.stat({ label: "Total items", value: c.fmtNum(usage.total_items), icon: "stack-2" }),
      c.stat({ label: "On disk", value: fmtBytes(usage.total_bytes), icon: "database" }),
      c.stat({ label: "Tiers", value: c.fmtNum((data.tiers || []).length), icon: "layers" }),
      c.stat({ label: "Health", value: data.health || "—", icon: "heartbeat" }),
    );
    sourcesHost.replaceChildren(c.table(
      [
        { key: "label", label: "Source", render: (r) => h("div.lt3-row-2", c.icon(TIER_ICON[r.type] || "circle"), h("b", r.label)) },
        { key: "type", label: "Type", width: "1%", render: (r) => c.pill(r.type) },
        { key: "count", label: "Items", width: "1%", render: (r) => h("span", { style: { "font-variant-numeric": "tabular-nums" } }, r.count == null ? "—" : c.fmtNum(r.count)) },
        { key: "size", label: "Size", width: "1%", render: (r) => h("span.lt3-faint", r.size_bytes ? fmtBytes(r.size_bytes) : "—") },
        { key: "health", label: "Health", width: "1%", render: (r) => c.statePill(r.health === "ok" ? "ready" : r.health === "unavailable" ? "failed" : "idle") },
        { key: "detail", label: "Detail", render: (r) => h("span.lt3-muted", r.detail || "") },
      ],
      sources,
    ));
  }

  async function doRecall() {
    const q = recallInput.value.trim();
    recallHost.replaceChildren(c.loading({ lines: 2 }));
    const res = await ctx.api.memoryRecall(q, 25);
    const items = (res.data && res.data.results) || [];
    if (!res.ok) { recallHost.replaceChildren(c.banner("Recall is unavailable — start the backend.", "warn")); return; }
    if (!items.length) { recallHost.replaceChildren(c.emptyState({ icon: "search-off", title: "No matches", body: "Nothing recalled for that query yet." })); return; }
    recallHost.replaceChildren(h("div.lt3-stack-2", items.map((it) => c.card(
      h("div.lt3-stack-2",
        h("div.lt3-row", { style: { "justify-content": "space-between" } }, h("b", it.title || "memory"), c.pill(it.source, it.source === "graph" ? "info" : "")),
        h("p.lt3-muted", { style: { margin: 0, "font-size": "var(--lt3-text-sm)" } }, it.snippet || ""),
      ), { flat: true },
    ))));
  }

  async function act(kind) {
    const fn = kind === "compact" ? ctx.api.memoryCompact() : ctx.api.memoryRebuild("vector");
    const res = await fn;
    if (res && (res.ok || res.data)) {
      const d = res.data || res;
      const msg = kind === "compact" ? `Compacted ${d.compacted ?? 0} duplicate memories` : `Rebuild: ${d.status || "ok"}`;
      ctx.toast(msg, d.status === "error" || d.status === "unavailable" ? "err" : "ok");
    } else {
      ctx.toast(`${kind} unavailable`, "err");
    }
    hydrate();
  }

  function tierTabs(ctx2, host) {
    const tiers = ["workspace", "project", "agent", "conversation", "graph", "vector"];
    let active = null;
    return c.segmented(tiers.map((t) => ({ key: t, label: t })), active, async (key) => {
      host.replaceChildren(c.loading({ lines: 2 }));
      const res = await ctx2.api.memoryInspect(key, 50);
      const d = res.data || {};
      if (key === "graph") { host.replaceChildren(jsonBlock(ctx2, d.stats || {}, d.available)); return; }
      if (key === "vector") { host.replaceChildren(jsonBlock(ctx2, d.index || {}, d.available)); return; }
      const items = d.items || [];
      if (!items.length) { host.replaceChildren(c.emptyState({ icon: "inbox", title: `No ${key} memory`, body: "This tier has no items yet." })); return; }
      host.replaceChildren(h("div.lt3-stack-2", items.slice(0, 50).map((m) => c.card(
        h("div.lt3-stack-2",
          h("div.lt3-row-2", m.kind ? c.pill(m.kind) : null, h("b", { style: { "font-size": "var(--lt3-text-sm)" } }, m.title || m.id || "item")),
          h("p.lt3-muted", { style: { margin: 0, "font-size": "var(--lt3-text-sm)" } }, m.content || (m.messages != null ? `${m.messages} messages` : "")),
        ), { flat: true },
      ))));
    });
  }

  function jsonBlock(ctx2, obj, available) {
    if (!available) return c.emptyState({ icon: "database-off", title: "Unavailable", body: "This tier is disabled or empty." });
    return h("pre", { style: { margin: 0, "white-space": "pre-wrap", "font-family": "var(--lt3-font-mono)", "font-size": "var(--lt3-text-xs)", color: "var(--muted)" } }, JSON.stringify(obj, null, 2));
  }
}

function fmtBytes(n) {
  const v = Number(n) || 0;
  if (v <= 0) return "—";
  if (v >= 1 << 20) return (v / (1 << 20)).toFixed(1) + " MB";
  if (v >= 1 << 10) return (v / (1 << 10)).toFixed(1) + " KB";
  return v + " B";
}
