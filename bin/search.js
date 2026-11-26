const DEFAULT_PORT = "4825";
const DEFAULT_LIMIT = 10;
const MAX_LIMIT = 100;
const TIMEOUT_MS = 30000;
const SORTS = ["relevance", "newest"];
const EXIT = { ok: 0, failed: 1, usage: 2, unreachable: 3, auth: 4 };

const SEARCH_USAGE = `\
ltcai search — query a running Lattice AI server

USAGE:
    ltcai search <query> [OPTIONS]

OPTIONS:
    --sort <relevance|newest>   Result order (default: chosen by the server from the query)
    --limit <n>                 Maximum results, 1-${MAX_LIMIT} (default: ${DEFAULT_LIMIT})
    --host <HOST>               Server host (default: LATTICEAI_HOST, then 127.0.0.1)
    --port <PORT>               Server port (default: LATTICEAI_PORT, then ${DEFAULT_PORT})
    -h, --help                  Print this help

Authentication uses the LATTICEAI_API_TOKEN environment variable as a Bearer token.
`;

class UsageError extends Error {}

function parseSearchArgs(argv) {
  const parsed = { help: false, words: [], sort: null, limit: DEFAULT_LIMIT, host: null, port: null };
  let literal = false;
  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    if (literal || !arg.startsWith("-")) {
      parsed.words.push(arg);
      continue;
    }
    if (arg === "--") {
      literal = true;
      continue;
    }
    if (arg === "-h" || arg === "--help") {
      parsed.help = true;
      continue;
    }
    const split = arg.indexOf("=");
    const flag = split === -1 ? arg : arg.slice(0, split);
    if (!["--sort", "--limit", "--host", "--port"].includes(flag)) {
      throw new UsageError(`unknown option ${flag}`);
    }
    const value = split === -1 ? argv[++i] : arg.slice(split + 1);
    if (value === undefined || value === "" || (split === -1 && value.startsWith("--"))) {
      throw new UsageError(`${flag} needs a value`);
    }
    parsed[flag.slice(2)] = value;
  }
  if (parsed.help) return parsed;
  parsed.query = parsed.words.join(" ").trim();
  if (!parsed.query) throw new UsageError("missing search query");
  if (parsed.sort !== null && !SORTS.includes(parsed.sort)) {
    throw new UsageError(`--sort must be one of ${SORTS.join(", ")}`);
  }
  const limit = Number(parsed.limit);
  if (!Number.isInteger(limit) || limit < 1 || limit > MAX_LIMIT) {
    throw new UsageError(`--limit must be a whole number from 1 to ${MAX_LIMIT}`);
  }
  parsed.limit = limit;
  return parsed;
}

