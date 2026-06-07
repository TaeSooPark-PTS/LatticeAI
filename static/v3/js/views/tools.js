/* ============================================================================
 * View: Tools — the unified tool registry (local / workspace / MCP).
 * Reads /mcp/tools (descriptions + governance) and /tools/permissions for risk
 * and approval. Tools are grouped by risk with governance pills; MCP servers
 * surface alongside. Unavailable is explicit.
 * ========================================================================== */

const RISK_ORDER = ["high", "medium", "low"];
const RISK_VARIANT = { high: "err", medium: "warn", low: "ok" };

export async function render(ctx) {
  const { h, c } = ctx;
  const src = h("span", c.sourceBadge("pending"));
  const statHost = h("div.lt3-statrow", c.loading({ lines: 1 }));
  const groupsHost = h("div", c.loading({ lines: 4, block: true }));

  const root = h("div.lt3-stack-6",
    c.viewHeader({
      eyebrow: "Platform",
      title: "Tool Registry",
      sub: "Every tool the agent can call — local, workspace, and MCP — with its governance policy: risk, approval, sandbox, and network access.",
      actions: [src],
    }),
    statHost,
    h("section", c.sectionHead("Registered tools"), groupsHost),
  );

  load();
  return root;

  async function load() {
    const [mcp, perms] = await Promise.all([ctx.api.mcpTools(), ctx.api.toolPermissions()]);
    src.replaceChildren(c.sourceBadge(mcp.source === "live" || perms.source === "live" ? "live" : "unavailable"));
    const permMap = {};
    for (const p of normalizePerms(perms.data)) permMap[p.tool] = p;
    const tools = mergeTools(mcp.data, permMap);

    if (!tools.length) {
      statHost.replaceChildren(c.stat({ label: "Tools", value: "—", icon: "tools" }));
      groupsHost.replaceChildren(c.emptyState({ icon: "tool", title: "Tool registry unavailable", body: "Start the backend to read the tool registry." }));
      return;
    }
    const mcpServers = (mcp.data && mcp.data.installed_mcps) || [];
    const approval = tools.filter((t) => t.requires_approval).length;
    statHost.replaceChildren(
      c.stat({ label: "Tools", value: c.fmtNum(tools.length), icon: "tools" }),
      c.stat({ label: "Need approval", value: c.fmtNum(approval), icon: "lock" }),
      c.stat({ label: "MCP servers", value: c.fmtNum(mcpServers.length), icon: "plug-connected" }),
    );

    const byRisk = {};
    for (const t of tools) (byRisk[t.risk] = byRisk[t.risk] || []).push(t);
    const sections = RISK_ORDER.filter((r) => byRisk[r]).map((risk) =>
      h("section",
        c.sectionHead(h("span.lt3-row-2", c.pill(`${risk} risk`, RISK_VARIANT[risk]), c.pill(String(byRisk[risk].length))), null),
        c.table(
          [
            { key: "name", label: "Tool", render: (t) => h("div", h("b", { style: { "font-family": "var(--lt3-font-mono)", "font-size": "var(--lt3-text-sm)" } }, t.name), t.description ? h("div.lt3-faint", { style: { "font-size": "var(--lt3-text-2xs)" } }, t.description) : null) },
            { key: "approval", label: "Approval", width: "1%", render: (t) => c.statePill(t.requires_approval ? "pending" : "ready") },
            { key: "sandbox", label: "Sandbox", width: "1%", render: (t) => t.sandbox ? c.pill(t.sandbox) : h("span.lt3-faint", "—") },
            { key: "net", label: "Network", width: "1%", render: (t) => t.network ? c.pill("network", "warn") : h("span.lt3-faint", "—") },
          ],
          byRisk[risk],
        ),
      ));
    groupsHost.replaceChildren(h("div.lt3-stack-6", ...sections));
  }
}

function normalizePerms(data) {
  if (!data) return [];
  if (Array.isArray(data.permissions)) return data.permissions;
  if (Array.isArray(data)) return data;
  return [];
}

function mergeTools(mcpData, permMap) {
  const out = [];
  const seen = new Set();
  const list = (mcpData && mcpData.tools) || [];
  for (const t of list) {
    const gov = t.governance || {};
    const perm = permMap[t.name] || {};
    out.push({
      name: t.name,
      description: t.description || "",
      risk: (perm.risk || riskFromGov(gov) || "medium"),
      requires_approval: perm.requires_approval != null ? perm.requires_approval : !gov.auto_approve,
      sandbox: gov.sandbox || "",
      network: gov.network != null ? gov.network : perm.network,
    });
    seen.add(t.name);
  }
  // Tools present only in the permission list (no MCP description).
  for (const name of Object.keys(permMap)) {
    if (seen.has(name)) continue;
    const p = permMap[name];
    out.push({ name, description: "", risk: p.risk || "medium", requires_approval: !!p.requires_approval, sandbox: "", network: !!p.network });
  }
  return out;
}

function riskFromGov(gov) {
  if (gov.risk === "exec" || gov.risk === "destructive") return "high";
  if (gov.risk === "write") return "medium";
  if (gov.risk === "read") return "low";
  return null;
}
