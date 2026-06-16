#!/usr/bin/env node
import { existsSync, readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative } from "node:path";

const repo = join(import.meta.dirname, "..");
const roots = [
  join(repo, "frontend", "src", "features", "brain"),
  join(repo, "frontend", "src", "features", "admin"),
  join(repo, "frontend", "src", "components", "onboarding"),
];

function walk(dir) {
  if (!existsSync(dir)) return [];
  const out = [];
  for (const name of readdirSync(dir)) {
    const path = join(dir, name);
    const stat = statSync(path);
    if (stat.isDirectory()) out.push(...walk(path));
    else if (name.endsWith(".tsx")) out.push(path);
  }
  return out;
}

const rawLocalizedProps = /\b(?:aria-label|placeholder|title)=["'][^"'{]*[A-Za-z][^"']*["']/g;
let failures = 0;

for (const file of roots.flatMap(walk)) {
  const text = readFileSync(file, "utf8");
  const matches = text.match(rawLocalizedProps) || [];
  if (!matches.length) continue;
  failures += matches.length;
  for (const match of matches) {
    console.error(`${relative(repo, file)}: hardcoded localized prop: ${match}`);
  }
}

if (failures) {
  console.error(`i18n literal check: ${failures} failure(s)`);
  process.exit(1);
}

console.log("i18n literal check: localized props use translation keys");
