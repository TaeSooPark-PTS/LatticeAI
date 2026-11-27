const path = require("path");

const { repoRoot, json, redirect, serveFile } = require("./http.cjs");
const { graphNodes } = require("./fixtures.cjs");

module.exports = function handleShell({ req, res, url, pathname }) {
  if (pathname === "/app" || pathname === "/v3") return serveFile(res, path.join(repoRoot, "static/app/index.html"));
  if (pathname === "/") return redirect(res, "/app#/account");
  if (pathname === "/workspace" || pathname === "/onboarding") return redirect(res, "/app#/workspace-admin");
  if (pathname === "/graph" || pathname === "/knowledge-graph") return redirect(res, "/app#/knowledge-graph");
  if (pathname === "/admin") return redirect(res, "/app#/admin/users");
  if (pathname === "/agents") return redirect(res, "/app#/agents");
  if (pathname === "/workflows") return redirect(res, "/app#/workflows");
  if (pathname === "/activity") return redirect(res, "/app#/activity");
  if (pathname === "/plugins/sdk") return redirect(res, "/app#/marketplace");
  if (pathname === "/chat" && req.method === "POST") {
    res.writeHead(200, { "content-type": "text/event-stream; charset=utf-8", "cache-control": "no-store", connection: "keep-alive" });
    const send = (obj) => res.write(`data: ${JSON.stringify(obj)}\n\n`);
    send({ chunk: "Hybrid retrieval ", model: "mock-local-model" });
    send({ chunk: "fuses the knowledge graph with the vector index, then reconciles the two ranked lists.", model: "mock-local-model" });
    send({ chunk: "", model: "mock-local-model", trace_id: "trace-mock", trace: {
      question: "", confidence: 0.9,
      graph_nodes: graphNodes.slice(0, 3).map((n) => ({ id: n.id, title: n.title, type: n.type })),
      source_files: [{ source: "notes/retrieval.md" }, { source: "config/index.yaml" }],
      vector_matches: [{ path: "notes/retrieval.md", score: 0.91 }, { path: "config/index.yaml", score: 0.74 }],
    } });
    res.write("data: [DONE]\n\n");
    res.end();
    return true;
  }
  if (pathname === "/api/command/briefing") return json(res, {
    sections: {
      knowledge: { available: true, recent: graphNodes.slice(0, 3).map((n) => ({ id: n.id, title: n.title })) },
      conversations: { available: true, questions: 4 },
      automations: { available: true, total: 3, enabled: 1, drafts: 2 },
      review: { available: true, pending: 2 },
      health: { available: true, grade: "B", score: 82 },
      suggestions: { available: true, count: 1, top: [] },
    },
    quick_actions: [{ id: "review-pending", kind: "review", count: 2, target: "/act/review" }],
  });
  if (pathname === "/history/conversations") return json(res, [
    { id: "conv-hybrid", title: "How hybrid search ranks", updated_at: "2026-06-06T13:20:00" },
    { id: "conv-reindex", title: "Reindex the workspace", updated_at: "2026-06-06T11:05:00" },
  ]);
  if (pathname.startsWith("/history/conversations/")) {
    if (req.method === "DELETE") return json(res, { removed: 1, kept: 0 });
    const id = pathname.slice("/history/conversations/".length);
    return json(res, { id, messages: [
      { role: "user", content: "How does hybrid search rank results?", timestamp: "2026-06-06T13:19:00" },
      { role: "assistant", content: "It fuses the vector index and the knowledge graph with **reciprocal-rank fusion**, so a strong hit in either modality surfaces.", timestamp: "2026-06-06T13:20:00" },
    ] });
  }
  if (pathname === "/chat") return redirect(res, "/app#/chat");
  if (pathname === "/account") return redirect(res, "/app#/account");
  if (pathname.startsWith("/static/")) return serveFile(res, path.join(repoRoot, pathname.slice(1)));
  if (pathname.startsWith("/icons/")) return serveFile(res, path.join(repoRoot, "static", pathname));
  if (pathname === "/manifest.json") return serveFile(res, path.join(repoRoot, "static/manifest.json"));
  if (pathname === "/favicon.ico") return serveFile(res, path.join(repoRoot, "static/favicon.ico"));
  if (pathname === "/sw.js") return serveFile(res, path.join(repoRoot, "static/sw.js"));

  if (pathname === "/account/profile") {
    if (req.method === "PATCH") return json(res, { email: "admin@example.com", nickname: "Admin", name: "Admin", role: "admin" });
    return json(res, { email: "admin@example.com", nickname: "Admin", name: "Admin", role: "admin" });
  }
  if (pathname === "/login" && req.method === "POST") return json(res, { status: "ok", email: "admin@example.com", nickname: "Admin", role: "admin" });
  if (pathname === "/register" && req.method === "POST") return json(res, { status: "ok", message: "registered", role: "user" });
  if (pathname === "/logout" && req.method === "POST") return json(res, { status: "ok" });
  if (pathname === "/account/change-password" && req.method === "POST") return json(res, { status: "ok" });
  if (pathname === "/auth/sso/config") return json(res, { enabled: false, providers: [] });
  return false;
};
