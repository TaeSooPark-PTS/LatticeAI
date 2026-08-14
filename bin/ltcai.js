#!/usr/bin/env node

const { spawn, spawnSync } = require("node:child_process");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

const root = path.resolve(__dirname, "..");
const managedVenv = process.env.LTCAI_NPM_VENV || path.join(os.homedir(), ".ltcai", "npm-python");
const managedPython = process.platform === "win32"
  ? path.join(managedVenv, "Scripts", "python.exe")
  : path.join(managedVenv, "bin", "python");
const hostName = process.platform === "win32" ? "lattice-host.exe" : "lattice-host";
const WORKER_MODULE = "latticeai.worker_app";

const USAGE = `\
ltcai — Lattice AI front door (lattice-host)

USAGE:
    ltcai [OPTIONS]
    ltcai doctor

OPTIONS:
    --host <HOST>          Bind hint (sets LATTICEAI_HOST). lattice-host listens on 127.0.0.1
    --port <PORT>          Gateway port (default: LATTICEAI_PORT, then 4825)
    --worker-port <PORT>   Pin the worker port instead of scanning
    --no-spawn             Do not start a worker; front an existing one
    --no-jobs              Mount /host/jobs but never run the timer
    -h, --help             Print this help
    -V, --version          Print the version

lattice-host is the only front door. It supervises the Python AI worker
(\`python -m latticeai.worker_app\`). LATTICEAI_* environment variables are
passed through. Pin the host binary with LATTICEAI_HOST_BIN or LTCAI_HOST.
`;

