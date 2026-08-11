/**
 * Fingerprint of the release-capture mock API surface.
 *
 * Release screenshots are shot against the visual mock server, so the evidence
 * is only trustworthy while that server still returns the payloads it returned
 * during capture. `capture_release_evidence.mjs` records this fingerprint in
 * SCREENSHOT_INDEX.md and `check_release_evidence_bound.mjs` re-computes it on
 * every lint; a mismatch means the mock changed after capture.
 *
 * v11.3.0: `tests/visual/mock_server.cjs` became a thin entry that composes the
 * route modules under `tests/visual/mock_server/`. Both scripts used to hash the
 * entry alone, so every payload edit — which now lands in a route module — was
 * invisible to the gate. They share this module precisely so the two sides of
 * the binding cannot drift apart again.
 *
 * The digest covers the entry plus every `*.cjs` in the directory, ordered by
 * repo-relative POSIX path, and mixes each path into the hash so a rename is a
 * change even when the bytes are identical.
 */
import { createHash } from "node:crypto";
import fs from "node:fs";
import path from "node:path";

/** Repo-relative path of the entry file (POSIX separators). */
export const MOCK_SERVER_ENTRY = "tests/visual/mock_server.cjs";
/** Repo-relative path of the directory holding the composed route modules. */
export const MOCK_SERVER_DIR = "tests/visual/mock_server";
/** Human-readable name for the whole surface, for error messages. */
export const MOCK_SERVER_LABEL = `${MOCK_SERVER_ENTRY} + ${MOCK_SERVER_DIR}/*.cjs`;

function toPosix(relativePath) {
  return relativePath.split(path.sep).join("/");
}

/**
 * Every file the fingerprint covers, as absolute paths in digest order.
 * Returns null when the entry file is missing (nothing to bind to).
 */
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
  // "tests/visual/mock_server.cjs" sorts before "tests/visual/mock_server/x.cjs"
  // ("." < "/"), so the entry always leads — but sort explicitly rather than
  // relying on that, and sort on the POSIX form so Windows agrees with CI.
  return [entry, ...modules].sort((a, b) => {
    const left = toPosix(path.relative(repoRoot, a));
    const right = toPosix(path.relative(repoRoot, b));
    return left < right ? -1 : left > right ? 1 : 0;
  });
}

/**
 * `{ sha256, mtime, bytes, files }` for the mock API surface, or null when the
 * entry file is missing.
 *
 * - `sha256`  digest over `<relative path>\0<per-file sha256>\n` for each file
 * - `mtime`   newest mtime in the set (the last edit that could have moved a payload)
 * - `bytes`   total size of the set
 * - `files`   how many files the digest covers
 */
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
