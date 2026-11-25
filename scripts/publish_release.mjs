#!/usr/bin/env node
import { spawnSync } from "node:child_process";
import { existsSync, readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
process.chdir(ROOT);

const DRY = process.argv.includes("--dry-run");
const pkg = JSON.parse(readFileSync(path.join(ROOT, "package.json"), "utf8"));
const V = pkg.version;
const PUBLISHER = JSON.parse(
  readFileSync(path.join(ROOT, "vscode-extension", "package.json"), "utf8"),
).publisher;

const TGZ = path.join(ROOT, `ltcai-${V}.tgz`);
const WHEEL = path.join(ROOT, "dist", `ltcai-${V}-py3-none-any.whl`);
const SDIST = path.join(ROOT, "dist", `ltcai-${V}.tar.gz`);
const VSIX = path.join(ROOT, "dist", `ltcai-${V}.vsix`);

function run(cmd, args, env = {}) {
  const r = spawnSync(cmd, args, {
    stdio: "inherit",
    cwd: ROOT,
    env: { ...process.env, ...env },
  });
  return r.status === 0;
}

async function json(url, init) {
  try {
    const res = await fetch(url, init);
    if (!res.ok) return null;
    return await res.json();
  } catch {
    return null;
  }
}

const checks = {
  async npm() {
    const d = await json(`https://registry.npmjs.org/ltcai/${V}`);
    return d?.version === V;
  },
  async pypi() {
    return (await json(`https://pypi.org/pypi/ltcai/${V}/json`)) !== null;
  },
  async marketplace() {
    const d = await json(
      "https://marketplace.visualstudio.com/_apis/public/gallery/extensionquery",
      {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Accept: "application/json;api-version=3.0-preview.1",
        },
        body: JSON.stringify({
          filters: [{ criteria: [{ filterType: 7, value: `${PUBLISHER}.ltcai` }] }],
          flags: 1,
        }),
      },
    );
    const ext = d?.results?.[0]?.extensions?.[0];
    return ext?.versions?.some((x) => x.version === V) ?? false;
  },
  async openvsx() {
    return (await json(`https://open-vsx.org/api/${PUBLISHER}/ltcai/${V}`)) !== null;
  },
};

const targets = [
  {
    name: "npm",
    artifacts: [TGZ],
    credsMissing: () => null,
    publish: () => run("npm", ["publish", TGZ]),
    note: "브라우저 2FA 승인 창이 뜨면 승인해야 완료됩니다.",
  },
  {
    name: "pypi",
    artifacts: [SDIST, WHEEL],
    credsMissing: () => null,
    publish: () =>
      run("node", [
        "scripts/run_python.mjs",
        "-m",
        "twine",
        "upload",
        "--skip-existing",
        SDIST,
        WHEEL,
      ]),
  },
  {
    name: "marketplace",
    artifacts: [VSIX],
    credsMissing: () => (process.env.VSCE_PAT ? null : "VSCE_PAT 환경변수가 필요합니다"),
    publish: () => run("npx", ["--yes", "@vscode/vsce", "publish", "--packagePath", VSIX]),
    note: "게시 후 마켓플레이스 검증에 몇 분 걸릴 수 있습니다.",
  },
  {
    name: "openvsx",
    artifacts: [VSIX],
    credsMissing: () => (process.env.OVSX_PAT ? null : "OVSX_PAT 환경변수가 필요합니다"),
    publish: () => run("npx", ["--yes", "ovsx", "publish", VSIX]),
  },
];

const results = [];
for (const t of targets) {
  const already = await checks[t.name]();
  if (already) {
    results.push([t.name, "already-published"]);
    continue;
  }
  const missing = t.artifacts.filter((a) => !existsSync(a));
  if (missing.length) {
    results.push([t.name, `artifact missing: ${missing.map((m) => path.basename(m)).join(", ")}`]);
    continue;
  }
  const creds = t.credsMissing();
  if (creds) {
    results.push([t.name, `skipped: ${creds}`]);
    continue;
  }
  if (DRY) {
    results.push([t.name, "would publish"]);
    continue;
  }
  if (t.note) console.log(`\n[${t.name}] ${t.note}`);
  console.log(`[${t.name}] publishing v${V}…`);
  results.push([t.name, t.publish() ? "published" : "FAILED"]);
}

console.log(`\n=== publish report — ltcai v${V} ===`);
for (const [name, status] of results) console.log(`  ${name.padEnd(12)} ${status}`);

const bad = results.filter(
  ([, s]) => s === "FAILED" || s.startsWith("artifact missing") || s.startsWith("skipped"),
);
process.exit(bad.length ? 1 : 0);
