#!/usr/bin/env node
import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import { join } from "node:path";

const host = process.env.LTCAI_TEST_HOST || "127.0.0.1";
const port = process.env.LTCAI_TEST_PORT || "8899";
const baseUrl = process.env.LTCAI_TEST_BASE_URL || `http://${host}:${port}`;
const venvPython = join(process.cwd(), ".venv", "bin", "python");
const python = process.env.PYTHON || (existsSync(venvPython) ? venvPython : "python");

function run(command, args, options = {}) {
  return new Promise((resolve) => {
    const child = spawn(command, args, {
      stdio: "inherit",
      env: { ...process.env, ...options.env },
      cwd: options.cwd || process.cwd(),
    });
    child.on("close", (code, signal) => resolve({ code, signal }));
  });
}

function delay(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function waitForHealth(url, timeoutMs = 30000) {
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

const server = spawn(
  python,
  ["-m", "uvicorn", "server:app", "--host", host, "--port", port],
  {
    cwd: process.cwd(),
    env: {
      ...process.env,
      LATTICEAI_MODE: process.env.LATTICEAI_MODE || "test",
      LATTICEAI_HOST: host,
      LATTICEAI_PORT: port,
    },
    stdio: ["ignore", "pipe", "pipe"],
  },
);

server.stdout.on("data", (chunk) => process.stdout.write(chunk));
server.stderr.on("data", (chunk) => process.stderr.write(chunk));

let exitCode = 1;
try {
  await waitForHealth(baseUrl);
  const result = await run(python, ["-m", "pytest", "tests/integration/", "-v"], {
    env: { LTCAI_TEST_BASE_URL: baseUrl },
  });
  exitCode = result.code || 0;
} catch (error) {
  console.error(String(error?.message || error));
} finally {
  await stop(server);
}

process.exit(exitCode);
