/* ============================================================================
 * View: Hooks — the lifecycle hooks registry + dispatch.
 * Reads /api/hooks (built-in + user hooks across the pre_/post_ run, tool,
 * workflow, upload, and index lifecycle pairs + agent), toggles enabled state,
 * reorders, registers, runs hooks, and shows a recent-executions log. Built-in
 * hooks are platform-managed; non-executable hooks are labelled "advisory".
 * ========================================================================== */

const KIND_LABEL = {
  pre_run: "Pre-run", post_run: "Post-run",
  pre_tool: "Pre-tool", post_tool: "Post-tool",
  pre_workflow: "Pre-workflow", post_workflow: "Post-workflow",
  pre_upload: "Pre-upload", post_upload: "Post-upload",
  pre_index: "Pre-index", post_index: "Post-index",
  agent: "Agent",
};

export async function render(ctx) {
  const { h, c } = ctx;
  const src = h("span", c.sourceBadge("pending"));
  const statHost = h("div.lt3-statrow", c.loading({ lines: 1 }));
  const groupsHost = h("div", c.loading({ lines: 4, block: true }));

  // Recent executions — proves hooks fire end-to-end (v3.4.0).
  const runsSrc = h("span", c.sourceBadge("pending"));
  const runsHost = h("div", c.loading({ lines: 3, block: true }));

  const nameInput = h("input.lt3-input", { type: "text", placeholder: "Hook name" });
  const kindSelect = h("select.lt3-select", Object.keys(KIND_LABEL).map((k) => h("option", { value: k }, KIND_LABEL[k])));
  const descInput = h("input.lt3-input", { type: "text", placeholder: "What it does (optional)" });

  const root = h("div.lt3-stack-6",
    c.viewHeader({
      eyebrow: "Platform",
      title: "Hooks",
      sub: "Lifecycle extension points across runs, tools, agents, pipelines, and workflows — visible, ordered, and individually toggleable.",
      actions: [src],
    }),
    statHost,
    c.panel({
      title: "Register a hook", sub: "Custom hooks are listed, ordered, and inspectable.",
      children: h("div.lt3-stack-3",
        h("div.lt3-grid-2", h("div.lt3-field", h("label", "Name"), nameInput), h("div.lt3-field", h("label", "Kind"), kindSelect)),
        h("div.lt3-field", h("label", "Description"), descInput),
        h("div.lt3-row-2", h("button.lt3-btn.lt3-btn--primary", { on: { click: register } }, c.icon("plus"), "Register hook")),
      ),
    }),
    groupsHost,
    c.panel({
      title: "Recent executions",
      sub: "What actually ran. Blocked pre-hooks and advisory built-ins are surfaced honestly — no fabricated success.",
      actions: [
        runsSrc,
        h("button.lt3-btn.lt3-btn--subtle.lt3-btn--sm", { on: { click: loadRuns } }, c.icon("refresh"), "Refresh"),
      ],
      children: runsHost,
    }),
  );

  load();
  loadRuns();
  return root;

  async function load() {
    const res = await ctx.api.hooks();
    src.replaceChildren(c.sourceBadge(res.source));
    const hooks = (res.data && res.data.hooks) || [];
    if (!hooks.length) {
      statHost.replaceChildren(c.stat({ label: "Hooks", value: "—", icon: "webhook" }));
      groupsHost.replaceChildren(c.emptyState({ icon: "webhook-off", title: "Hooks unavailable", body: "Start the backend to read the hooks registry." }));
      return;
    }
    const en = hooks.filter((x) => x.enabled).length;
    statHost.replaceChildren(
      c.stat({ label: "Hooks", value: c.fmtNum(hooks.length), icon: "webhook" }),
      c.stat({ label: "Enabled", value: c.fmtNum(en), icon: "circle-check" }),
      c.stat({ label: "Kinds", value: c.fmtNum((res.data.kinds || []).length), icon: "layers" }),
    );
    const byKind = {};
    for (const hk of hooks) (byKind[hk.kind] = byKind[hk.kind] || []).push(hk);
    groupsHost.replaceChildren(h("div.lt3-stack-6", Object.keys(byKind).map((kind) =>
      h("section", c.sectionHead(
          KIND_LABEL[kind] || kind,
          c.pill(String(byKind[kind].length)),
          h("button.lt3-btn.lt3-btn--subtle.lt3-btn--sm", { on: { click: () => runKind(kind) } }, c.icon("player-play"), `Run ${KIND_LABEL[kind] || kind}`),
        ),
        h("div.lt3-stack-2", byKind[kind].map((hk) => hookRow(ctx, hk)))))));
  }

  function hookRow(ctx2, hk) {
    return c.card(h("div.lt3-row", { style: { "justify-content": "space-between", "align-items": "center", gap: "var(--lt3-space-3)" } },
      h("div", { style: { "min-width": 0 } },
        h("div.lt3-row-2", h("b", hk.name), c.pill(hk.source === "builtin" ? "built-in" : "custom", hk.source === "builtin" ? "info" : ""), hk.managed === "platform" ? c.pill("managed", "") : null, hk.advisory ? c.pill("advisory", "warn") : c.pill("executable", "ok")),
        h("p.lt3-muted", { style: { margin: "2px 0 0", "font-size": "var(--lt3-text-sm)" } }, hk.description || ""),
        hk.binding ? h("div.lt3-faint", { style: { "font-size": "var(--lt3-text-2xs)", "font-family": "var(--lt3-font-mono)" } }, hk.binding) : null,
      ),
      h("div.lt3-row-2", { style: { "flex-shrink": 0 } },
        h("span.lt3-faint", { style: { "font-size": "var(--lt3-text-2xs)" } }, `#${hk.order}`),
        h("button.lt3-btn.lt3-btn--subtle.lt3-btn--sm", { on: { click: () => runHook(hk) } }, c.icon("player-play"), "Run"),
        h("button.lt3-btn.lt3-btn--subtle.lt3-btn--sm", { on: { click: () => toggle(ctx2, hk) } }, c.icon(hk.enabled ? "toggle-right" : "toggle-left"), hk.enabled ? "On" : "Off"),
        hk.removable ? h("button.lt3-btn.lt3-btn--ghost.lt3-btn--sm", { on: { click: () => remove(ctx2, hk) } }, c.icon("trash")) : null,
      ),
    ), { flat: true });
  }

  async function toggle(ctx2, hk) {
    const res = hk.enabled ? await ctx2.api.hookDisable(hk.id) : await ctx2.api.hookEnable(hk.id, true);
    ctx2.toast(res && res.ok ? `${hk.name}: ${hk.enabled ? "disabled" : "enabled"}` : "Action unavailable", res && res.ok ? "ok" : "err");
    load();
  }
  async function remove(ctx2, hk) {
    const res = await ctx2.api.hookRemove(hk.id);
    ctx2.toast(res && res.ok ? `Removed ${hk.name}` : "Remove unavailable", res && res.ok ? "ok" : "err");
    load();
  }
  async function register() {
    const name = nameInput.value.trim();
    if (!name) { ctx.toast("Enter a hook name", "info"); return; }
    const res = await ctx.api.hookRegister({ name, kind: kindSelect.value, description: descInput.value.trim() });
    if (res && res.ok) { ctx.toast(`Registered ${name}`, "ok"); nameInput.value = ""; descInput.value = ""; load(); }
    else { ctx.toast("Register unavailable", "err"); }
  }

  /* ── Dispatch ─────────────────────────────────────────────────────────────
   * Fire hooks for real and surface the honest outcome. status === "ok" is a
   * clean run; "blocked"/"skipped"/"advisory" are warnings (advisory built-ins
   * never claim success); "error" is a failure. The run log refreshes after. */

  // Map a hook status to a toast variant — advisory/blocked are never "ok".
  function statusVariant(status) {
    if (status === "ok") return "ok";
    if (status === "error") return "err";
    return "warn"; // blocked | skipped | advisory | anything unexpected
  }

  async function runHook(hk) {
    const res = await ctx.api.hookRun({ hook_id: hk.id, event: "manual" });
    if (!res || !res.ok || !res.data) {
      const detail = (res && res.data && (res.data.detail || res.data.error)) || (res && res.error) || "dispatch unavailable";
      ctx.toast(`Run failed: ${detail}`, "err");
      loadRuns();
      return;
    }
    const d = res.data;
    const status = d.status || "unknown";
    ctx.toast(`Ran ${hk.name}: ${status}${d.detail ? " — " + d.detail : ""}`, statusVariant(status));
    loadRuns();
  }

  async function runKind(kind) {
    const res = await ctx.api.hookRun({ kind, event: "manual" });
    if (!res || !res.ok || !res.data) {
      const detail = (res && res.data && (res.data.detail || res.data.error)) || (res && res.error) || "dispatch unavailable";
      ctx.toast(`Run ${KIND_LABEL[kind] || kind} failed: ${detail}`, "err");
      loadRuns();
      return;
    }
    const d = res.data;
    const ran = d.ran == null ? 0 : d.ran;
    const label = KIND_LABEL[kind] || kind;
    const variant = d.blocked ? "warn" : (ran > 0 ? "ok" : "info");
    ctx.toast(`${ran} ${label} hook(s) ran${d.blocked ? " — blocked: " + (d.block_reason || "policy") : ""}`, variant);
    loadRuns();
  }

  /* ── Recent executions log ─────────────────────────────────────────────── */

  async function loadRuns() {
    runsHost.replaceChildren(c.loading({ lines: 3, block: true }));
    const res = await ctx.api.hookRuns(50);
    runsSrc.replaceChildren(c.sourceBadge(res.source));
    const runs = (res.data && res.data.runs) || [];
    if (!res.ok && res.source === "unavailable") {
      runsHost.replaceChildren(c.emptyState({
        icon: "webhook-off",
        title: "Run log unavailable",
        body: "Start the backend to read recent hook executions.",
      }));
      return;
    }
    if (!runs.length) {
      runsHost.replaceChildren(c.emptyState({
        icon: "history-off",
        title: "No hook executions yet",
        body: "Run a hook, or trigger an agent run / workflow / upload to see lifecycle hooks fire here.",
      }));
      return;
    }
    runsHost.replaceChildren(h("div.lt3-stack-2", runs.map((r) => runRow(r))));
  }

  function runRow(r) {
    const status = r.status || "unknown";
    const detail = r.detail || r.output || "";
    return c.card(h("div.lt3-row", { style: { "justify-content": "space-between", "align-items": "flex-start", gap: "var(--lt3-space-3)" } },
      h("div", { style: { "min-width": 0 } },
        h("div.lt3-row-2",
          h("b", r.name || r.hook_id || "hook"),
          c.pill(KIND_LABEL[r.kind] || r.kind || "—", "info"),
          c.statePill(status),
        ),
        (r.target_event || r.target_kind) ? h("div.lt3-faint", { style: { "font-size": "var(--lt3-text-2xs)", "font-family": "var(--lt3-font-mono)", "margin-top": "2px" } },
          `event: ${r.target_event || r.target_kind || "—"}`) : null,
        detail ? h("p.lt3-faint", {
          style: { margin: "4px 0 0", "font-size": "var(--lt3-text-xs)", "font-family": "var(--lt3-font-mono)", "white-space": "nowrap", overflow: "hidden", "text-overflow": "ellipsis", "max-width": "100%" },
          title: detail,
        }, truncate(detail, 160)) : null,
      ),
      h("div", { style: { "flex-shrink": 0, "text-align": "right" } },
        h("div.lt3-faint", { style: { "font-size": "var(--lt3-text-2xs)" } }, r.duration_ms == null ? "—" : `${r.duration_ms}ms`),
        r.started_at ? h("div.lt3-faint", { style: { "font-size": "var(--lt3-text-2xs)", "margin-top": "2px" } }, r.started_at) : null,
      ),
    ), { flat: true });
  }

  function truncate(s, n) {
    s = String(s);
    return s.length > n ? s.slice(0, n - 1) + "…" : s;
  }
}
