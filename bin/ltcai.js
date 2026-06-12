#!/usr/bin/env node

const { spawn } = require("node:child_process");
const { spawnSync } = require("node:child_process");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

const root = path.resolve(__dirname, "..");
const managedVenv = process.env.LTCAI_NPM_VENV || path.join(os.homedir(), ".ltcai", "npm-python");
const managedPython = process.platform === "win32"
  ? path.join(managedVenv, "Scripts", "python.exe")
  : path.join(managedVenv, "bin", "python");

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
      console.error(`[LTCAI] Missing ${requirements}. The npm package is incomplete and cannot start the Python server.`);
      process.exit(1);
    }
    console.log("[LTCAI] Installing Python dependencies. This can take a few minutes on first run.");
    if (!runSync(managedPython, ["-m", "pip", "install", "--upgrade", "pip"])) process.exit(1);
    if (!runSync(managedPython, ["-m", "pip", "install", "-r", requirements])) process.exit(1);
  }

  return managedPython;
}

const bootstrappedPython = ensureManagedPython();
const candidates = [
  process.env.LTCAI_PYTHON,
  bootstrappedPython,
  path.join(root, ".venv", "bin", "python"),
  path.join(root, "venv", "bin", "python"),
  process.env.PYTHON,
  "python3",
  "python",
].filter(Boolean);

function run(index) {
  const python = candidates[index];
  if (!python) {
    console.error("Could not find Python. Install Python 3.11+ and try again.");
    process.exit(1);
  }

  const child = spawn(python, [path.join(root, "ltcai_cli.py"), ...process.argv.slice(2)], {
    cwd: root,
    stdio: "inherit",
  });

  child.on("error", () => run(index + 1));
  child.on("exit", (code, signal) => {
    if (signal) process.kill(process.pid, signal);
    process.exit(code ?? 0);
  });
}

run(0);
