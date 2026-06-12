import { existsSync, readFileSync, statSync } from "node:fs";
import path from "node:path";

const root = process.cwd();
const readme = path.join(root, "README.md");
const checkedMarkdown = new Set();
const failures = [];

function stripAnchor(target) {
  const hash = target.indexOf("#");
  return hash >= 0 ? target.slice(0, hash) : target;
}

function isExternal(target) {
  return /^(https?:|mailto:|tel:)/i.test(target);
}

function decodeTarget(target) {
  return decodeURIComponent(target.replace(/^<|>$/g, ""));
}

function localPath(fromFile, target) {
  const cleaned = stripAnchor(target).trim();
  if (!cleaned || isExternal(cleaned)) return null;
  return path.resolve(path.dirname(fromFile), decodeTarget(cleaned));
}

function links(markdown) {
  const out = [];
  const linkPattern = /!?\[[^\]]*\]\(([^)]+)\)/g;
  let match;
  while ((match = linkPattern.exec(markdown))) {
    out.push(match[1].trim());
  }
  return out;
}

function checkFileLink(fromFile, target) {
  const resolved = localPath(fromFile, target);
  if (!resolved) return;
  if (!resolved.startsWith(root)) {
    failures.push(`${path.relative(root, fromFile)} links outside repo: ${target}`);
    return;
  }
  if (!existsSync(resolved)) {
    failures.push(`${path.relative(root, fromFile)} has missing link: ${target}`);
  }
}

function checkMarkdownFile(file) {
  if (checkedMarkdown.has(file)) return;
  checkedMarkdown.add(file);
  const markdown = readFileSync(file, "utf8");
  for (const target of links(markdown)) {
    checkFileLink(file, target);
  }
}

checkMarkdownFile(readme);

for (const target of links(readFileSync(readme, "utf8"))) {
  const resolved = localPath(readme, target);
  if (!resolved || !existsSync(resolved)) continue;
  if (statSync(resolved).isFile() && resolved.endsWith(".md")) {
    checkMarkdownFile(resolved);
  }
}

if (failures.length) {
  console.error("Markdown link check failed:");
  for (const failure of failures) console.error(`- ${failure}`);
  process.exit(1);
}

console.log(`Markdown link check passed for README and ${checkedMarkdown.size - 1} README-linked Markdown files.`);
