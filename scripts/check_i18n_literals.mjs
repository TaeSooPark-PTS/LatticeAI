#!/usr/bin/env node
import { existsSync, readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative } from "node:path";

const repo = join(import.meta.dirname, "..");
const roots = [
  join(repo, "frontend", "src"),
];
const allowlistPath = join(repo, "scripts", "i18n_literal_allowlist.json");
const allowlist = existsSync(allowlistPath)
  ? JSON.parse(readFileSync(allowlistPath, "utf8"))
  : {};

function walk(dir) {
  if (!existsSync(dir)) return [];
  const out = [];
  for (const name of readdirSync(dir)) {
    const path = join(dir, name);
    const stat = statSync(path);
    if (stat.isDirectory()) out.push(...walk(path));
    else if ((name.endsWith(".tsx") || name.endsWith(".ts")) && name !== "openapi.ts") {
      out.push(path);
    }
  }
  return out;
}

const rawLocalizedProps = /\b(?:aria-label|placeholder|title)=["'][^"'{]*[A-Za-z][^"']*["']/g;
const rawJsxText = />\s*([A-Z][A-Za-z0-9][^<>{}\n]{2,})\s*</g;
const rawComponentCopy = /\b(?:title|detail|description|successLabel|empty)=["'][^"'{]*[A-Za-z][^"']*["']/g;
let failures = 0;
let allowed = 0;

for (const file of roots.flatMap(walk)) {
  const text = readFileSync(file, "utf8");
  const matches = [
    ...(text.match(rawLocalizedProps) || []),
    ...(text.match(rawComponentCopy) || []),
  ];
  for (const match of text.matchAll(rawJsxText)) {
    const literal = match[1].trim();
    if (!literal || /^[A-Z][a-z]*$/.test(literal) || /^Lattice\b/.test(literal)) continue;
    matches.push(`JSX text: ${literal}`);
  }
  if (!matches.length) continue;
  const rel = relative(repo, file);
  const budget = Number(allowlist[rel]?.maxFindings || 0);
  if (budget >= matches.length) {
    allowed += matches.length;
    continue;
  }
  const newMatches = matches.slice(budget);
  failures += newMatches.length;
  for (const match of newMatches) {
    console.error(`${rel}: hardcoded localized prop: ${match}`);
  }
}

if (failures) {
  console.error(`i18n literal check: ${failures} failure(s)`);
  process.exit(1);
}

console.log(`i18n literal check: localized props use translation keys (${allowed} allowlisted legacy literal(s))`);
