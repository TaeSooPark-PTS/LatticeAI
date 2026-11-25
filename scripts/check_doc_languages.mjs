#!/usr/bin/env node

import { execFileSync } from "node:child_process";
import { existsSync, readdirSync, readFileSync } from "node:fs";
import path from "node:path";
import process from "node:process";

const ROOT = process.cwd();
const HANGUL_RE = /[\uac00-\ud7a3]/;

function isInsideRoot(absPath) {
  return absPath === ROOT || absPath.startsWith(`${ROOT}${path.sep}`);
}

function existsExactCase(absPath) {
  if (!existsSync(absPath)) return false;
  if (!isInsideRoot(absPath)) return false;
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
    rel.startsWith(".github/") ||
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

function resolveBadgeTarget(fromAbs, rawTarget) {
  const cleanTarget = rawTarget.split("#")[0].trim();
  if (!cleanTarget) return null;
  const targetPath = path.resolve(path.dirname(fromAbs), cleanTarget);
  if (!isInsideRoot(targetPath) || !existsExactCase(targetPath)) {
    return null;
  }
  return {
    abs: targetPath,
    rel: path.relative(ROOT, targetPath).split(path.sep).join("/"),
  };
}

const filters = process.argv
  .slice(2)
  .map((arg) => arg.replace(/^\.\//, "").replace(/\/+$/, ""))
  .filter(Boolean);

function matchesFilters(rel) {
  if (filters.length === 0) return true;
  return filters.some((prefix) => rel === prefix || rel.startsWith(`${prefix}/`));
}

const files = listTrackedMarkdown().filter(
  (rel) => !isExcludedPath(rel) && matchesFilters(rel),
);
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

    const bodyLines = lines.map(stripLanguageSwitcherLine);
    const koBodyLines = bodyLines.filter((l) => HANGUL_RE.test(l)).length;
    if (koBodyLines < 3) {
      errors.push(
        `${rel}: Korean doc has only ${koBodyLines} Hangul line(s) — looks untranslated`,
      );
    }

    const enTarget = extractBadgeTarget(content, "English");
    if (!enTarget) {
      errors.push(`${rel}: missing English language switcher badge`);
    } else {
      const resolvedEn = resolveBadgeTarget(abs, enTarget);
      if (!resolvedEn) {
        errors.push(`${rel}: English switcher target does not exist (case-sensitive): ${enTarget}`);
      } else if (isKoreanDoc(resolvedEn.rel)) {
        errors.push(`${rel}: English switcher points to Korean doc: ${enTarget}`);
      } else if (!isExcludedPath(resolvedEn.rel)) {
        const enContent = readFileSync(resolvedEn.abs, "utf8");
        const backKoTarget = extractBadgeTarget(enContent, "한국어");
        if (!backKoTarget) {
          errors.push(
            `${rel}: English target ${resolvedEn.rel} is missing a 한국어 switcher badge pointing back`,
          );
        } else {
          const backResolved = resolveBadgeTarget(resolvedEn.abs, backKoTarget);
          if (!backResolved || backResolved.rel !== rel) {
            errors.push(
              `${rel}: bidirectional switcher mismatch (${resolvedEn.rel} points 한국어 to ${backKoTarget}, expected ${rel})`,
            );
          }
        }
      }
    }
    continue;
  }

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

  const koTarget = extractBadgeTarget(content, "한국어");
  if (!koTarget) {
    errors.push(`${rel}: missing 한국어 language switcher badge (100% EN↔KO parity required)`);
  } else {
    const resolvedKo = resolveBadgeTarget(abs, koTarget);
    if (!resolvedKo) {
      errors.push(`${rel}: Korean switcher target does not exist (case-sensitive): ${koTarget}`);
    } else if (!isKoreanDoc(resolvedKo.rel)) {
      errors.push(
        `${rel}: 한국어 switcher must point to a Korean mirror (README.ko.md or docs/ko/**), got: ${koTarget}`,
      );
    } else {
      const koContent = readFileSync(resolvedKo.abs, "utf8");
      const backEnTarget = extractBadgeTarget(koContent, "English");
      if (!backEnTarget) {
        errors.push(
          `${rel}: Korean mirror ${resolvedKo.rel} is missing an English switcher badge pointing back`,
        );
      } else {
        const backResolved = resolveBadgeTarget(resolvedKo.abs, backEnTarget);
        if (!backResolved || backResolved.rel !== rel) {
          errors.push(
            `${rel}: bidirectional switcher mismatch (${resolvedKo.rel} points English to ${backEnTarget}, expected ${rel})`,
          );
        }
      }
    }
  }
}

if (filters.length === 0 || matchesFilters("README.md") || matchesFilters("README.ko.md")) {
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
}

if (errors.length > 0) {
  console.error("Documentation language purity check failed:");
  for (const err of errors) {
    console.error(`- ${err}`);
  }
  process.exit(1);
}

console.log(
  `Documentation language purity check passed: ${englishCount} English docs (0 Hangul leaks) & ${koreanCount} Korean mirrors verified (100% bidirectional parity).`,
);
