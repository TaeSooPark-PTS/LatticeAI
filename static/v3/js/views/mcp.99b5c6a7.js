/* ============================================================================
 * View: MCP Manager — connected MCP servers, available tools, health.
 * Reads /mcp/tools, /mcp/installed, /mcp/claude-code-servers, /mcp/custom and
 * lets the user inspect connected servers and recommend new ones. Unavailable
 * state is explicit; nothing is invented.
 * ========================================================================== */

export async function render(ctx) {
  const { h, c } = ctx;
  const src = h("span", c.sourceBadge("pending"));
  const statHost = h("div.lt3-statrow", c.loading({ lines: 1 }));
  const serversHost = h("div", c.loading({ lines: 3, block: true }));
  const recHost = h("div");
  const recInput = h("input.lt3-input", { type: "text", placeholder: "Describe what you need (e.g. “web search”, “github”)…", "aria-label": "Recommend MCP" });

  const root = h("div.lt3-stack-6",
    c.viewHeader({
      eyebrow: "Platform",
      title: "MCP Manager",
      sub: "Model Context Protocol servers: what is connected, the tools they expose, their permissions, and their health — managed from /app.",
      actions: [src],
    }),
    statHost,
    c.panel({
      title: "Recommend a server", sub: "Find MCP servers that match a capability.",
      children: h("div.lt3-stack-3",
        h("div.lt3-row-2", recInput, h("button.lt3-btn.lt3-btn--primary", { on: { click: recommend } }, c.icon("search"), "Recommend")),
        recHost),
    }),
    h("section", c.sectionHead("Connected servers"), serversHost),
  );

  load();
  return root;

  async function load() {
    const [tools, claude, custom] = await Promise.all([ctx.api.mcpTools(), ctx.api.mcpClaudeServers(), ctx.api.mcpCustom()]);
    src.replaceChildren(c.sourceBadge(tools.source));
    const installed = (tools.data && tools.data.installed_mcps) || [];
    const claudeServers = (claude.data && claude.data.servers) || [];
    const customServers = (custom.data && custom.data.custom) || [];
    const toolList = (tools.data && tools.data.tools) || [];
    const servers = mergeServers(installed, claudeServers, customServers);

    if (!servers.length && !toolList.length) {
      statHost.replaceChildren(c.stat({ label: "MCP", value: "—", icon: "plug-connected" }));
      serversHost.replaceChildren(c.emptyState({ icon: "plug-off", title: "MCP unavailable", body: "Start the backend to read MCP servers and tools." }));
      return;
    }
    const connected = servers.filter((s) => s.installed).length;
    statHost.replaceChildren(
      c.stat({ label: "Servers", value: c.fmtNum(servers.length), icon: "server" }),
      c.stat({ label: "Connected", value: c.fmtNum(connected), icon: "plug-connected" }),
      c.stat({ label: "Local tools", value: c.fmtNum(toolList.length), icon: "tools" }),
    );
    if (!servers.length) {
      serversHost.replaceChildren(c.emptyState({ icon: "plug", title: "No MCP servers", body: "No MCP servers are registered yet. Use Recommend to find some." }));
      return;
    }
    serversHost.replaceChildren(h("div.lt3-grid-auto", servers.map((s) => serverCard(ctx, s))));
  }

  function serverCard(ctx2, s) {
    return c.card(h("div.lt3-stack-3",
      h("div.lt3-row", { style: { "justify-content": "space-between", "align-items": "flex-start" } },
        h("div.lt3-row-2",
          h("span.lt3-avatar", { style: { width: "34px", height: "34px", "border-radius": "var(--lt3-radius-md)" } }, s.icon && s.icon.length <= 2 ? s.icon : c.icon("plug")),
          h("div", h("b", s.name), h("div.lt3-faint", { style: { "font-size": "var(--lt3-text-2xs)" } }, s.category || s.source || "")),
        ),
        c.statePill(s.installed ? "ready" : "available"),
      ),
      s.description ? h("p.lt3-muted", { style: { margin: 0, "font-size": "var(--lt3-text-sm)" } }, s.description) : null,
      s.package ? h("div.lt3-faint", { style: { "font-size": "var(--lt3-text-2xs)", "font-family": "var(--lt3-font-mono)", "word-break": "break-all" } }, s.package) : null,
      Array.isArray(s.env_vars) && s.env_vars.length ? h("div.lt3-cluster", s.env_vars.slice(0, 4).map((e) => h("span.lt3-chip", c.icon("key"), e.name || e))) : null,
    ), { interactive: false });
  }

  async function recommend() {
    const q = recInput.value.trim();
    if (!q) { ctx.toast("Describe what you need", "info"); return; }
    recHost.replaceChildren(c.loading({ lines: 2 }));
    const res = await ctx.api.mcpRecommend(q, 6);
    const recs = (res && res.data && (res.data.recommendations || res.data)) || [];
    if (!res || !res.ok || !Array.isArray(recs) || !recs.length) {
      recHost.replaceChildren(c.banner("No recommendations available — the MCP registry may be offline.", "info"));
      return;
    }
    recHost.replaceChildren(h("div.lt3-stack-2", recs.slice(0, 6).map((r) => c.card(
      h("div.lt3-row", { style: { "justify-content": "space-between" } },
        h("div", h("b", r.name || r.id), h("p.lt3-muted", { style: { margin: 0, "font-size": "var(--lt3-text-sm)" } }, r.description || "")),
        r.category ? c.pill(r.category) : null),
      { flat: true }))));
  }
}

function mergeServers(installed, claude, custom) {
  const out = [];
  const seen = new Set();
  const push = (s, source) => {
    const id = s.id || s.name;
    if (!id || seen.has(id)) return;
    seen.add(id);
    out.push({
      id, name: s.name || id, description: s.description || "", category: s.category || source,
      package: s.package || "", icon: s.icon || "", source: s.source || source,
      installed: s.installed != null ? !!s.installed : source === "claude-code",
      env_vars: s.env_vars || [],
    });
  };
  (installed || []).forEach((s) => push(s, "registry"));
  (claude || []).forEach((s) => push(s, "claude-code"));
  (custom || []).forEach((s) => push(s, "custom"));
  return out;
}
