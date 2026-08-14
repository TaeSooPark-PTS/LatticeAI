#!/usr/bin/env node
/**
 * Start an isolated Lattice product server and run Playwright first-value E2E
 * against it (Wave 3.2 residual / v9.9.5).
 *
 * v11.6.0 "One Door": the front door is `lattice-host`, which supervises the
 * Python AI worker (`latticeai.worker_app`) on an internal port behind it.
 * This runner used to start `uvicorn server:app` — a root `server.py` the
 * release deleted — so it now boots the same binary the product ships.
 *
 * Mirrors scripts/run_integration_tests.mjs isolation rules: never touch
 * the developer's real HOME / ~/.ltcai / Brain vault.
 */
import { spawn, spawnSync } from "node:child_process";
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const host = process.env.LTCAI_E2E_HOST || "127.0.0.1";
if (!new Set(["127.0.0.1", "localhost"]).has(host)) {
  throw new Error(`LTCAI_E2E_HOST must be loopback, received: ${host}`);
}
const port = process.env.LTCAI_E2E_PORT || "4899";
if (!/^\d+$/.test(port) || Number(port) < 1 || Number(port) > 65535) {
  throw new Error(`LTCAI_E2E_PORT must be 1–65535, received: ${port}`);
}
const baseUrl = `http://${host}:${port}`;
const venvPython = join(process.cwd(), ".venv", "bin", "python");
const requestedPython = process.env.PYTHON || (existsSync(venvPython) ? venvPython : "python3");

/**
 * The interpreter, as an absolute path.
 *
 * `LTCAI_PYTHON` is handed to `lattice-host`, which spawns the worker with a
 * `PATH` **prefixed** by `/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin`
 * (supervisor/worker_env.rs — GUI processes inherit a minimal PATH). A bare
 * name like `python3` is therefore looked up against a different PATH than the
 * one this runner validated it on: on GitHub Actions the interpreter that
 * `pip install -e "."` populated lives under /opt/hostedtoolcache, but the
 * child finds /usr/bin/python3 first and dies with "No module named uvicorn"
 * — the worker exit 1 that turned every proxied route into a 502. Resolving to
 * `sys.executable` means the interpreter cannot change identity when it
 * crosses the process boundary.
 */
function resolveInterpreter(candidate) {
  const probe = spawnSync(candidate, ["-c", "import sys; print(sys.executable)"], {
    encoding: "utf8",
  });
  const resolved = probe.status === 0 ? String(probe.stdout || "").trim() : "";
  // An unresolvable candidate is passed through untouched so the supervisor
  // reports the failure in its own words rather than this helper's.
  return resolved && existsSync(resolved) ? resolved : candidate;
}

const python = resolveInterpreter(requestedPython);

/**
 * The `lattice-host` binary this run should exercise.
 *
 * An explicit pin wins (same names bin/ltcai.js accepts). Otherwise the
 * current tree is built: a stale `rust/target/release/lattice-host` left over
 * from an older version would otherwise be tested instead of this checkout.
 * `cargo build` is a no-op when the tree is already built.
 */
function resolveHostBinary() {
  const pinned = process.env.LATTICEAI_HOST_BIN || process.env.LTCAI_HOST;
  if (pinned) {
    if (!existsSync(pinned)) throw new Error(`lattice-host not found at ${pinned}`);
    return pinned;
  }
  const built = join(process.cwd(), "rust", "target", "debug", "lattice-host");
  if (process.env.LTCAI_SKIP_HOST_BUILD !== "1") {
    console.log("[e2e] cargo build -p lattice-host");
    const build = spawnSync("cargo", ["build", "-p", "lattice-host"], {
      stdio: "inherit",
      cwd: join(process.cwd(), "rust"),
    });
    if (build.status !== 0) throw new Error("cargo build -p lattice-host failed");
  }
  if (!existsSync(built)) throw new Error(`lattice-host missing: ${built}`);
  return built;
}

const sandboxRoot = mkdtempSync(join(tmpdir(), "ltcai-e2e-"));
const sandbox = {
  home: join(sandboxRoot, "home"),
  data: join(sandboxRoot, "data"),
  brain: join(sandboxRoot, "brain"),
  agent: join(sandboxRoot, "agent-workspace"),
  vault: join(sandboxRoot, "vault"),
  cache: join(sandboxRoot, "cache"),
  config: join(sandboxRoot, "config"),
  tmp: join(sandboxRoot, "tmp"),
};
for (const path of Object.values(sandbox)) mkdirSync(path, { recursive: true });

