/* ============================================================================
 * View: Chat — a grounded conversation surface (integration-ready preview).
 * Flush, full-height layout: a main thread + composer on the left, a "grounding
 * context" rail on the right that shows what the retrieval lattice would feed
 * the next answer. This is a PREVIEW — it never calls a backend for generation;
 * the production chat lives at /chat. When wired, sends will ground answers via
 * POST /api/search/hybrid. All user-entered text is escaped (passed as children).
 * ========================================================================== */

export const layout = "flush";

export async function render(ctx) {
  const { h, icon, api, store, c, params, navigate, toast } = ctx;

  // Grounding toggles drive the (future) retrieval request shape.
  const ground = { graph: true, vector: true };
  let modelName = "Local model";

  /* ── Thread ───────────────────────────────────────────────────────────── */
  const thread = h("div.lt3-chat__thread", { role: "log", "aria-live": "polite", "aria-label": "Conversation" });
  const scrollToEnd = () => requestAnimationFrame(() => { thread.scrollTop = thread.scrollHeight; });

  function avatar(role) {
    return h("div.lt3-msg__avatar", { "aria-hidden": "true" }, icon(role === "user" ? "user" : "sparkles"));
  }

  // bubbleKids: array of nodes/strings. Strings are auto-escaped as children.
  function addMsg(role, ...bubbleKids) {
    const msg = h(`div.lt3-msg.lt3-msg--${role === "user" ? "user" : "ai"}`,
      avatar(role),
      h("div.lt3-msg__bubble", bubbleKids),
    );
    thread.append(msg);
    scrollToEnd();
    return msg;
  }

  // A citation chip that routes into the grounding source it came from.
  function citation(label, routeKey, routeParams) {
    return h("button.lt3-chip", {
      type: "button",
      "aria-label": `Open source: ${label}`,
      on: { click: () => navigate(routeKey, routeParams) },
    }, icon(routeKey === "files" ? "file" : "chart-dots-3"), label);
  }

  function seedThread() {
    thread.replaceChildren();
    if (params && params.new) {
      thread.append(h("div", { style: { margin: "auto", "max-width": "440px" } },
        c.emptyState({
          icon: "message-2",
          title: "Ask anything about your workspace",
          body: "Answers are grounded in your knowledge graph and vector index — nothing leaves this machine.",
        }),
      ));
      return;
    }
    addMsg("user", "How does hybrid search decide what to surface?");
    addMsg("ai",
      h("p", { style: { margin: "0 0 var(--lt3-space-3)" } },
        "Hybrid search runs the vector index and the knowledge graph in parallel, then reconciles the two ranked lists with reciprocal-rank fusion — so a strong signal in either modality can surface, and entities adjacent in the graph reinforce semantically close passages."),
      h("div.lt3-cluster", { style: { "margin-top": "var(--lt3-space-2)" } },
        h("span.lt3-faint", { style: { "font-size": "var(--lt3-text-2xs)" } }, "Grounded in"),
        citation("graph: Rank Fusion", "knowledge-graph"),
        citation("file: retrieval.md", "files"),
      ),
    );
  }

  /* ── Send ─────────────────────────────────────────────────────────────── */
  function send() {
    const text = (input.value || "").trim();
    if (!text) return;
    // Clear the centered empty-state on first real message.
    const empty = thread.querySelector(".lt3-empty");
    if (empty) thread.replaceChildren();
    addMsg("user", text); // text passed as a child string → auto-escaped.
    input.value = "";
    autoGrow();
    const mods = [ground.graph && "knowledge graph", ground.vector && "vector index"].filter(Boolean).join(" + ") || "no grounding sources";
    addMsg("ai",
      h("p", { style: { margin: "0 0 var(--lt3-space-3)" } },
        "This is an integration-ready preview, so I'm not generating a live answer yet. Once connected, I'll ground responses by fusing the ", mods,
        " via POST /api/search/hybrid, then cite the exact entities and files behind every claim."),
      h("button.lt3-btn.lt3-btn--subtle.lt3-btn--sm", {
        type: "button",
        on: { click: () => { window.location.href = "/chat"; } },
      }, icon("arrow-up-right"), "Continue in classic chat"),
    );
    input.focus();
  }

  /* ── Composer ─────────────────────────────────────────────────────────── */
  const input = h("textarea", {
    rows: 1,
    placeholder: "Message your workspace…   (Enter to send · Shift+Enter for newline)",
    "aria-label": "Message your workspace",
    on: {
      input: () => autoGrow(),
      keydown: (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } },
    },
  });
  function autoGrow() {
    input.style.height = "auto";
    input.style.height = Math.min(input.scrollHeight, 180) + "px";
  }

  const sendBtn = h("button.lt3-btn.lt3-btn--primary.lt3-iconbtn", {
    type: "button", "aria-label": "Send message", title: "Send",
    style: { width: "38px", height: "38px", color: "#fff" },
    on: { click: () => send() },
  }, icon("arrow-up"));

  function groundChip(key, label) {
    const chip = h("button.lt3-chip", {
      type: "button",
      "aria-pressed": String(ground[key]),
      dataset: { active: String(ground[key]) },
      "aria-label": `Toggle ${label} grounding`,
      on: { click: () => {
        ground[key] = !ground[key];
        chip.dataset.active = String(ground[key]);
        chip.setAttribute("aria-pressed", String(ground[key]));
        toast(`${label} grounding ${ground[key] ? "on" : "off"}`, ground[key] ? "ok" : "info");
      } },
    }, icon(key === "graph" ? "chart-dots-3" : "grid-dots"), label);
    return chip;
  }

  const modelChip = h("span.lt3-chip", { style: { "margin-left": "auto" } }, icon("cpu"), h("span", { id: "lt3-chat-model" }, modelName));

  const composer = h("div.lt3-composer",
    h("div.lt3-composer__box",
      input,
      sendBtn,
    ),
    h("div.lt3-composer__tools",
      h("span.lt3-faint", { style: { "font-size": "var(--lt3-text-2xs)", "margin-right": "var(--lt3-space-1)" } }, "Ground with"),
      groundChip("graph", "Knowledge Graph"),
      groundChip("vector", "Vector"),
      modelChip,
    ),
  );

  /* ── Top bar (compact, inside flush main) ─────────────────────────────── */
  const modelPillSlot = h("span", c.pill("…", "info", { dot: true }));
  const topSrcSlot = h("span", c.sourceBadge("pending"));
  const topbar = h("div.lt3-row", {
    style: {
      padding: "var(--lt3-space-3) var(--lt3-space-6)",
      "border-bottom": "1px solid var(--border)",
      flex: "none",
    },
  },
    h("div.lt3-row-2",
      h("strong", { style: { "font-size": "var(--lt3-text-md)" } }, "Chat"),
      modelPillSlot,
      topSrcSlot,
    ),
    h("span", { style: { flex: "1" } }),
    h("button.lt3-btn.lt3-btn--ghost.lt3-btn--sm", {
      type: "button",
      on: { click: () => { window.location.href = "/chat"; } },
    }, icon("external-link"), "Open classic chat"),
  );

  const main = h("div.lt3-chat__main", topbar, thread, composer);

  /* ── Grounding context rail ───────────────────────────────────────────── */
  const railBody = h("div.lt3-stack-4", c.loading({ lines: 4 }));
  const railSrcSlot = h("span", c.sourceBadge("pending"));
  const rail = h("aside.lt3-chat__context",
    h("div.lt3-row", { style: { "justify-content": "space-between", "margin-bottom": "var(--lt3-space-4)" } },
      h("div.lt3-eyebrow", icon("target"), "Grounding context"),
      railSrcSlot,
    ),
    railBody,
  );

  const root = h("div.lt3-chat", main, rail);

  /* ── Hydrate (async, after first paint) ───────────────────────────────── */
  seedThread();
  autoGrow();
  hydrate();
  return root;

  async function hydrate() {
    // Resolve the active model for the top pill + composer chip.
    const m = await api.models();
    const cat = (m.data && m.data.catalog) || [];
    const cur = (m.data && m.data.current) || (cat[0] && cat[0].id) || "";
    const hit = cat.find((x) => x.id === cur);
    modelName = (hit && hit.name) || cur || "Local model";
    modelPillSlot.replaceChildren(c.pill(modelName, "info", { dot: true }));
    topSrcSlot.replaceChildren(c.sourceBadge(m.source));
    root.querySelector("#lt3-chat-model")?.replaceChildren(document.createTextNode(modelName));

    // Retrieval context that would ground the next answer.
    const idx = store.get().indexStatus
      ? { data: store.get().indexStatus, source: "live" }
      : await api.indexStatus().then((r) => { store.setIndexStatus(r.data); return r; });
    railSrcSlot.replaceChildren(c.sourceBadge(idx.source));
    renderRail(idx.data);
  }

  function renderRail(idx) {
    const sources = (idx && idx.sources) || [];
    railBody.replaceChildren(
      h("div", { style: { transform: "scale(0.92)", "transform-origin": "top left", width: "108.5%" } },
        c.pillars(idx),
      ),
      h("div.lt3-stack-3",
        h("div.lt3-eyebrow", "Indexed sources"),
        sources.length
          ? h("div.lt3-stack-3", sources.map((s) => h("div.lt3-stack-2",
              h("div.lt3-row", { style: { "justify-content": "space-between" } },
                h("div.lt3-row-2", icon("database"), h("span", { style: { "font-size": "var(--lt3-text-sm)" } }, s.label)),
                c.statePill(s.state),
              ),
              c.meter(s.progress ?? (s.state === "indexed" ? 1 : 0.5), s.state === "indexing" ? "warn" : "vector"),
            )))
          : c.emptyState({ icon: "database-off", title: "No sources", body: "Connect a folder to ground answers." }),
      ),
      h("p.lt3-faint", { style: { "font-size": "var(--lt3-text-2xs)", "line-height": "var(--lt3-leading-normal)" } },
        "The next answer would fuse these sources via hybrid search and cite the entities and files it used."),
    );
  }
}
