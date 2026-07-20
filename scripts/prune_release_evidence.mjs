#!/usr/bin/env node
// Retention policy for output/release/: keep the newest N versioned evidence
// directories (default 3, override with LTCAI_RELEASE_EVIDENCE_KEEP or argv).
// Older captures are reproducible on demand via `npm run release:evidence`,
// so keeping the full history only burns disk (13 versions ≈ 90MB).
import { existsSync, readdirSync, rmSync, statSync } from "node:fs";
import { join } from "node:path";

const repo = join(import.meta.dirname, "..");
const releaseDir = join(repo, "output", "release");
const keep = Math.max(1, Number(process.argv[2] || process.env.LTCAI_RELEASE_EVIDENCE_KEEP || 3));

if (!existsSync(releaseDir)) {
  console.log(`release evidence: nothing to prune (${releaseDir} missing)`);
  process.exit(0);
}

const semver = (name) => {
  const m = /^v(\d+)\.(\d+)\.(\d+)$/.exec(name);
  return m ? [Number(m[1]), Number(m[2]), Number(m[3])] : null;
};

const versioned = readdirSync(releaseDir)
  .filter((name) => semver(name) && statSync(join(releaseDir, name)).isDirectory())
  .sort((a, b) => {
    const [a1, a2, a3] = semver(a);
    const [b1, b2, b3] = semver(b);
    return b1 - a1 || b2 - a2 || b3 - a3;
  });

const stale = versioned.slice(keep);
for (const name of stale) {
  rmSync(join(releaseDir, name), { recursive: true, force: true });
  console.log(`release evidence: pruned ${name}`);
}
console.log(
  `release evidence: keeping ${Math.min(keep, versioned.length)} of ${versioned.length} versions (policy: newest ${keep})`,
);