function serverBase(parsed, env) {
  let host = parsed.host || env.LATTICEAI_HOST || "127.0.0.1";
  if (host === "0.0.0.0" || host === "::") host = "127.0.0.1";
  if (host.includes(":") && !host.startsWith("[")) host = `[${host}]`;
  const port = String(parsed.port || env.LATTICEAI_PORT || DEFAULT_PORT);
  if (!/^\d{1,5}$/.test(port) || Number(port) < 1 || Number(port) > 65535) {
    throw new UsageError("port must be a number from 1 to 65535");
  }
  if (/[/@?#\s\\]/.test(host)) throw new UsageError("host must be a bare host name or address");
  return `http://${host}:${port}`;
}

function clean(value, max) {
  const text = String(value ?? "")
    .replace(/[\u0000-\u001f\u007f-\u009f​-‏‪-‮⁦-⁩]+/g, " ")
    .replace(/\s+/g, " ")
    .trim();
  return text.length > max ? `${text.slice(0, max - 1)}…` : text;
}

function timestampOf(match) {
  const raw = clean(match.updated_at, 40);
  return raw ? raw.replace("T", " ").slice(0, 19) : "(no timestamp)";
}

function formatResults(payload) {
  const matches = Array.isArray(payload.matches) ? payload.matches : [];
  if (matches.length === 0) return "No results.\n";
  const lines = [`${matches.length} result(s), sorted by ${clean(payload.sort_by, 20) || "relevance"}`];
  matches.forEach((match, index) => {
    const title = clean(match.title || match.node_id || match.id, 100) || "(untitled)";
    const type = clean(match.type, 30);
    const state = match.status === "superseded" ? " (superseded)" : "";
    lines.push(`${String(index + 1).padStart(2)}. ${timestampOf(match)}  ${title}${type ? ` [${type}]` : ""}${state}`);
    const summary = clean(match.summary, 160);
    if (summary && summary !== title) lines.push(`    ${summary}`);
  });
  return `${lines.join("\n")}\n`;
}

function errorDetail(body) {
  try {
    const detail = JSON.parse(body).detail;
    if (typeof detail === "string") return clean(detail, 200);
    if (Array.isArray(detail)) return clean(detail.map((entry) => entry.msg).join("; "), 200);
  } catch {
    return "";
  }
  return "";
}

async function runSearch(argv, env = process.env, io = process) {
  let parsed;
  let base;
  try {
    parsed = parseSearchArgs(argv);
    if (parsed.help) {
      io.stdout.write(SEARCH_USAGE);
      return EXIT.ok;
    }
    base = serverBase(parsed, env);
  } catch (error) {
    if (!(error instanceof UsageError)) throw error;
    io.stderr.write(`[LTCAI] ${error.message}\n\n${SEARCH_USAGE}`);
    return EXIT.usage;
  }

  const params = new URLSearchParams({ q: parsed.query, limit: String(parsed.limit) });
  if (parsed.sort) params.set("sort_by", parsed.sort);
  const headers = { Accept: "application/json" };
  const token = String(env.LATTICEAI_API_TOKEN || "").trim();
  if (token) {
    if (!/^[\x21-\x7e]+$/.test(token)) {
      io.stderr.write("[LTCAI] LATTICEAI_API_TOKEN contains characters that cannot be sent in a header.\n");
      return EXIT.auth;
    }
    headers.Authorization = `Bearer ${token}`;
  }

  let response;
  let body;
  try {
    response = await fetch(`${base}/api/search/hybrid?${params}`, {
      headers,
      redirect: "error",
      signal: AbortSignal.timeout(TIMEOUT_MS),
    });
    body = await response.text();
  } catch (error) {
    const reason = error.name === "TimeoutError" ? "the request timed out" : error.cause?.code || "connection failed";
    io.stderr.write(`[LTCAI] Cannot reach Lattice AI at ${base} (${reason}). Start the server with \`ltcai\`, or pass --host/--port.\n`);
    return EXIT.unreachable;
  }

  if (response.status === 401 || response.status === 403) {
    const hint = token
      ? "LATTICEAI_API_TOKEN was rejected; sign in again and update it."
      : "Set LATTICEAI_API_TOKEN to a valid session token.";
    io.stderr.write(`[LTCAI] Authentication failed (HTTP ${response.status}). ${hint}\n`);
    return EXIT.auth;
  }
  if (!response.ok) {
    const detail = errorDetail(body);
    io.stderr.write(`[LTCAI] Search failed (HTTP ${response.status})${detail ? `: ${detail}` : ""}.\n`);
    return EXIT.failed;
  }
  let payload;
  try {
    payload = JSON.parse(body);
  } catch {
    payload = null;
  }
  if (!payload || !Array.isArray(payload.matches)) {
    io.stderr.write(`[LTCAI] ${base} did not answer with Lattice AI search results.\n`);
    return EXIT.failed;
  }
  io.stdout.write(formatResults(payload));
  return EXIT.ok;
}

module.exports = { runSearch, parseSearchArgs, serverBase, formatResults, EXIT, SEARCH_USAGE };
