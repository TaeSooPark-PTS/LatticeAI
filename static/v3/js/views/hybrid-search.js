/* ============================================================================
 * View: Hybrid Search — the fused-retrieval surface (headline capability).
 * Runs api.hybridSearch(query, {mode}) and shows, per result, how the vector,
 * lexical and graph signals combined into the fused score. Integration-ready:
 * degrades to clearly-badged sample data when /api/search/hybrid isn't live.
 * ========================================================================== */

const MODES = [
  { key: "hybrid", label: "Hybrid" },
  { key: "vector", label: "Vector" },
  { key: "graph", label: "Graph" },
  { key: "keyword", label: "Keyword" },
];

const EXAMPLES = ["retrieval design", "vector index config", "rank fusion", "graph adjacency"];

const SIGNALS = [
  { key: "vector", label: "Vector", variant: "vector", icon: "grid-dots", desc: "Dense vector similarity from the local embedding index." },
  { key: "keyword", label: "Keyword", variant: "", icon: "abc", desc: "Lexical overlap — exact terms and phrases." },
  { key: "graph", label: "Graph", variant: "graph", icon: "chart-dots-3", desc: "Structural proximity in the knowledge graph." },
];

export async function render(ctx) {
  const { h, icon, api, store, c } = ctx;

  const state = { query: "", mode: "hybrid", source: "pending" };

  // Fusion alpha from the live/sample index status.
  let alpha = 0.5;
  store.get().indexStatus
    ? (alpha = store.get().indexStatus?.pipelines?.hybrid?.alpha ?? 0.5)
    : api.indexStatus().then((r) => {
        store.setIndexStatus(r.data);
        alpha = r.data?.pipelines?.hybrid?.alpha ?? 0.5;
        alphaPill.replaceChildren(c.pill(`α ${alpha}`, "info"));
      });

  const input = h("input", {
    type: "text", placeholder: "Search your workspace…", "aria-label": "Search query",
    on: { keydown: (e) => { if (e.key === "Enter") run(input.value); } },
  });
  const alphaPill = h("span", c.pill(`α ${alpha}`, "info"));
  const srcSlot = h("span", c.sourceBadge("pending"));
  const resultsHost = h("div.lt3-stack-6", introBlock());

  const seg = h("div.lt3-fusion", { role: "tablist", "aria-label": "Fusion mode" },
    MODES.map((m) => h("button", {
      type: "button", role: "tab",
      dataset: { active: String(m.key === state.mode) },
      "aria-selected": String(m.key === state.mode),
      on: { click: () => { state.mode = m.key; syncSeg(); if (state.query) run(state.query); } },
    }, m.label)),
  );
  function syncSeg() {
    seg.querySelectorAll("button").forEach((b, i) => {
      const on = MODES[i].key === state.mode;
      b.dataset.active = String(on); b.setAttribute("aria-selected", String(on));
    });
  }

  const root = h("div.lt3-stack-6",
    c.viewHeader({
      eyebrow: "Retrieval · fusion",
      title: "Hybrid Search",
      sub: "Fuse the knowledge graph's structure with the vector field's semantics — every match is scored across both, then reconciled into one ranked answer.",
      actions: [srcSlot],
    }),
    h("section.lt3-search-hero",
      h("div.lt3-row-2", { style: { "align-items": "stretch" } },
        h("div.lt3-search", { style: { flex: "1", height: "46px" } }, icon("search"), input),
        h("button.lt3-btn.lt3-btn--primary.lt3-btn--lg", { on: { click: () => run(input.value) } }, icon("arrows-join"), "Search"),
      ),
      h("div.lt3-row", { style: { "justify-content": "space-between", "flex-wrap": "wrap", gap: "var(--lt3-space-3)" } },
        seg,
        h("div.lt3-row-2", h("span.lt3-faint", { style: { "font-size": "var(--lt3-text-2xs)" } }, "Fusion weight"), alphaPill),
      ),
      h("div.lt3-cluster",
        h("span.lt3-faint", { style: { "font-size": "var(--lt3-text-2xs)" } }, "Try"),
        EXAMPLES.map((q) => h("button.lt3-chip", { type: "button", on: { click: () => run(q) } }, icon("search"), q)),
      ),
    ),
    resultsHost,
  );

  /* ── search flow ───────────────────────────────────────────────────────── */
  async function run(rawQuery) {
    const q = String(rawQuery || "").trim();
    if (!q) { input.focus(); return; }
    state.query = q;
    if (input.value !== q) input.value = q;
    resultsHost.replaceChildren(
      c.sectionHead(`Results for “${q}”`, srcSlot.cloneNode(true)),
      c.loading({ lines: 4 }),
    );
    const res = await api.hybridSearch(q, { mode: state.mode });
    state.source = res.source;
    srcSlot.replaceChildren(c.sourceBadge(res.source));
    renderResults(res);
  }

  function renderResults(res) {
    if (!res.ok) {
      resultsHost.replaceChildren(c.errorState(res.error || "Search failed", () => run(state.query)));
      return;
    }
    const rows = Array.isArray(res.data) ? res.data : [];
    if (!rows.length) {
      resultsHost.replaceChildren(
        c.sectionHead(`No results for “${state.query}”`, c.sourceBadge(res.source)),
        c.emptyState({ icon: "search-off", title: "Nothing matched", body: "Try broader terms, or switch the fusion mode above." }),
      );
      return;
    }
    resultsHost.replaceChildren(
      c.sectionHead(
        `${rows.length} ${rows.length === 1 ? "result" : "results"}`,
        c.pill(MODES.find((m) => m.key === state.mode).label, "info"),
        c.sourceBadge(res.source),
      ),
      h("div.lt3-stack-3", rows.map((r) => resultCard(r))),
    );
  }

  function resultCard(r) {
    const score = typeof r.score === "number" ? r.score : (0.5 * (r.vector || 0) + 0.2 * (r.lexical || 0) + 0.3 * (r.graph || 0));
    return h("article.lt3-result",
      h("div.lt3-result__top",
        h("div.lt3-result__title", { style: { flex: "1", "min-width": "0" } }, String(r.title || "Untitled")),
        c.pill(`${(score).toFixed(2)} score`, "info", { dot: true }),
      ),
      h("div.lt3-faint.lt3-mono", { style: { "font-size": "var(--lt3-text-2xs)" } }, String(r.path || "")),
      r.snippet && h("p.lt3-result__snippet", String(r.snippet)),
      h("div.lt3-result__scores",
        scoreBlock("Vector", r.vector, "vector"),
        scoreBlock("Keyword", r.lexical, ""),
        scoreBlock("Graph", r.graph, "graph"),
      ),
    );
  }

  function scoreBlock(label, value, variant) {
    const v = Number(value) || 0;
    return h("div.lt3-score",
      h("div.lt3-score__row", h("span", label), h("b", v.toFixed(2))),
      c.meter(v, variant),
    );
  }

  /* ── pre-search intro ──────────────────────────────────────────────────── */
  function introBlock() {
    return h("div.lt3-stack-6",
      c.emptyState({
        icon: "arrows-join",
        title: "Search across structure and meaning",
        body: "Enter a query above. Every result is scored on three independent signals, then fused into one ranking.",
      }),
      h("section",
        c.sectionHead("How fusion scores a match"),
        h("div.lt3-grid-3",
          SIGNALS.map((s) => signalCard(s)),
        ),
      ),
    );
  }

  function signalCard(s) {
    const tint = s.variant === "vector" ? "var(--lt3-pillar-vector)"
      : s.variant === "graph" ? "var(--lt3-pillar-graph)"
      : "var(--accent-3)";
    return c.card(
      h("div.lt3-stack-3",
        h("div.lt3-row-2",
          h("span.lt3-result__title", { style: { display: "grid", "place-items": "center", width: "32px", height: "32px", "border-radius": "var(--lt3-radius-sm)", background: `color-mix(in srgb, ${tint} 16%, transparent)`, color: tint } }, icon(s.icon)),
          h("b", s.label),
        ),
        h("p.lt3-muted", { style: { "font-size": "var(--lt3-text-sm)" } }, s.desc),
        c.meter(s.key === "graph" ? 0.85 : s.key === "vector" ? 0.7 : 0.55, s.variant),
      ),
      { flat: true },
    );
  }

  return root;
}
