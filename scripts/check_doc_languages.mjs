#!/usr/bin/env node
/**
 * Bilingual Documentation Language Purity & Switcher Gate.
 *
 * Enforces strict language separation across GitHub-facing Markdown docs:
 *   1. English canonical documents (root *.md except README.ko.md, docs/**
 *      except docs/ko/**, browser-extension/README.md, vscode-extension/README.md,
 *      rust/star/ARCHITECTURE.md) must contain ZERO Hangul characters outside
 *      the top language-switcher badge (`한국어`).
 *   2. README.md and README.ko.md must carry bidirectional English / 한국어
 *      switcher links pointing to each other.
 *   3. Every Korean document under docs/ko/** (and README.ko.md) must carry an
 *      English switcher link pointing to an existing English document in the
 *      repository AND contain Korean prose (preventing empty/untranslated stubs).
 *
 * Runtime agent prompt assets under `skills/` and test fixtures under `tests/`
 * or `output/` are intentionally excluded.
 */

import { execFileSync } from "node:child_process";
import { existsSync, readdirSync, readFileSync } from "node:fs";
import path from "node:path";
import process from "node:process";

const ROOT = process.cwd();
const HANGUL_RE = /[\uac00-\ud7a3]/;

// Case-sensitive path existence check so macOS APFS cannot hide case mismatches
// that would 404 on GitHub or Linux CI.
function existsExactCase(absPath) {
  if (!existsSync(absPath)) return false;
  const rel = path.relative(ROOT, absPath);
  if (rel.startsWith("..")) return false;
  let cur = ROOT;
  for (const part of rel.split(path.sep)) {
    if (!part || part === ".") continue;
    const entries = readdirSync(cur);
    if (!entries.includes(part)) return false;
    cur = path.join(cur, part);
  }
  return true;
}

// Strip only the standardized language switcher badge line(s) before checking
// English docs for stray Hangul characters.
function stripLanguageSwitcherLine(line) {
  if (
    line.includes("img.shields.io/badge/") &&
    line.includes("English") &&
    line.includes("한국어")
  ) {
    return "";
  }
  return line;
}

function listTrackedMarkdown() {
  const raw = execFileSync(
    "git",
    [
      "-c",
      "core.quotepath=false",
      "ls-files",
      "--cached",
      "--others",
      "--exclude-standard",
      "*.md",
    ],
    { cwd: ROOT, encoding: "utf8" },
  );
  return [...new Set(raw.split(/\r?\n/).map((s) => s.trim()).filter(Boolean))].sort();
}

function isExcludedPath(rel) {
  return (
    rel.startsWith("skills/") ||
    rel.startsWith("tests/") ||
    rel.startsWith("output/") ||
    rel.startsWith("plugins/") ||
    rel.startsWith("rust/fixtures/") ||
    rel.startsWith("agent_workspace/")
  );
}

function isKoreanDoc(rel) {
  return rel === "README.ko.md" || rel.startsWith("docs/ko/");
}

function extractBadgeTarget(text, label) {
  const re = new RegExp(
    `\\[!\\[${label}\\]\\(https://img\\.shields\\.io/badge/[^)]+\\)\\]\\(([^)]+)\\)`,
  );
  const m = text.match(re);
  return m ? m[1].trim() : null;
}

const files = listTrackedMarkdown().filter((rel) => !isExcludedPath(rel));
const errors = [];
let englishCount = 0;
let koreanCount = 0;

for (const rel of files) {
  const abs = path.join(ROOT, rel);
  if (!existsSync(abs)) continue;
  const content = readFileSync(abs, "utf8");
  const lines = content.split(/\r?\n/);

  if (isKoreanDoc(rel)) {
    koreanCount += 1;

    // 1. Must contain Korean prose beyond the switcher badge.
    const bodyLines = lines.map(stripLanguageSwitcherLine);
    const koBodyLines = bodyLines.filter((l) => HANGUL_RE.test(l)).length;
    if (koBodyLines < 3) {
      errors.push(
        `${rel}: Korean doc has only ${koBodyLines} Hangul line(s) — looks untranslated`,
      );
    }

    // 2. Must have a working English switcher button resolving inside repo.
    const enTarget = extractBadgeTarget(content, "English");
    if (!enTarget) {
      errors.push(`${rel}: missing English language switcher badge`);
    } else {
      const targetPath = path.resolve(path.dirname(abs), enTarget.split("#")[0]);
      if (!targetPath.startsWith(ROOT) || !existsExactCase(targetPath)) {
        errors.push(`${rel}: English switcher target does not exist (case-sensitive): ${enTarget}`);
      } else {
        const targetRel = path.relative(ROOT, targetPath);
        if (isKoreanDoc(targetRel)) {
          errors.push(`${rel}: English switcher points to Korean doc: ${enTarget}`);
        }
      }
    }
    continue;
  }

  // English canonical document
  englishCount += 1;
  const leaks = [];
  for (let i = 0; i < lines.length; i += 1) {
    const cleaned = stripLanguageSwitcherLine(lines[i]);
    if (HANGUL_RE.test(cleaned)) {
      leaks.push(`L${i + 1}: ${lines[i].trim().slice(0, 100)}`);
    }
  }
  if (leaks.length > 0) {
    errors.push(
      `${rel}: contains ${leaks.length} Hangul line(s) in English doc (first: ${leaks[0]})`,
    );
  }

  // If the English doc carries a Korean switcher badge, verify its target exists.
  const koTarget = extractBadgeTarget(content, "한국어");
  if (koTarget) {
    const targetPath = path.resolve(path.dirname(abs), koTarget.split("#")[0]);
    if (!targetPath.startsWith(ROOT) || !existsExactCase(targetPath)) {
      errors.push(`${rel}: Korean switcher target does not exist (case-sensitive): ${koTarget}`);
    }
  }
}

// Explicitly verify README.md <-> README.ko.md bidirectional switcher.
const readmeEn = readFileSync(path.join(ROOT, "README.md"), "utf8");
if (extractBadgeTarget(readmeEn, "한국어") !== "README.ko.md") {
  errors.push("README.md: missing 한국어 switcher badge pointing to README.ko.md");
}
if (!existsSync(path.join(ROOT, "README.ko.md"))) {
  errors.push("README.ko.md: missing Korean root README mirror");
} else {
  const readmeKo = readFileSync(path.join(ROOT, "README.ko.md"), "utf8");
  if (extractBadgeTarget(readmeKo, "English") !== "README.md") {
    errors.push("README.ko.md: missing English switcher badge pointing to README.md");
  }
}

if (errors.length > 0) {
  console.error("Documentation language purity check failed:");
  for (const err of errors) {
    console.error(`- ${err}`);
  }
  process.exit(1);
}

console.log(
  `Documentation language purity check passed: ${englishCount} English docs (0 Hangul leaks) & ${koreanCount} Korean mirrors verified.`,
);