const isolatedEnv = {
  ...process.env,
  HOME: sandbox.home,
  USERPROFILE: sandbox.home,
  XDG_CACHE_HOME: sandbox.cache,
  XDG_CONFIG_HOME: sandbox.config,
  XDG_DATA_HOME: join(sandboxRoot, "share"),
  TMPDIR: sandbox.tmp,
  TEMP: sandbox.tmp,
  TMP: sandbox.tmp,
  LATTICEAI_MODE: "local",
  LATTICEAI_HOST: host,
  LATTICEAI_PORT: port,
  LATTICEAI_DATA_DIR: sandbox.data,
  LATTICEAI_BRAIN_DIR: sandbox.brain,
  LATTICEAI_AGENT_ROOT: sandbox.agent,
  LATTICEAI_OBSIDIAN_VAULT_DIR: sandbox.vault,
  // The supervisor resolves the worker interpreter itself; naming it keeps
  // the choice the same one this runner made (repo .venv, else python3).
  LTCAI_PYTHON: python,
  LATTICEAI_STORAGE_ENGINE: "sqlite",
  LATTICEAI_POSTGRES_DSN: "",
  LATTICEAI_REQUIRE_AUTH: "false",
  LATTICEAI_ENABLE_TELEGRAM: "false",
  LATTICEAI_AUTOLOAD_MODELS: "false",
  LATTICEAI_ALLOW_MODEL_DOWNLOADS: "false",
  LATTICEAI_AUTO_READ_CHAT_PATHS: "false",
  LATTICEAI_TUNNEL: "false",
  LATTICEAI_DISCORD_PERMISSION_WEBHOOK: "",
  LATTICEAI_DISCORD_BOT_TOKEN: "",
  OIDC_DISCOVERY_URL: "",
  PYTHON_KEYRING_BACKEND: "keyring.backends.null.Keyring",
  LTCAI_E2E_BASE_URL: baseUrl,
};

let sandboxCleaned = false;
function cleanupSandbox() {
  if (sandboxCleaned) return;
  sandboxCleaned = true;
  rmSync(sandboxRoot, { recursive: true, force: true });
}
process.once("exit", cleanupSandbox);

/**
 * The supervised worker's own stdout/stderr.
 *
 * `lattice-host` redirects them into `$HOME/.ltcai/desktop-sidecar.log` and
 * `.err.log` (supervisor/worker_env.rs), and this runner rewrites HOME into a
 * sandbox it deletes on the way out — so a worker that dies takes the only
 * explanation with it and CI shows nothing but a 502. Print the tail before
 * cleanup: those lines are the difference between "the gateway returned 502"
 * and "that interpreter has no uvicorn".
 */
function dumpWorkerLogs() {
  for (const name of ["desktop-sidecar.err.log", "desktop-sidecar.log"]) {
    const path = join(sandbox.home, ".ltcai", name);
    if (!existsSync(path)) {
      console.error(`[e2e] ${name}: not written`);
      continue;
    }
    const lines = readFileSync(path, "utf8").replace(/\n+$/, "").split("\n");
    const tail = lines.slice(-80);
    console.error(`[e2e] ---- ${name} (last ${tail.length} of ${lines.length} lines) ----`);
    console.error(tail.join("\n") || "(empty)");
  }
}

function delay(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

// 120 s, not 60: the gate now covers two boots — this process binds the
// gateway immediately, but /health is proxied and only answers once the
// supervised Python worker has imported and bound its own port.
async function waitForHealth(url, timeoutMs = 120000) {
  const deadline = Date.now() + timeoutMs;
  let lastError = "";
  while (Date.now() < deadline) {
    try {
      const response = await fetch(`${url}/health`, { signal: AbortSignal.timeout(3000) });
      if (response.ok) return;
      lastError = `HTTP ${response.status}`;
    } catch (error) {
      lastError = String(error?.message || error);
    }
    await delay(500);
  }
  throw new Error(`Timed out waiting for ${url}/health: ${lastError}`);
}

function stop(child) {
  return new Promise((resolve) => {
    if (child.exitCode !== null || child.signalCode !== null) {
      resolve();
      return;
    }
    const timer = setTimeout(() => {
      if (child.exitCode === null && child.signalCode === null) child.kill("SIGKILL");
    }, 5000);
    child.once("close", () => {
      clearTimeout(timer);
      resolve();
    });
    child.kill("SIGTERM");
  });
}

async function main() {
  console.log(`[e2e] sandbox ${sandboxRoot}`);
  console.log(`[e2e] worker interpreter ${python}`);
  const hostBinary = resolveHostBinary();
  console.log(`[e2e] starting ${hostBinary} at ${baseUrl}`);
  // The shipped topology: lattice-host serves the product on `port` and
  // supervises the Python worker on a free port behind it. Do not
  // double-append /health — the wait helper already probes `${url}/health`,
  // which the gateway proxies to the worker, so a 200 there means both
  // processes are up.
  const server = spawn(
    hostBinary,
    ["--port", port],
    { stdio: "inherit", env: isolatedEnv, cwd: process.cwd() },
  );

  let exitCode = 1;
  try {
    await waitForHealth(baseUrl);
    console.log("[e2e] sidecar healthy — running Playwright");
    // Playwright must use the host browser install (npx playwright install).
    // The sidecar isolation rewrites HOME/XDG_* into a disposable sandbox —
    // reusing that env here makes Chromium look under /tmp/.../ms-playwright
    // and fail with "Executable doesn't exist".
    const playwrightEnv = {
      ...process.env,
      LTCAI_E2E_BASE_URL: baseUrl,
    };
    const playwright = spawn(
      "npx",
      ["playwright", "test", "-c", "playwright.e2e.config.js"],
      { stdio: "inherit", env: playwrightEnv, cwd: process.cwd() },
    );
    exitCode = await new Promise((resolve, reject) => {
      playwright.on("error", reject);
      playwright.on("close", (code) => resolve(code ?? 1));
    });
  } catch (error) {
    console.error("[e2e] failed:", error);
    exitCode = 1;
  } finally {
    if (exitCode !== 0) dumpWorkerLogs();
    await stop(server);
    cleanupSandbox();
  }
  process.exit(exitCode);
}

main();
