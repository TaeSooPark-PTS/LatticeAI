const assert = require("node:assert/strict");
const { execFile } = require("node:child_process");
const http = require("node:http");
const path = require("node:path");
const test = require("node:test");

const repo = path.resolve(__dirname, "..");
const cli = path.join(repo, "bin", "ltcai.js");
const { parseSearchArgs, serverBase, formatResults } = require("../bin/search.js");

function ltcai(args, env = {}) {
  return new Promise((resolve) => {
    execFile(
      process.execPath,
      [cli, "search", ...args],
      { env: { ...process.env, LATTICEAI_HOST: "127.0.0.1", LATTICEAI_API_TOKEN: "", ...env } },
      (error, stdout, stderr) => resolve({ code: error ? error.code : 0, stdout, stderr }),
    );
  });
}

async function mockServer(handler) {
  const seen = [];
  const server = http.createServer((req, res) => {
    seen.push({ url: new URL(req.url, "http://mock"), headers: req.headers, method: req.method });
    handler(req, res);
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const port = String(server.address().port);
  const close = () => new Promise((resolve) => server.close(resolve));
  return { port, seen, close };
}

function reply(status, body, headers = {}) {
  return (_req, res) => {
    res.writeHead(status, { "content-type": "application/json", ...headers });
    res.end(typeof body === "string" ? body : JSON.stringify(body));
  };
}

const results = {
  sort_by: "newest",
  matches: [
    { title: "Refund policy v2", type: "Document", summary: "30 days", updated_at: "2026-11-18T09:00:00", status: "active" },
    { title: "Refund\u001b[31m meeting", type: "Document", summary: "moved", updated_at: "2026-06-02T09:00:00" },
  ],
};

test("search prints ranked results with timestamps and sends sort, limit and token", async () => {
  const server = await mockServer(reply(200, results));
  const run = await ltcai(["refund", "policy", "--sort", "newest", "--limit=5"], {
    LATTICEAI_PORT: server.port,
    LATTICEAI_API_TOKEN: "test-session-value",
  });
  await server.close();
  assert.equal(run.code, 0, run.stderr);
  const [request] = server.seen;
  assert.equal(request.method, "GET");
  assert.equal(request.url.pathname, "/api/search/hybrid");
  assert.equal(request.url.searchParams.get("q"), "refund policy");
  assert.equal(request.url.searchParams.get("sort_by"), "newest");
  assert.equal(request.url.searchParams.get("limit"), "5");
  assert.equal(request.headers.authorization, "Bearer test-session-value");
  assert.match(run.stdout, /sorted by newest/);
  assert.match(run.stdout, / 1\. 2026-11-18 09:00:00 {2}Refund policy v2 \[Document\]/);
  assert.match(run.stdout, / 2\. 2026-06-02 09:00:00/);
  assert.ok(!run.stdout.includes("\u001b"));
  assert.ok(!`${run.stdout}${run.stderr}`.includes("test-session-value"));
});

test("without --sort or a token neither is sent, and --port wins over the environment", async () => {
  const server = await mockServer(reply(200, { matches: [] }));
  const run = await ltcai(["anything", "--port", server.port], { LATTICEAI_PORT: "1" });
  await server.close();
  assert.equal(run.code, 0, run.stderr);
  assert.equal(server.seen[0].url.searchParams.has("sort_by"), false);
  assert.equal(server.seen[0].url.searchParams.get("limit"), "10");
  assert.equal(server.seen[0].headers.authorization, undefined);
  assert.equal(run.stdout, "No results.\n");
});

test("authentication failures are explained without echoing the token", async () => {
  const server = await mockServer(reply(401, { detail: "nope" }));
  const without = await ltcai(["x"], { LATTICEAI_PORT: server.port });
  const rejected = await ltcai(["x"], { LATTICEAI_PORT: server.port, LATTICEAI_API_TOKEN: "stale-value" });
  await server.close();
  assert.equal(without.code, 4);
  assert.match(without.stderr, /Authentication failed \(HTTP 401\)\. Set LATTICEAI_API_TOKEN/);
  assert.equal(rejected.code, 4);
  assert.match(rejected.stderr, /was rejected/);
  assert.ok(!rejected.stderr.includes("stale-value"));
});

test("a stopped server is reported as unreachable", async () => {
  const server = await mockServer(reply(200, results));
  await server.close();
  const run = await ltcai(["x"], { LATTICEAI_PORT: server.port });
  assert.equal(run.code, 3);
  assert.match(run.stderr, /Cannot reach Lattice AI at http:\/\/127\.0\.0\.1:\d+ \(ECONNREFUSED\)/);
});

test("server errors, foreign answers and redirects fail clearly", async () => {
  const refused = await mockServer(reply(422, { detail: [{ msg: "Input should be 'relevance'" }] }));
  const invalid = await ltcai(["x"], { LATTICEAI_PORT: refused.port });
  await refused.close();
  assert.equal(invalid.code, 1);
  assert.match(invalid.stderr, /HTTP 422\): Input should be 'relevance'/);

  const foreign = await mockServer(reply(200, "<html></html>"));
  const html = await ltcai(["x"], { LATTICEAI_PORT: foreign.port });
  await foreign.close();
  assert.equal(html.code, 1);
  assert.match(html.stderr, /did not answer with Lattice AI search results/);

  const moved = await mockServer(reply(302, "", { location: "http://127.0.0.1:1/" }));
  const redirected = await ltcai(["x"], { LATTICEAI_PORT: moved.port });
  await moved.close();
  assert.equal(redirected.code, 3);
});

test("bad arguments exit 2 with usage and never call the server", async () => {
  const server = await mockServer(reply(200, results));
  const cases = [
    [[], /missing search query/],
    [["x", "--sort", "oldest"], /--sort must be one of relevance, newest/],
    [["x", "--limit", "0"], /--limit must be a whole number/],
    [["x", "--limit", "2.5"], /--limit must be a whole number/],
    [["x", "--limit"], /--limit needs a value/],
    [["x", "--wat"], /unknown option --wat/],
    [["x", "--port", "99999"], /port must be a number/],
    [["x", "--host", "a/b"], /host must be a bare host/],
  ];
  for (const [args, message] of cases) {
    const run = await ltcai(args, { LATTICEAI_PORT: server.port });
    assert.equal(run.code, 2, `${args}`);
    assert.match(run.stderr, message);
    assert.match(run.stderr, /USAGE:/);
  }
  const help = await ltcai(["--help"]);
  await server.close();
  assert.equal(help.code, 0);
  assert.match(help.stdout, /ltcai search <query>/);
  assert.equal(server.seen.length, 0);
});

test("argument and address helpers cover the edge forms", () => {
  assert.equal(parseSearchArgs(["--", "--sort"]).query, "--sort");
  assert.equal(serverBase({ host: null, port: null }, {}), "http://127.0.0.1:4825");
  assert.equal(serverBase({ host: null, port: null }, { LATTICEAI_HOST: "0.0.0.0" }), "http://127.0.0.1:4825");
  assert.equal(serverBase({ host: "::1", port: "9" }, {}), "http://[::1]:9");
  assert.match(formatResults({ matches: [{ id: "n1", status: "superseded" }] }), /\(no timestamp\) {2}n1 \(superseded\)/);
});

test("the npm package ships the search module", () => {
  const manifest = require("../package.json");
  assert.ok(manifest.files.includes("bin/search.js"));
});