function loadDotEnv(file) {
  if (!fs.existsSync(file)) return;
  for (const rawLine of fs.readFileSync(file, "utf8").split(/\r?\n/)) {
    const line = rawLine.trim();
    if (!line || line.startsWith("#") || !line.includes("=")) continue;
    const index = line.indexOf("=");
    const key = line.slice(0, index).trim();
    const value = line.slice(index + 1).trim().replace(/^["']|["']$/g, "");
    if (key && process.env[key] === undefined) process.env[key] = value;
  }
}

function applyExtraPath() {
  const extra = process.env.LATTICEAI_EXTRA_PATH;
  if (!extra) return;
  const sep = path.delimiter;
  const current = (process.env.PATH || "").split(sep).filter(Boolean);
  for (const item of extra.split(sep).filter(Boolean).reverse()) {
    const expanded = item.replace(/^~(?=$|\/|\\)/, os.homedir());
    if (fs.existsSync(expanded) && !current.includes(expanded)) current.unshift(expanded);
  }
  process.env.PATH = current.join(sep);
}

loadDotEnv(path.join(root, ".env"));
applyExtraPath();

function runSync(cmd, args, options = {}) {
  const result = spawnSync(cmd, args, { stdio: "inherit", ...options });
  return result.status === 0;
}

function canImport(python, moduleName) {
  const result = spawnSync(python, ["-c", `import ${moduleName}`], { stdio: "ignore" });
  return result.status === 0;
}

function ensureManagedPython() {
  if (process.env.LTCAI_SKIP_NPM_BOOTSTRAP === "1") return null;
  if (!fs.existsSync(managedPython)) {
    fs.mkdirSync(path.dirname(managedVenv), { recursive: true });
    const bootstrap = process.env.PYTHON || "python3";
    console.log(`[LTCAI] Creating Python environment at ${managedVenv}`);
    if (!runSync(bootstrap, ["-m", "venv", managedVenv])) return null;
  }

  if (!canImport(managedPython, "fastapi")) {
    const requirements = path.join(root, "requirements.txt");
    if (!fs.existsSync(requirements)) {
      console.error(`[LTCAI] Missing ${requirements}. The npm package is incomplete and cannot start the Python worker.`);
      process.exit(1);
    }
    console.log("[LTCAI] Installing Python dependencies. This can take a few minutes on first run.");
    if (!runSync(managedPython, ["-m", "pip", "install", "--upgrade", "pip"])) process.exit(1);
    if (!runSync(managedPython, ["-m", "pip", "install", "-r", requirements])) process.exit(1);
  }

  return managedPython;
}

function pythonCandidates(includeManaged) {
  return [
    process.env.LTCAI_PYTHON,
    includeManaged ? ensureManagedPython() : managedPython,
    path.join(root, ".venv", "bin", "python"),
    path.join(root, "venv", "bin", "python"),
    process.platform === "win32" ? path.join(root, ".venv", "Scripts", "python.exe") : null,
    process.env.PYTHON,
    "python3",
    "python",
  ].filter(Boolean);
}

function resolveWorkerPython(bootstrap) {
  for (const python of pythonCandidates(bootstrap)) {
    if (canImport(python, WORKER_MODULE)) return python;
  }
  return pythonCandidates(bootstrap)[0] || "python3";
}

function whichHost() {
  const tool = process.platform === "win32" ? "where" : "which";
  const result = spawnSync(tool, ["lattice-host"], { encoding: "utf8" });
  if (result.status !== 0) return null;
  const found = String(result.stdout || "").split(/\r?\n/).map((line) => line.trim()).find(Boolean);
  return found && fs.existsSync(found) ? found : null;
}

function existingHostBinary() {
  const pinned = process.env.LATTICEAI_HOST_BIN || process.env.LTCAI_HOST;
  if (pinned && fs.existsSync(pinned)) return pinned;
  const candidates = [
    path.join(root, "rust", "target", "release", hostName),
    path.join(root, "rust", "target", "debug", hostName),
    path.join(root, "src-tauri", "target", "release", hostName),
    path.join(root, "src-tauri", "target", "debug", hostName),
  ];
  for (const candidate of candidates) {
    if (fs.existsSync(candidate)) return candidate;
  }
  return whichHost();
}

function ensureHostBinary() {
  const existing = existingHostBinary();
  if (existing) return existing;
  if (process.env.LTCAI_SKIP_HOST_BUILD === "1") {
    console.error("[LTCAI] lattice-host binary not found and LTCAI_SKIP_HOST_BUILD=1.");
    process.exit(1);
  }
  const manifest = path.join(root, "rust", "Cargo.toml");
  if (!fs.existsSync(manifest)) {
    console.error("[LTCAI] lattice-host binary not found and rust/ workspace is missing.");
    process.exit(1);
  }
  console.log("[LTCAI] Building lattice-host (cargo build -p lattice-host)...");
  if (!runSync("cargo", ["build", "-p", "lattice-host"], { cwd: path.join(root, "rust") })) {
    console.error("[LTCAI] cargo build -p lattice-host failed.");
    process.exit(1);
  }
  const built = path.join(root, "rust", "target", "debug", hostName);
  if (!fs.existsSync(built)) {
    console.error(`[LTCAI] build succeeded but ${built} is missing.`);
    process.exit(1);
  }
  return built;
}

function parseArgs(argv) {
  const parsed = {
    help: false,
    version: false,
    doctor: false,
    host: null,
    port: null,
    hostArgs: [],
  };
  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    if (arg === "doctor") {
      parsed.doctor = true;
      continue;
    }
    if (arg === "-h" || arg === "--help") {
      parsed.help = true;
      continue;
    }
    if (arg === "-V" || arg === "--version") {
      parsed.version = true;
      continue;
    }
    if (arg === "--host" || arg.startsWith("--host=")) {
      parsed.host = arg === "--host" ? argv[++i] : arg.slice("--host=".length);
      continue;
    }
    if (arg === "--port" || arg.startsWith("--port=")) {
      parsed.port = arg === "--port" ? argv[++i] : arg.slice("--port=".length);
      if (parsed.port) parsed.hostArgs.push("--port", parsed.port);
      continue;
    }
    if (arg === "--reload" || arg === "--tunnel") {
      console.error(`[LTCAI] ${arg} is not supported on the lattice-host front door.`);
      continue;
    }
    parsed.hostArgs.push(arg);
  }
  return parsed;
}

function pinWorkerCommand() {
  if (process.env.LATTICEAI_DESKTOP_BACKEND_CMD) return;
  const python = resolveWorkerPython(true);
  process.env.LATTICEAI_DESKTOP_BACKEND_CMD = `${python} -m ${WORKER_MODULE}`;
}

function doctor() {
  let ok = true;
  const host = existingHostBinary();
  if (host) {
    console.log(`[OK] lattice-host: ${host}`);
  } else {
    console.log("[MISS] lattice-host: not built (run without doctor to cargo-build it)");
    ok = false;
  }
  const python = resolveWorkerPython(false);
  if (canImport(python, WORKER_MODULE)) {
    console.log(`[OK] worker: ${python} -m ${WORKER_MODULE}`);
  } else {
    console.log(`[MISS] worker: cannot import ${WORKER_MODULE} (${python})`);
    ok = false;
  }
  process.exit(ok ? 0 : 1);
}

function launchHost(bin, args) {
  const child = spawn(bin, args, { cwd: root, stdio: "inherit" });
  child.on("error", (err) => {
    console.error(`[LTCAI] failed to launch lattice-host (${bin}): ${err.message}`);
    process.exit(1);
  });
  child.on("exit", (code, signal) => {
    if (signal) process.kill(process.pid, signal);
    process.exit(code ?? 0);
  });
}

const parsed = parseArgs(process.argv.slice(2));

if (parsed.help) {
  process.stdout.write(USAGE);
  const host = existingHostBinary();
  if (host) {
    process.stdout.write("\n");
    spawnSync(host, ["--help"], { stdio: "inherit" });
  }
  process.exit(0);
}

if (parsed.version) {
  const host = existingHostBinary();
  if (host) {
    spawnSync(host, ["--version"], { stdio: "inherit" });
  } else {
    console.log("ltcai (lattice-host front door)");
  }
  process.exit(0);
}

if (parsed.doctor) {
  doctor();
}

if (parsed.host) process.env.LATTICEAI_HOST = parsed.host;
if (parsed.port) process.env.LATTICEAI_PORT = parsed.port;

pinWorkerCommand();
launchHost(ensureHostBinary(), parsed.hostArgs);
