import { createHash } from "node:crypto";
import fs from "node:fs";
import path from "node:path";

export const MOCK_SERVER_ENTRY = "tests/visual/mock_server.cjs";
export const MOCK_SERVER_DIR = "tests/visual/mock_server";
export const MOCK_SERVER_LABEL = `${MOCK_SERVER_ENTRY} + ${MOCK_SERVER_DIR}/*.cjs`;

function toPosix(relativePath) {
  return relativePath.split(path.sep).join("/");
}

export function mockServerFiles(repoRoot) {
  const entry = path.join(repoRoot, ...MOCK_SERVER_ENTRY.split("/"));
  if (!fs.existsSync(entry)) {
    return null;
  }
  const dir = path.join(repoRoot, ...MOCK_SERVER_DIR.split("/"));
  const modules = fs.existsSync(dir)
    ? fs
        .readdirSync(dir)
        .filter((name) => name.endsWith(".cjs"))
        .map((name) => path.join(dir, name))
    : [];
  return [entry, ...modules].sort((a, b) => {
    const left = toPosix(path.relative(repoRoot, a));
    const right = toPosix(path.relative(repoRoot, b));
    return left < right ? -1 : left > right ? 1 : 0;
  });
}

export function mockServerFingerprint(repoRoot) {
  const files = mockServerFiles(repoRoot);
  if (!files) {
    return null;
  }
  const digest = createHash("sha256");
  let bytes = 0;
  let newest = 0;
  for (const file of files) {
    const body = fs.readFileSync(file);
    const stat = fs.statSync(file);
    bytes += body.length;
    newest = Math.max(newest, stat.mtime.getTime());
    digest.update(toPosix(path.relative(repoRoot, file)));
    digest.update("\0");
    digest.update(createHash("sha256").update(body).digest("hex"));
    digest.update("\n");
  }
  return {
    sha256: digest.digest("hex"),
    mtime: new Date(newest).toISOString(),
    bytes,
    files: files.length,
  };
}
