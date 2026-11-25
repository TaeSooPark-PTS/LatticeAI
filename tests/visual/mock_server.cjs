const http = require("http");

const { json, text } = require("./mock_server/http.cjs");
const { port } = require("./mock_server/fixtures.cjs");

const ROUTES = [
  require("./mock_server/routes_shell.cjs"),
  require("./mock_server/routes_platform.cjs"),
  require("./mock_server/routes_models.cjs"),
  require("./mock_server/routes_agents.cjs"),
  require("./mock_server/routes_knowledge.cjs"),
  require("./mock_server/routes_chronicle.cjs"),
  require("./mock_server/routes_admin.cjs"),
];

const server = http.createServer((req, res) => {
  const url = new URL(req.url, `http://127.0.0.1:${port}`);
  const pathname = decodeURIComponent(url.pathname);
  const ctx = { req, res, url, pathname };

  for (const handle of ROUTES) {
    if (handle(ctx)) return;
  }

  if (req.method === "POST" || req.method === "PATCH" || req.method === "DELETE") return json(res, { status: "ok" });
  text(res, "not found", "text/plain; charset=utf-8");
});

server.listen(port, "127.0.0.1", () => {
  console.log(`Lattice AI visual mock server listening on http://127.0.0.1:${port}`);
});
