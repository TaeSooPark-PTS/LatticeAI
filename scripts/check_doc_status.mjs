// Repo-wide documentation status + link gate.
//
// Companion to scripts/check_current_release_docs.mjs (which pins the fixed set
// of current-release files). This script widens coverage to every Markdown doc:
//
//   1. Every relative Markdown link under the root canonical docs and docs/**
//      must resolve to a file inside the repo.
//   2. Docs are classified by an explicit status badge:
//        > **Status: canonical**   (or `> Status: canonical`)
//        > **Status: reference**
//        > **Status: historical**
//      Canonical docs are asserted to track the current package version and
//      carry no stale "Current release" marker. Reference/historical (and
//      unbadged) docs are allowed to preserve their point-in-time versions, so
//      snapshots do not have to be rewritten every release.
//
// It intentionally does not force a badge onto every doc; unbadged docs are
// link-checked only. Add a `> **Status: ...**` badge when a doc should be held
// to (or exempted from) the current-version assertion.

import { existsSync, readFileSync, readdirSync, statSync } from "node:fs";
import path from "node:path";
import process from "node:process";

const root = process.cwd();
const pkg = JSON.parse(readFileSync(path.join(root, "package.json"), "utf8"));
const version = pkg.version;
const escapedVersion = version.replaceAll(".", "\\.");

const rootCanonicalDocs = [
  "README.md",
  "ARCHITECTURE.md",
  "FEATURE_STATUS.md",
  "SECURITY.md",
  "PRIVACY.md",
  "AGENTS.md",
];

const errors = [];

function walkMarkdown(dir) {
  const out = [];
  if (!existsSync(dir)) return out;
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      out.push(...walkMarkdown(full));
    } else if (entry.isFile() && entry.name.endsWith(".md")) {
      out.push(full);
    }
  }
  return out;
}

function collectDocs() {
  const files = new Set();
  for (const rel of rootCanonicalDocs) {
    const full = path.join(root, rel);
    if (existsSync(full)) files.add(full);
  }
  for (const full of walkMarkdown(path.join(root, "docs"))) {
    files.add(full);
  }
  return [...files].sort();
}

function isExternal(target) {
  return /^(https?:|mailto:|tel:)/i.test(target);
}

function relativeLinks(markdown) {
  const out = [];
  const linkPattern = /!?\[[^\]]*\]\(([^)]+)\)/g;
  let match;
  while ((match = linkPattern.exec(markdown))) {
    out.push(match[1].trim());
  }
  return out;
}

function checkLinks(file, markdown) {
  for (const raw of relativeLinks(markdown)) {
    const hash = raw.indexOf("#");
    let target = (hash >= 0 ? raw.slice(0, hash) : raw).trim();
    target = target.replace(/^<|>$/g, "").trim();
    if (!target || isExternal(target)) continue;
    const resolved = path.resolve(path.dirname(file), decodeURIComponent(target));
    const rel = path.relative(root, file);
    if (!resolved.startsWith(root)) {
      errors.push(`${rel}: link points outside repo: ${raw}`);
      continue;
    }
    if (!existsSync(resolved)) {
      errors.push(`${rel}: missing link target: ${raw}`);
    }
  }
}

function statusOf(markdown) {
  const match = markdown.match(
    />\s*\*{0,2}Status:\*{0,2}\s*(canonical|reference|historical)\b/i,
  );
  return match ? match[1].toLowerCase() : null;
}

function checkCanonicalVersion(file, markdown) {
  const rel = path.relative(root, file);
  const stale = markdown.match(
    new RegExp(`Current release:\\s+\\*\\*(?!${escapedVersion}\\b)[^*]+\\*\\*`, "i"),
  );
  if (stale) {
    errors.push(`${rel}: canonical doc has stale current-release marker ${JSON.stringify(stale[0])}`);
  }
  if (!markdown.includes(version)) {
    errors.push(`${rel}: canonical doc does not reference current version ${version}`);
  }
}

const docs = collectDocs();
let canonical = 0;
let reference = 0;
let historical = 0;

for (const file of docs) {
  const markdown = readFileSync(file, "utf8");
  checkLinks(file, markdown);
  const status = statusOf(markdown);
  if (status === "canonical") {
    canonical += 1;
    checkCanonicalVersion(file, markdown);
  } else if (status === "reference") {
    reference += 1;
  } else if (status === "historical") {
    historical += 1;
  }
}

if (errors.length) {
  console.error("Documentation status/link check failed:");
  for (const error of errors) {
    console.error(`- ${error}`);
  }
  process.exit(1);
}

console.log(
  `Documentation status/link check passed: ${docs.length} docs scanned ` +
    `(${canonical} canonical @ ${version}, ${reference} reference, ${historical} historical).`,
);
