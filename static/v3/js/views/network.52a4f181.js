import { t } from "../core/i18n.880e1fec.js";

export async function render(ctx) {
  const { h, icon, api, c, toast, store } = ctx;
  const host = h("div.lt3-stack-6", c.loading({ lines: 5, block: true }));

  async function load() {
    const [identity, peers] = await Promise.all([api.networkIdentity(), api.networkPeers()]);
    host.replaceChildren(
      c.viewHeader({
        eyebrow: t("network.eyebrow"),
        title: t("network.title"),
        sub: t("network.sub"),
        actions: [c.sourceBadge(identity.source === "live" || peers.source === "live" ? "live" : "unavailable")],
      }),
      identityPanel(identity),
      pairPanel(),
      peersPanel(peers),
    );
  }

  function identityPanel(res) {
    const d = res.data || {};
    return c.panel({
      title: t("network.identity"),
      actions: [c.sourceBadge(res.source)],
      children: h("dl.lt3-keyval",
        h("dt", "device_id"), h("dd", h("span.lt3-mono", d.device_id || d.id || "—")),
        h("dt", "fingerprint"), h("dd", h("span.lt3-mono", d.fingerprint || d.public_key_fingerprint || "—")),
        h("dt", t("network.publicKey")), h("dd", h("pre.lt3-code", truncate(d.public_key || "—", 420))),
      ),
    });
  }

  function pairPanel() {
    const name = h("input.lt3-input", { type: "text", placeholder: t("network.peerName"), "aria-label": t("network.peerName") });
    const base = h("input.lt3-input", { type: "url", placeholder: t("network.baseUrl"), "aria-label": t("network.baseUrl") });
    const key = h("textarea.lt3-textarea", { rows: 3, placeholder: t("network.publicKey"), "aria-label": t("network.publicKey") });
    return c.panel({
      title: t("network.pair"),
      children: h("div.lt3-stack-4",
        h("div.lt3-grid-2", field(ctx, t("network.peerName"), name), field(ctx, t("network.baseUrl"), base)),
        field(ctx, t("network.publicKey"), key),
        h("button.lt3-btn.lt3-btn--primary", { on: { click: async () => {
          const res = await api.pairPeer({ name: name.value.trim(), base_url: base.value.trim(), public_key: key.value.trim() });
          toast(resultText(res, t("network.paired")), res.ok ? "ok" : "err");
          if (res.ok) load();
        } } }, icon("link"), t("network.pair")),
      ),
    });
  }

  function peersPanel(res) {
    const rows = Array.isArray(res.data?.peers) ? res.data.peers : [];
    return c.panel({
      title: t("network.peers"),
      actions: [c.sourceBadge(res.source)],
      children: rows.length ? c.table([
        { key: "name", label: t("common.name"), render: (p) => h("div", h("b", p.name || p.peer_id || p.id), h("div.lt3-faint", { style: { "font-family": "var(--lt3-font-mono)", "font-size": "var(--lt3-text-2xs)" } }, p.peer_id || p.id || "")) },
        { key: "base", label: t("network.baseUrl"), render: (p) => p.base_url || "—" },
        { key: "fp", label: "fingerprint", render: (p) => h("span.lt3-mono", p.fingerprint || p.public_key_fingerprint || "—") },
        { key: "act", label: "", width: "1%", render: (p) => h("div.lt3-row-2",
          h("button.lt3-btn.lt3-btn--subtle.lt3-btn--sm", { on: { click: () => pushPeer(p.peer_id || p.id) } }, icon("send"), t("network.push")),
          h("button.lt3-btn.lt3-btn--ghost.lt3-btn--sm", { on: { click: () => unpairPeer(p.peer_id || p.id) } }, icon("unlink"), t("network.unpair")),
        ) },
      ], rows) : c.emptyState({ icon: "network-off", title: t("network.peers"), body: t("common.none") }),
    });
  }

  async function pushPeer(peerId) {
    const res = await api.pushPeer(peerId, store.get().workspaceId);
    toast(resultText(res, t("network.pushed")), res.ok ? "ok" : "err");
  }
  async function unpairPeer(peerId) {
    const res = await api.unpairPeer(peerId);
    toast(resultText(res, t("network.unpaired")), res.ok ? "ok" : "err");
    if (res.ok) load();
  }

  await load();
  return host;
}

function field({ h }, label, control) {
  return h("div.lt3-field", h("label.lt3-label", label), control);
}

function truncate(value, n) {
  const s = String(value || "");
  return s.length > n ? `${s.slice(0, n)}…` : s;
}

function resultText(res, okText) {
  if (res && res.ok) return okText;
  const data = (res && res.data) || {};
  return String(data.detail || data.error || res?.error || t("common.unavailable"));
}
