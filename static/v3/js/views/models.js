/* ============================================================================
 * View: Models — the local MLX runtime.
 * Lists the language models available to the runtime, highlights the loaded
 * one, and shows the local embedding signal that backs the Vector Index.
 * Falls back to clearly-badged sample data when the runtime endpoint isn't
 * available.
 *
 * View contract (shared by all views):
 *   export async function render(ctx) -> single DOM node
 *   ctx = { h, icon, api, store, c, route, params, navigate, toast }
 * ========================================================================== */

const PENDING = "Model loading runs on the local MLX runtime — pending backend.";

export async function render(ctx) {
  const { h, icon, api, c } = ctx;

  const srcSlot = h("span", c.sourceBadge("pending"));
  const activeHost = h("div", c.loading({ lines: 2, block: true }));
  const statHost = h("div.lt3-statrow", c.loading({ lines: 1 }));
  const embedHost = h("div", c.loading({ lines: 2 }));
  const tableHost = h("div", c.loading({ lines: 4 }));

  const root = h("div.lt3-stack-6",
    c.viewHeader({
      eyebrow: "Compute",
      title: "Models",
      sub: "Local and OpenAI-compatible runtime choices. Local models keep generation on this machine; cloud-compatible providers are shown only when configured.",
      actions: [
        srcSlot,
        h("button.lt3-btn.lt3-btn--ghost", { on: { click: () => load() } }, icon("refresh"), "Refresh"),
      ],
    }),
    activeHost,
    statHost,
    c.panel({
      eyebrow: "Retrieval",
      title: "Embedding models",
      sub: "The current default vector signal is lattice-local-hash-v1 fallback embeddings; future local providers can replace it behind the same index.",
      children: embedHost,
    }),
    c.panel({
      head: h("div.lt3-row", { style: { "justify-content": "space-between", "align-items": "center", width: "100%" } },
        h("div", h("div.lt3-eyebrow", "Runtime"), h("h3.lt3-panel__title", "Model catalog")),
        h("span", { id: "models-cat-src" }, c.sourceBadge("pending")),
      ),
      children: tableHost,
    }),
  );

  async function load() {
    activeHost.replaceChildren(c.loading({ lines: 2, block: true }));
    tableHost.replaceChildren(c.loading({ lines: 4 }));
    embedHost.replaceChildren(c.loading({ lines: 2 }));

    const res = await api.models();
    const data = res.data || {};
    const catalog = Array.isArray(data.catalog) ? data.catalog : [];

    srcSlot.replaceChildren(c.sourceBadge(res.source));
    root.querySelector("#models-cat-src")?.replaceChildren(c.sourceBadge(res.source));

    if (!catalog.length) {
      activeHost.replaceChildren(
        c.emptyState({ icon: "cpu-off", title: "No models on this machine", body: "Pull an MLX model into the local runtime to get started." }),
      );
      statHost.replaceChildren();
      embedHost.replaceChildren(c.emptyState({ icon: "grid-dots", title: "Fallback embeddings active", body: "lattice-local-hash-v1 can build deterministic local vectors without downloading a semantic embedding model." }));
      tableHost.replaceChildren(c.emptyState({ icon: "cpu-off", title: "Catalog is empty", body: "Connect the MLX runtime to list installed models." }));
      return;
    }

    const isEmbedding = (m) => String(m.family || "").toLowerCase() === "embedding";
    const language = catalog.filter((m) => !isEmbedding(m));
    const embeddings = catalog.filter(isEmbedding);
    const loaded = catalog.filter((m) => String(m.state).toLowerCase() === "loaded");
    const active = catalog.find((m) => m.id === data.current)
      || loaded.find((m) => !isEmbedding(m))
      || language[0]
      || catalog[0];

    renderActive(active);
    renderStats(catalog, embeddings, loaded);
    renderEmbeddings(embeddings);
    renderCatalog(language.length ? language : catalog);
  }

  function renderActive(m) {
    if (!m) { activeHost.replaceChildren(); return; }
    activeHost.replaceChildren(
      c.card(
        h("div.lt3-stack-3",
          h("div.lt3-row-2", { style: { "justify-content": "space-between", "align-items": "flex-start", "flex-wrap": "wrap", gap: "var(--lt3-space-3)" } },
            h("div.lt3-row-2", { style: { "align-items": "center", gap: "var(--lt3-space-3)" } },
              h("div.lt3-pillar__icon", { style: { background: "var(--lt3-pillar-hybrid-soft)", color: "var(--lt3-pillar-hybrid)" } }, icon("cpu")),
              h("div",
                h("div.lt3-eyebrow", "Active model"),
                h("div", { style: { "font-size": "var(--lt3-text-xl)", "font-weight": 800, "letter-spacing": "-0.01em" } }, m.name || m.id),
                h("div.lt3-faint.lt3-mono", { style: { "font-size": "var(--lt3-text-2xs)", "margin-top": "var(--lt3-space-1)" } }, m.id),
              ),
            ),
            h("div.lt3-cluster", { style: { "align-items": "center" } },
              m.recommended ? c.pill("Recommended", "info") : null,
              c.statePill("loaded"),
            ),
          ),
          h("div.lt3-cluster",
            specChip(ctx, "category", "Family", titleCase(m.family || "local")),
            specChip(ctx, "stack-2", "Params", m.params || "—"),
            specChip(ctx, "binary", "Quant", m.quant || "—"),
            specChip(ctx, "ruler-2", "Context", c.fmtNum(m.context) + " tok"),
          ),
        ),
        { attrs: { style: "border-color: color-mix(in srgb, var(--lt3-pillar-hybrid) 32%, var(--border)); background: var(--lt3-pillar-hybrid-soft)" } },
      ),
    );
  }

  function renderStats(catalog, embeddings, loaded) {
    const maxCtx = catalog.reduce((mx, m) => Math.max(mx, Number(m.context) || 0), 0);
    statHost.replaceChildren(
      c.stat({ label: "Loaded", value: c.fmtNum(loaded.length), icon: "player-play" }),
      c.stat({ label: "Available", value: c.fmtNum(catalog.length), icon: "stack-2" }),
      c.stat({ label: "Embedding models", value: c.fmtNum(embeddings.length), icon: "grid-dots" }),
      c.stat({ label: "Max context", value: c.fmtNum(maxCtx) + " tok", icon: "ruler-2" }),
    );
  }

  function renderEmbeddings(embeddings) {
    if (!embeddings.length) {
      embedHost.replaceChildren(
        c.emptyState({ icon: "grid-dots", title: "Fallback embeddings active", body: "lattice-local-hash-v1 builds deterministic local vectors until a semantic embedding provider is configured." }),
      );
      return;
    }
    embedHost.replaceChildren(
      h("div.lt3-grid-auto",
        embeddings.map((m) => c.card(
          h("div.lt3-stack-2",
            h("div.lt3-row-2", { style: { "justify-content": "space-between", "align-items": "center" } },
              h("div.lt3-row-2", { style: { "align-items": "center" } },
                h("span", { style: { color: "var(--lt3-pillar-vector)", display: "inline-flex" } }, icon("grid-dots")),
                h("b", { style: { "font-size": "var(--lt3-text-sm)" } }, m.name || m.id),
              ),
              c.statePill(m.state),
            ),
            h("div.lt3-faint.lt3-mono", { style: { "font-size": "var(--lt3-text-2xs)" } }, m.id),
            h("div.lt3-cluster",
              c.pill(`${m.params || "—"} params`, ""),
              c.pill(m.quant || "—", ""),
              c.pill(`${c.fmtNum(m.context)} ctx`, ""),
            ),
            h("div.lt3-faint", { style: { "font-size": "var(--lt3-text-2xs)" } }, "Powers the Vector Index → Hybrid Search."),
          ),
          { flat: true },
        )),
      ),
    );
  }

  function renderCatalog(rows) {
    const columns = [
      { key: "name", label: "Model", render: (m) => h("div.lt3-stack-2", { style: { gap: "2px" } },
          h("b", { style: { "font-size": "var(--lt3-text-sm)" } }, m.name || m.id),
          h("span.lt3-faint.lt3-mono", { style: { "font-size": "var(--lt3-text-2xs)" } }, m.id),
        ) },
      { key: "family", label: "Family", render: (m) => titleCase(m.family || "local") },
      { key: "params", label: "Params", render: (m) => h("span.lt3-mono", m.params || "—") },
      { key: "quant", label: "Quant", render: (m) => h("span.lt3-mono", m.quant || "—") },
      { key: "context", label: "Context", render: (m) => h("span.lt3-mono", c.fmtNum(m.context)) },
      { key: "state", label: "State", render: (m) => c.statePill(m.state) },
      { key: "action", label: "", width: "1%", render: (m) => actionButton(m) },
    ];
    tableHost.replaceChildren(
      c.table(columns, rows, {
        empty: c.emptyState({ icon: "cpu-off", title: "No language models", body: "Pull an MLX chat model into the runtime." }),
      }),
    );
  }

  function actionButton(m) {
    const loaded = String(m.state).toLowerCase() === "loaded";
    const label = loaded ? "Unload" : "Load";
    const ic = loaded ? "player-stop" : "player-play";
    return h("button.lt3-btn.lt3-btn--ghost.lt3-btn--sm",
      { "aria-label": `${label} ${m.name || m.id}`, on: { click: () => ctx.toast(PENDING, "info") } },
      icon(ic), label,
    );
  }

  load();
  return root;
}

/* ── helpers ─────────────────────────────────────────────────────────────── */
function specChip({ h, icon }, ic, label, value) {
  return h("span.lt3-pill",
    h("span", { style: { color: "var(--faint)", display: "inline-flex" } }, icon(ic)),
    h("span.lt3-faint", { style: { "font-size": "var(--lt3-text-2xs)" } }, label),
    h("b", { style: { "font-size": "var(--lt3-text-xs)" } }, value),
  );
}

function titleCase(s) {
  s = String(s || "");
  return s ? s.charAt(0).toUpperCase() + s.slice(1) : s;
}
