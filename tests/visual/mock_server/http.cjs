/**
 * Response writers for the visual mock.
 *
 * Each one returns `true`. The route modules are one long `if` chain that used
 * to live inside `http.createServer`, where `return json(res, …)` simply ended
 * the handler; now the same statement has to tell the entry "answered, stop
 * here", so the writers report that themselves and every branch keeps its shape.
 */
const fs = require("fs");
const path = require("path");

const repoRoot = path.resolve(__dirname, "../../..");

function json(res, value, status = 200) {
  const body = JSON.stringify(value);
  res.writeHead(status, {
    "content-type": "application/json; charset=utf-8",
    "cache-control": "no-store",
  });
  res.end(body);
  return true;
}

function text(res, value, contentType = "text/plain; charset=utf-8") {
  res.writeHead(200, { "content-type": contentType, "cache-control": "no-store" });
  res.end(value);
  return true;
}

function redirect(res, target) {
  res.writeHead(308, { location: target, "cache-control": "no-store" });
  res.end();
  return true;
}

function serveFile(res, filePath) {
  if (!filePath.startsWith(repoRoot) || !fs.existsSync(filePath) || !fs.statSync(filePath).isFile()) {
    json(res, { detail: "not found" }, 404);
    return true;
  }
  const ext = path.extname(filePath).toLowerCase();
  const types = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".png": "image/png",
    ".ico": "image/x-icon",
    ".svg": "image/svg+xml",
  };
  res.writeHead(200, { "content-type": types[ext] || "application/octet-stream", "cache-control": "no-store" });
  fs.createReadStream(filePath).pipe(res);
  return true;
}

module.exports = { repoRoot, json, text, redirect, serveFile };
