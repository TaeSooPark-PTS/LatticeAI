/* ============================================================================
 * View: Marketplace — agent templates, plugins, and skills.
 * Templates come from /marketplace/templates (offline local catalog incl. the
 * five named agent templates). Install / Clone / Export use the real backend.
 * Plugins and skills marketplace read live directories; unavailable is shown
 * honestly with no fabricated catalog.
 * ========================================================================== */

const KIND_VARIANT = { agent: "info", workflow: "warn", plugin: "" };

export async function render(ctx) {
  const { h, c } = ctx;
  const body = h("div", c.loading({ lines: 4, block: true }));
  let tab = "templates";

  const tabsEl = c.tabs(
    [{ key: "templates", label: "Templates" }, { key: "plugins", label: "Plugins" }, { key: "skills", label: "Skills" }],
    tab, (k) => { tab = k; paint(); },
  );

  const root = h("div.lt3-stack-6",
    c.viewHeader({
      eyebrow: "Platform",
      title: "Marketplace",
      sub: "Reusable agent templates, plugins, and skills — install, clone, customize, export, and import, all from /app.",
    }),
    tabsEl,
    body,
  );

  paint();
  return root;

  function paint() {
    [...tabsEl.querySelectorAll(".lt3-tab")].forEach((b, i) => { b.dataset.active = String(["templates", "plugins", "skills"][i] === tab); });
    body.replaceChildren(c.loading({ lines: 3, block: true }));
    if (tab === "templates") return paintTemplates();
    if (tab === "plugins") return paintPlugins();
    return paintSkills();
  }

  async function paintTemplates() {
    const res = await ctx.api.templates();
    const list = (res.data && res.data.templates) || [];
    if (!list.length) {
      body.replaceChildren(stateRow(ctx, res.source), c.emptyState({ icon: "package-off", title: "Catalog unavailable", body: "Start the backend to load the template catalog." }));
      return;
    }
    const byKind = {};
    for (const t of list) (byKind[t.kind] = byKind[t.kind] || []).push(t);
    const sections = Object.keys(byKind).map((kind) =>
      h("section",
        c.sectionHead(`${cap(kind)} templates`, c.pill(String(byKind[kind].length))),
        h("div.lt3-grid-auto", byKind[kind].map((t) => templateCard(ctx, t))),
      ));
    body.replaceChildren(h("div.lt3-stack-6", stateRow(ctx, res.source), ...sections));
  }

  function templateCard(ctx2, t) {
    const def = t.definition || {};
    const caps = def.capabilities || def.roles || [];
    return c.card(h("div.lt3-stack-3",
      h("div.lt3-row", { style: { "justify-content": "space-between", "align-items": "flex-start" } },
        h("div", h("b", t.name), h("div.lt3-faint", { style: { "font-size": "var(--lt3-text-2xs)", "font-family": "var(--lt3-font-mono)" } }, t.id)),
        c.pill(t.kind, KIND_VARIANT[t.kind] || ""),
      ),
      h("p.lt3-muted", { style: { margin: 0, "font-size": "var(--lt3-text-sm)" } }, t.description || ""),
      caps.length ? h("div.lt3-cluster", caps.slice(0, 6).map((x) => h("span.lt3-chip", x))) : null,
      h("div.lt3-row-2",
        h("button.lt3-btn.lt3-btn--primary.lt3-btn--sm", { on: { click: () => install(ctx2, t) } }, c.icon("download"), "Install"),
        h("button.lt3-btn.lt3-btn--subtle.lt3-btn--sm", { on: { click: () => clone(ctx2, t) } }, c.icon("copy"), "Clone"),
        h("button.lt3-btn.lt3-btn--ghost.lt3-btn--sm", { on: { click: () => exportT(ctx2, t) } }, c.icon("file-export"), "Export"),
      ),
      h("div.lt3-faint", { style: { "font-size": "var(--lt3-text-2xs)" } }, `v${t.version || "1.0.0"} · ${(t.metadata && t.metadata.category) || "general"}`),
    ), { interactive: false });
  }

  async function install(ctx2, t) {
    const res = await ctx2.api.installTemplate(t);
    ctx2.toast(res && res.ok ? `Installed ${t.name}` : "Install unavailable", res && res.ok ? "ok" : "err");
  }
  async function clone(ctx2, t) {
    const res = await ctx2.api.cloneTemplate(t.kind, t.id, `${t.name} (Copy)`);
    ctx2.toast(res && res.ok ? `Cloned to ${res.data.template.id}` : "Clone unavailable", res && res.ok ? "ok" : "err");
  }
  async function exportT(ctx2, t) {
    const res = await ctx2.api.exportTemplate(t.kind, t.id);
    if (res && res.ok && res.data) {
      try {
        const blob = new Blob([JSON.stringify(res.data, null, 2)], { type: "application/json" });
        const a = document.createElement("a");
        a.href = URL.createObjectURL(blob); a.download = `${t.id}.template.json`; a.click();
        URL.revokeObjectURL(a.href);
        ctx2.toast("Exported template", "ok");
      } catch { ctx2.toast("Export failed", "err"); }
    } else { ctx2.toast("Export unavailable", "err"); }
  }

  async function paintPlugins() {
    const [reg, dir] = await Promise.all([ctx.api.pluginsRegistry(), ctx.api.pluginsDirectory()]);
    const installed = (reg.data && reg.data.plugins) || [];
    const directory = (dir.data && dir.data.plugins) || [];
    if (!installed.length && !directory.length) {
      body.replaceChildren(stateRow(ctx, reg.source), c.emptyState({ icon: "plug-off", title: "No plugins", body: "Plugin registry and directory are unavailable." }));
      return;
    }
    body.replaceChildren(h("div.lt3-stack-6",
      stateRow(ctx, reg.source),
      h("section", c.sectionHead("Installed plugins", c.pill(String(installed.length))),
        installed.length ? h("div.lt3-grid-auto", installed.map((p) => simpleCard(ctx, p.name || p.id, p.description, p.version, "ok"))) : hint(ctx, "No plugins installed.")),
      h("section", c.sectionHead("Directory", c.pill(String(directory.length))),
        directory.length ? h("div.lt3-grid-auto", directory.slice(0, 30).map((p) => simpleCard(ctx, p.name, p.description, p.version, null, p.author))) : hint(ctx, "Directory unavailable.")),
    ));
  }

  async function paintSkills() {
    const res = await ctx.api.skillsMarketplace();
    const skills = (res.data && res.data.skills) || [];
    body.replaceChildren(h("div.lt3-stack-3",
      stateRow(ctx, res.source),
      h("p.lt3-faint", { style: { margin: 0 } }, "Manage installed skills in the Skills view; this is the discovery catalog."),
      skills.length
        ? h("div.lt3-grid-auto", skills.slice(0, 40).map((s) => simpleCard(ctx, s.skill || s.name, s.description, s.version, s.installed ? "ok" : null, s.author)))
        : c.emptyState({ icon: "puzzle-off", title: "Skills catalog unavailable", body: "The skills marketplace did not respond." }),
    ));
  }
}

function stateRow(ctx, source) {
  return ctx.h("div.lt3-row-2", { style: { "justify-content": "flex-end" } }, ctx.c.sourceBadge(source));
}
function simpleCard(ctx, name, desc, version, state, author) {
  const { h, c } = ctx;
  return c.card(h("div.lt3-stack-2",
    h("div.lt3-row", { style: { "justify-content": "space-between" } }, h("b", name || "Untitled"), state ? c.statePill(state === "ok" ? "ready" : state) : null),
    h("p.lt3-muted", { style: { margin: 0, "font-size": "var(--lt3-text-sm)" } }, desc || ""),
    h("div.lt3-faint", { style: { "font-size": "var(--lt3-text-2xs)" } }, [version ? `v${version}` : null, author ? `by ${author}` : null].filter(Boolean).join(" · ")),
  ), { flat: true });
}
function hint(ctx, text) { return ctx.h("p.lt3-faint", { style: { margin: 0 } }, text); }
function cap(s) { return String(s || "").charAt(0).toUpperCase() + String(s || "").slice(1); }
