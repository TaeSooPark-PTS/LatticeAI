/* ============================================================================
 * View: Skills — the skills registry (install / enable / disable / remove).
 * Reads the live workspace skill registry (/workspace/skills) and toggles real
 * state. The discovery catalog lives in Marketplace; this is management.
 * ========================================================================== */

export async function render(ctx) {
  const { h, c } = ctx;
  const src = h("span", c.sourceBadge("pending"));
  const statHost = h("div.lt3-statrow", c.loading({ lines: 1 }));
  const listHost = h("div", c.loading({ lines: 4, block: true }));

  const root = h("div.lt3-stack-6",
    c.viewHeader({
      eyebrow: "Platform",
      title: "Skills",
      sub: "Install, enable, disable, and remove skills. Installed skills are shared machine-global capabilities the agent can use.",
      actions: [src],
    }),
    statHost,
    h("section", c.sectionHead("Installed & available skills"), listHost),
  );

  load();
  return root;

  async function load() {
    const res = await ctx.api.skills();
    src.replaceChildren(c.sourceBadge(res.source));
    const skills = normalize(res.data);
    if (!skills.length) {
      statHost.replaceChildren(c.stat({ label: "Skills", value: "—", icon: "puzzle" }));
      listHost.replaceChildren(c.emptyState({ icon: "puzzle-off", title: "Skills registry unavailable", body: res.source === "live" ? "No skills are registered yet — install one from the Marketplace." : "Start the backend to read the skills registry." }));
      return;
    }
    const enabled = skills.filter((s) => s.enabled).length;
    const installed = skills.filter((s) => s.installed).length;
    statHost.replaceChildren(
      c.stat({ label: "Skills", value: c.fmtNum(skills.length), icon: "puzzle" }),
      c.stat({ label: "Enabled", value: c.fmtNum(enabled), icon: "circle-check" }),
      c.stat({ label: "Installed", value: c.fmtNum(installed), icon: "download" }),
    );
    listHost.replaceChildren(h("div.lt3-grid-auto", skills.map((s) => skillCard(ctx, s))));
  }

  function skillCard(ctx2, s) {
    return c.card(h("div.lt3-stack-3",
      h("div.lt3-row", { style: { "justify-content": "space-between", "align-items": "flex-start" } },
        h("div", h("b", s.name), h("div.lt3-faint", { style: { "font-size": "var(--lt3-text-2xs)" } }, [s.source, s.version ? `v${s.version}` : null, s.category].filter(Boolean).join(" · "))),
        c.statePill(s.enabled ? "ready" : s.installed ? "idle" : "available"),
      ),
      s.description ? h("p.lt3-muted", { style: { margin: 0, "font-size": "var(--lt3-text-sm)" } }, s.description) : null,
      h("div.lt3-row-2",
        s.installed
          ? h("button.lt3-btn.lt3-btn--subtle.lt3-btn--sm", { on: { click: () => toggle(ctx2, s) } }, c.icon(s.enabled ? "player-pause" : "player-play"), s.enabled ? "Disable" : "Enable")
          : h("button.lt3-btn.lt3-btn--primary.lt3-btn--sm", { on: { click: () => install(ctx2, s) } }, c.icon("download"), "Install"),
        s.installed ? h("button.lt3-btn.lt3-btn--ghost.lt3-btn--sm", { on: { click: () => uninstall(ctx2, s) } }, c.icon("trash"), "Remove") : null,
      ),
    ), { interactive: false });
  }

  async function toggle(ctx2, s) {
    const res = s.enabled ? await ctx2.api.skillDisable(s.name) : await ctx2.api.skillEnable(s.name);
    ctx2.toast(res && res.ok ? `${s.enabled ? "Disabled" : "Enabled"} ${s.name}` : "Action unavailable", res && res.ok ? "ok" : "err");
    load();
  }
  async function install(ctx2, s) {
    const res = await ctx2.api.skillInstall(s.name, s.plugin);
    ctx2.toast(res && res.ok ? `Installed ${s.name}` : (res && res.status === 403 ? "Admin required" : "Install unavailable"), res && res.ok ? "ok" : "err");
    load();
  }
  async function uninstall(ctx2, s) {
    const res = await ctx2.api.skillUninstall(s.name);
    ctx2.toast(res && res.ok ? `Removed ${s.name}` : (res && res.status === 403 ? "Admin required" : "Remove unavailable"), res && res.ok ? "ok" : "err");
    load();
  }
}

function normalize(data) {
  if (!data) return [];
  const list = Array.isArray(data.skills) ? data.skills
    : Array.isArray(data.registry) ? data.registry
    : Array.isArray(data) ? data : [];
  return list.map((s) => ({
    name: s.name || s.skill || s.id || "skill",
    description: s.description || "",
    version: s.version || "",
    source: s.source || "",
    category: s.category || "",
    plugin: s.plugin || "",
    enabled: s.enabled != null ? !!s.enabled : !!s.installed,
    installed: s.installed != null ? !!s.installed : true,
  }));
}
