/* ============================================================================
 * View: Chat — a first-class v3 surface (NOT a preview that links out).
 *
 * Native to the /app shell: shares the design system, tokens, command palette,
 * workspace switcher, and mode model. Talks to the REAL backend
 * (POST /chat SSE + /history/* ) through the v3 adapter, and degrades to a
 * clearly-badged sample stream when no chat backend is available.
 *
 * Layout (flush, 3-pane): conversations · thread+composer · retrieval context
 * (Knowledge Graph · Vector · Hybrid Search · indexed file references).
 * ========================================================================== */

import { timeAgo } from "../core/dom.js";

export const layout = "flush";

export async function render(ctx) {
  const { h, icon, api, store, c, params, navigate, toast } = ctx;

  const state = {
    conversations: [], convSource: "pending",
    activeId: null, title: "New chat",
    messages: [],            // { role: "user"|"ai", content, source?, error? }
    streaming: false, abort: null,
    grounding: { graph: true, vector: true },
    model: "", modelSource: "pending",
    lastQuery: "", lastTrace: null,
    graphCache: null,
  };

  /* ── element hosts ───────────────────────────────────────────────────── */
  const listItems = h("div.lt3-chatlist__items", c.loading({ lines: 4 }));
  const listSrc = h("span", c.sourceBadge("pending"));
  const threadInner = h("div.lt3-chat__thread-inner");
  const thread = h("div.lt3-chat__thread", { id: "lt3-chat-thread", role: "log", "aria-live": "polite", "aria-label": "Conversation" }, threadInner);
  const titleEl = h("div.lt3-chat__title", state.title);
  const modelPill = h("span", c.pill("model", "info", { dot: true }));
  const barSrc = h("span", c.sourceBadge("pending"));
  const ctxBody = h("div.lt3-chat__context-body", c.loading({ lines: 5 }));
  const ctxSrc = h("span", c.sourceBadge("pending"));

  const textarea = h("textarea", {
    rows: "1", placeholder: "Message your workspace…  (Enter to send · Shift+Enter for newline)",
    "aria-label": "Message", autocomplete: "off",
    on: {
      input: autogrow,
      keydown: (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } },
    },
  });
  const sendBtn = h("button.lt3-btn.lt3-btn--primary", { "aria-label": "Send", on: { click: send } }, icon("arrow-up"));

  const groundChip = (key, label, icn) => h("button.lt3-chip", {
    type: "button", dataset: { active: String(state.grounding[key]) }, "aria-pressed": String(state.grounding[key]),
    title: `Toggle ${label} grounding`,
    on: { click: (e) => { state.grounding[key] = !state.grounding[key]; const b = e.currentTarget; b.dataset.active = String(state.grounding[key]); b.setAttribute("aria-pressed", String(state.grounding[key])); } },
  }, icon(icn), label);

  /* ── assembled shell ─────────────────────────────────────────────────── */
  const chat = h("div.lt3-chat", { dataset: { list: "closed", context: "closed" } },
    h("div.lt3-chat__scrim", { on: { click: closePanes } }),

    // Conversations rail
    h("aside.lt3-chatlist", { "aria-label": "Conversations" },
      h("div.lt3-chatlist__head",
        h("div.lt3-row", { style: { "justify-content": "space-between" } },
          h("div.lt3-eyebrow", "Conversations"),
          h("button.lt3-iconbtn.lt3-iconbtn--sm.lt3-chat__pane-close", { "aria-label": "Close conversations", on: { click: closePanes } }, icon("x")),
        ),
        h("button.lt3-btn.lt3-btn--ghost.lt3-btn--block", { on: { click: () => startNew(true) } }, icon("message-plus"), "New chat"),
        h("div.lt3-row", { style: { "justify-content": "space-between" } }, h("span.lt3-faint", { style: { "font-size": "var(--lt3-text-2xs)" } }, "History"), listSrc),
      ),
      listItems,
    ),

    // Main thread + composer
    h("div.lt3-chat__main",
      h("div.lt3-chat__bar",
        h("button.lt3-iconbtn.lt3-chat__toggle-list", { "aria-label": "Conversations", on: { click: () => togglePane("list") } }, icon("layout-sidebar")),
        h("div.lt3-avatar", { style: { background: "transparent", color: "var(--accent)" } }, icon("message-2")),
        titleEl,
        h("div.lt3-spacer"),
        modelPill,
        barSrc,
        h("button.lt3-iconbtn.lt3-chat__toggle-context", { "aria-label": "Retrieval context", on: { click: () => togglePane("context") } }, icon("layout-sidebar-right")),
        h("button.lt3-iconbtn", { "aria-label": "Open classic chat", title: "Open classic chat", on: { click: () => { window.location.href = "/chat"; } } }, icon("external-link")),
      ),
      thread,
      h("div.lt3-composer",
        h("div.lt3-composer__inner",
          h("div.lt3-composer__box", textarea, sendBtn),
          h("div.lt3-composer__tools",
            groundChip("graph", "Knowledge Graph", "chart-dots-3"),
            groundChip("vector", "Vector", "grid-dots"),
            h("span.lt3-spacer"),
            h("span.lt3-kbd", "↵"),
          ),
          h("div.lt3-composer__hint", "Answers are grounded in your local workspace via hybrid retrieval. Nothing leaves this machine."),
        ),
      ),
    ),

    // Retrieval context
    h("aside.lt3-chat__context", { "aria-label": "Retrieval context" },
      h("div.lt3-chat__context-head",
        h("div.lt3-eyebrow", icon("stack-2"), "Retrieval context"),
        h("div.lt3-row-2", ctxSrc, h("button.lt3-iconbtn.lt3-iconbtn--sm.lt3-chat__pane-close", { "aria-label": "Close context", on: { click: closePanes } }, icon("x"))),
      ),
      ctxBody,
    ),
  );

  /* ── boot ────────────────────────────────────────────────────────────── */
  loadModel();
  loadConversations();
  renderContext("");            // index-based defaults until the first answer
  if (params.new) startNew(false); else loadInitial();

  return chat;

  /* ── conversations ───────────────────────────────────────────────────── */
  async function loadConversations() {
    const res = await api.chatHistory();
    state.conversations = normalizeConversations(res.data);
    state.convSource = res.source;
    listSrc.replaceChildren(c.sourceBadge(res.source));
    renderConversations();
  }

  function renderConversations() {
    if (!state.conversations.length) {
      listItems.replaceChildren(c.emptyState({ icon: "message-off", title: "No conversations", body: "Start a new chat to begin." }));
      return;
    }
    listItems.replaceChildren(...state.conversations.map((conv) =>
      h("button.lt3-convo", {
        dataset: { active: String(conv.id === state.activeId) },
        on: { click: () => selectConversation(conv.id) },
      },
        icon("message"),
        h("div.lt3-convo__body",
          h("div.lt3-convo__title", conv.title || "Untitled"),
          conv.updated_at && h("div.lt3-convo__meta", timeAgo(conv.updated_at)),
        ),
        h("span.lt3-iconbtn.lt3-iconbtn--sm.lt3-convo__del", {
          role: "button", tabindex: "0", "aria-label": "Delete conversation",
          on: { click: (e) => { e.stopPropagation(); removeConversation(conv.id); } },
        }, icon("trash")),
      ),
    ));
  }

  async function selectConversation(id) {
    if (state.streaming) stopStreaming();
    closePanes();
    state.activeId = id;
    const conv = state.conversations.find((x) => x.id === id);
    state.title = conv ? conv.title : "Conversation";
    titleEl.textContent = state.title;
    state.lastTrace = null;
    renderConversations();
    threadInner.replaceChildren(c.loading({ lines: 4 }));
    const res = await api.conversation(id);
    if (state.activeId !== id) return;
    state.messages = (res.data || []).map((m) => ({
      role: m.role === "assistant" ? "ai" : "user",
      content: m.content || "",
      source: res.source,
    }));
    renderMessages();
    const lastUser = [...state.messages].reverse().find((m) => m.role === "user");
    renderContext(lastUser ? lastUser.content : "");
  }

  function startNew(userInitiated) {
    if (state.streaming) stopStreaming();
    closePanes();
    state.activeId = null;
    state.title = "New chat";
    state.messages = [];
    state.lastTrace = null;
    titleEl.textContent = state.title;
    renderConversations();
    renderMessages();
    renderContext("");
    if (userInitiated) { try { textarea.focus(); } catch {} navigate("chat", { new: "1" }); }
  }

  async function removeConversation(id) {
    await api.deleteConversation(id);
    state.conversations = state.conversations.filter((x) => x.id !== id);
    if (state.activeId === id) startNew(false);
    else renderConversations();
    toast("Conversation removed", "ok");
  }

  /* ── messages / thread ───────────────────────────────────────────────── */
  function renderMessages() {
    if (!state.messages.length) {
      threadInner.replaceChildren(emptyThread());
      return;
    }
    threadInner.replaceChildren(...state.messages.map((m) => messageNode(m)));
    scrollToBottom();
  }

  function emptyThread() {
    return h("div.lt3-empty", { style: { margin: "auto 0" } },
      h("div.lt3-empty__icon", icon("sparkles")),
      h("div.lt3-empty__title", "Ask anything about your workspace"),
      h("div.lt3-empty__body", "Grounded in your knowledge graph and vector index via hybrid retrieval. Try a question, or pick a starter below."),
      h("div.lt3-cluster", { style: { "justify-content": "center", "margin-top": "var(--lt3-space-2)" } },
        ...["How does hybrid search rank results?", "What entities are in my notes?", "Summarize retrieval.md"].map((q) =>
          h("button.lt3-chip", { on: { click: () => { textarea.value = q; autogrow(); send(); } } }, icon("arrow-up-right"), q)),
      ),
    );
  }

  function messageNode(m) {
    const isUser = m.role === "user";
    const body = h("div.lt3-msg__body",
      h("div.lt3-msg__bubble", m.content),
      m.role === "ai" && m.source && h("div.lt3-row-2", c.sourceBadge(m.source)),
    );
    return h(`div.lt3-msg.lt3-msg--${isUser ? "user" : "ai"}`,
      h("div.lt3-msg__avatar", icon(isUser ? "user" : "sparkles")),
      body,
    );
  }

  function scrollToBottom() { requestAnimationFrame(() => { thread.scrollTop = thread.scrollHeight; }); }

  function autogrow() {
    textarea.style.height = "auto";
    textarea.style.height = Math.min(200, textarea.scrollHeight) + "px";
  }

  /* ── send + stream ───────────────────────────────────────────────────── */
  async function send() {
    const text = textarea.value.trim();
    if (!text || state.streaming) return;
    textarea.value = ""; autogrow();

    if (!state.messages.length) threadInner.replaceChildren();
    const userMsg = { role: "user", content: text };
    state.messages.push(userMsg);
    threadInner.append(messageNode(userMsg));
    state.lastQuery = text;
    if (!state.activeId) { state.title = text.slice(0, 48); titleEl.textContent = state.title; }

    // streaming AI bubble
    const bubble = h("div.lt3-msg__bubble");
    const srcRow = h("div.lt3-row-2");
    const aiNode = h("div.lt3-msg.lt3-msg--ai",
      h("div.lt3-msg__avatar", icon("sparkles")),
      h("div.lt3-msg__body", bubble, srcRow),
    );
    bubble.append(typingIndicator());
    threadInner.append(aiNode);
    scrollToBottom();

    state.streaming = true;
    state.abort = new AbortController();
    setComposerStreaming(true);
    let started = false;

    const result = await api.streamChat(
      { message: text, conversation_id: state.activeId, grounding: state.grounding },
      {
        signal: state.abort.signal,
        onChunk: (_delta, full) => {
          if (!started) { started = true; bubble.replaceChildren(); }
          bubble.textContent = full;
          scrollToBottom();
        },
        onTrace: (trace) => { state.lastTrace = trace; renderContext(text); },
      },
    );

    state.streaming = false;
    state.abort = null;
    setComposerStreaming(false);

    if (result.aborted) {
      if (!result.text) { bubble.textContent = "(stopped)"; bubble.classList.add("lt3-faint"); }
      return;
    }
    if (!result.text) {
      aiNode.replaceWith(errorNode(text));
      return;
    }
    bubble.textContent = result.text;
    state.messages.push({ role: "ai", content: result.text, source: result.source });
    srcRow.replaceChildren(c.sourceBadge(result.source));
    if (!state.lastTrace) renderContext(text);
    refreshConversationMeta();
    scrollToBottom();
  }

  function errorNode(retryText) {
    return h("div.lt3-msg.lt3-msg--ai",
      h("div.lt3-msg__avatar", icon("alert-triangle")),
      h("div.lt3-msg__body",
        h("div.lt3-banner.lt3-banner--err",
          icon("alert-triangle"),
          h("div", h("div", { style: { fontWeight: 600 } }, "Couldn't reach the model"),
            h("div.lt3-faint", "The chat backend isn't responding. Check the local runtime and retry.")),
          h("button.lt3-btn.lt3-btn--subtle.lt3-btn--sm", { style: { "margin-left": "auto" }, on: { click: () => { textarea.value = retryText; autogrow(); send(); } } }, icon("refresh"), "Retry"),
        ),
      ),
    );
  }

  function typingIndicator() { return h("span.lt3-typing", { "aria-label": "Assistant is typing" }, h("i"), h("i"), h("i")); }

  function setComposerStreaming(on) {
    if (on) {
      sendBtn.replaceChildren(icon("player-stop"));
      sendBtn.setAttribute("aria-label", "Stop");
      sendBtn.onclick = stopStreaming;
    } else {
      sendBtn.replaceChildren(icon("arrow-up"));
      sendBtn.setAttribute("aria-label", "Send");
      sendBtn.onclick = send;
    }
  }

  function stopStreaming() { if (state.abort) { try { state.abort.abort(); } catch {} } }

  function refreshConversationMeta() {
    // New, unsaved conversation — reload the list so the backend-assigned id /
    // title appears once persisted.
    if (!state.activeId && state.messages.length) loadConversations();
  }

  /* ── retrieval context (KG · Vector · Hybrid · files) ────────────────── */
  async function renderContext(query) {
    const q = (query || "").trim();
    let hybrid = [], hybridSource = "pending";
    if (q) { const hs = await api.hybridSearch(q, { mode: groundingMode() }); hybrid = hs.data || []; hybridSource = hs.source; }

    if (!state.graphCache) state.graphCache = await api.graph();
    const graphNodes = (state.lastTrace && state.lastTrace.graph_nodes) ||
      ((state.graphCache.data.nodes || []).slice(0, 5).map((n) => ({ id: n.id, title: n.label || n.title, type: n.type })));
    const vectorMatches = (state.lastTrace && state.lastTrace.vector_matches) ||
      hybrid.map((r) => ({ path: r.path, score: r.vector }));
    const fileRefs = (state.lastTrace && state.lastTrace.source_files && state.lastTrace.source_files.map((s) => s.source)) ||
      [...new Set(hybrid.map((r) => r.path))];

    const overall = q ? hybridSource : state.graphCache.source;
    ctxSrc.replaceChildren(c.sourceBadge(overall));

    ctxBody.replaceChildren(
      ctxSection("Knowledge graph", "chart-dots-3",
        graphNodes.length
          ? graphNodes.slice(0, 6).map((n) => ctxItem("var(--lt3-pillar-graph)", n.title || n.id, n.type))
          : [ctxEmpty("No linked entities yet")]),

      ctxSection("Vector matches", "grid-dots",
        vectorMatches.length
          ? vectorMatches.slice(0, 5).map((v) => ctxItem("var(--lt3-pillar-vector)", v.path, v.score != null ? v.score.toFixed(2) : null))
          : [ctxEmpty("Run a query to see vector matches")]),

      ctxSection("Hybrid search", "arrows-join",
        hybrid.length
          ? hybrid.slice(0, 4).map((r) => ctxItem("var(--lt3-pillar-hybrid)", r.title || r.path, r.score != null ? r.score.toFixed(2) : null))
          : [ctxEmpty("Ask a question to fuse graph + vector")]),

      ctxSection("Indexed files", "files",
        fileRefs.length
          ? fileRefs.slice(0, 6).map((p) => ctxItem("var(--faint)", p, null))
          : [ctxEmpty("No file references yet")]),

      h("button.lt3-btn.lt3-btn--subtle.lt3-btn--sm.lt3-btn--block", { on: { click: () => navigate("hybrid-search", q ? { q } : undefined) } }, icon("arrows-join"), "Open Hybrid Search"),
    );
  }

  function ctxSection(title, icn, children) {
    return h("section",
      h("div.lt3-ctx-sec__title", icon(icn), title),
      h("div", children),
    );
  }
  function ctxItem(color, label, score) {
    return h("div.lt3-ctx-item",
      h("span.lt3-ctx-item__dot", { style: { background: color } }),
      h("span.lt3-ctx-item__label", { title: String(label) }, String(label)),
      score != null && h("span.lt3-ctx-item__score", String(score)),
    );
  }
  function ctxEmpty(text) { return h("div.lt3-faint", { style: { "font-size": "var(--lt3-text-xs)", padding: "var(--lt3-space-1) 0" } }, text); }

  function groundingMode() {
    if (state.grounding.graph && state.grounding.vector) return "hybrid";
    if (state.grounding.vector) return "vector";
    if (state.grounding.graph) return "graph";
    return "hybrid";
  }

  /* ── misc ────────────────────────────────────────────────────────────── */
  async function loadModel() {
    const res = await api.models();
    state.model = (res.data && res.data.current) || "local model";
    state.modelSource = res.source;
    modelPill.replaceChildren(c.pill(shortModel(state.model), "info", { dot: true }));
    barSrc.replaceChildren(c.sourceBadge(res.source));
  }

  function loadInitial() {
    // Land on the most recent conversation if one exists, else an empty thread.
    api.chatHistory().then((res) => {
      const list = normalizeConversations(res.data);
      if (list.length) selectConversation(list[0].id);
      else renderMessages();
    });
  }

  function togglePane(which) {
    const other = which === "list" ? "context" : "list";
    chat.dataset[other] = "closed";
    chat.dataset[which] = chat.dataset[which] === "open" ? "closed" : "open";
  }
  function closePanes() { chat.dataset.list = "closed"; chat.dataset.context = "closed"; }
}

/* ── helpers ─────────────────────────────────────────────────────────────── */
function normalizeConversations(data) {
  const list = Array.isArray(data) ? data : (data && Array.isArray(data.conversations) ? data.conversations : []);
  return list.map((conv, i) => ({
    id: conv.id || conv.conversation_id || `conv-${i}`,
    title: conv.title || conv.name || "Untitled",
    updated_at: conv.updated_at || conv.last_message_at || conv.timestamp || null,
  }));
}

function shortModel(id) {
  const s = String(id || "model");
  const tail = s.includes("/") ? s.split("/").pop() : s;
  return tail.length > 22 ? tail.slice(0, 21) + "…" : tail;
}
