#!/usr/bin/env node
import { existsSync, readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative } from "node:path";
import { pathToFileURL } from "node:url";
import { parse } from "@babel/parser";

const repo = join(import.meta.dirname, "..");
const roots = [join(repo, "frontend", "src")];
const allowlistPath = join(repo, "scripts", "i18n_literal_allowlist.json");
const allowlist = existsSync(allowlistPath)
  ? JSON.parse(readFileSync(allowlistPath, "utf8"))
  : {};

const localizedAttributes = new Set([
  "alt",
  "aria-label",
  "description",
  "detail",
  "empty",
  "label",
  "placeholder",
  "successLabel",
  "title",
  "tooltip",
]);
const localizedProperties = new Set([
  "description",
  "detail",
  "empty",
  "label",
  "placeholder",
  "successLabel",
  "title",
  "tooltip",
]);

function walk(dir) {
  if (!existsSync(dir)) return [];
  const out = [];
  for (const name of readdirSync(dir)) {
    const path = join(dir, name);
    const stat = statSync(path);
    if (stat.isDirectory()) out.push(...walk(path));
    else if ((name.endsWith(".tsx") || name.endsWith(".ts")) && name !== "openapi.ts" && !/\.test\.[tj]sx?$/.test(name)) out.push(path);
  }
  return out;
}

function propertyName(node) {
  if (!node) return "";
  if (node.type === "Identifier") return node.name;
  if (node.type === "StringLiteral") return node.value;
  return "";
}

function literalText(node) {
  if (!node) return "";
  if (node.type === "StringLiteral") return node.value.trim();
  if (node.type === "TemplateLiteral") {
    const parts = node.quasis.map((quasi) => quasi.value.cooked ?? quasi.value.raw);
    return (node.expressions.length === 0 ? parts[0] || "" : parts.join(" ")).trim();
  }
  return "";
}

function renderedLiterals(expression) {
  if (!expression || expression.type === "JSXEmptyExpression") return [];
  if (
    expression.type === "ParenthesizedExpression"
    || expression.type === "TSAsExpression"
    || expression.type === "TSSatisfiesExpression"
    || expression.type === "TSNonNullExpression"
  ) {
    return renderedLiterals(expression.expression);
  }
  const direct = literalText(expression);
  if (direct) return [direct];
  if (expression.type === "ConditionalExpression") {
    return [...renderedLiterals(expression.consequent), ...renderedLiterals(expression.alternate)];
  }
  if (expression.type === "LogicalExpression" && ["&&", "||", "??"].includes(expression.operator)) {
    return renderedLiterals(expression.right);
  }
  return [];
}

function looksLocalized(text) {
  return /[A-Za-z\u3131-\uD79D]/u.test(text);
}

function nodeStartLine(node) {
  const base = node?.loc?.start?.line || 1;
  if (node?.type === "JSXText" && typeof node.value === "string") {
    const leading = node.value.match(/^\s*/)?.[0] || "";
    const newlineOffset = (leading.match(/\n/g) || []).length;
    return base + newlineOffset;
  }
  return base;
}

export function inspectSource(source, file) {
  const ast = parse(source, {
    sourceType: "module",
    plugins: file.endsWith(".tsx") ? ["typescript", "jsx"] : ["typescript"],
  });
  const findings = [];
  const add = (kind, node, value) => {
    const normalized = String(value || "").trim();
    if (!looksLocalized(normalized)) return;
    findings.push({ id: `${kind}: ${normalized}`, line: nodeStartLine(node) });
  };

  const visit = (node, parent) => {
    if (!node || typeof node !== "object") return;
    if (Array.isArray(node)) {
      for (const item of node) visit(item, parent);
      return;
    }
    if (typeof node.type !== "string") return;

    if (node.type === "JSXText") add("JSX text", node, node.value);

    if (node.type === "JSXAttribute" && node.name?.type === "JSXIdentifier" && localizedAttributes.has(node.name.name)) {
      const attrName = node.name.name;
      if (node.value?.type === "StringLiteral") add(`JSX ${attrName}`, node, node.value.value);
      if (node.value?.type === "JSXExpressionContainer") {
        for (const value of renderedLiterals(node.value.expression)) add(`JSX ${attrName}`, node, value);
      }
    }

    if (node.type === "JSXExpressionContainer" && parent?.type !== "JSXAttribute") {
      for (const value of renderedLiterals(node.expression)) add("JSX expression", node, value);
    }

    if (file.endsWith(".tsx") && node.type === "ObjectProperty" && !node.shorthand) {
      const prop = propertyName(node.key);
      if (localizedProperties.has(prop)) {
        for (const value of renderedLiterals(node.value)) add(`property ${prop}`, node, value);
      }
    }

    for (const [key, child] of Object.entries(node)) {
      if (key === "loc" || key === "start" || key === "end" || key === "leadingComments" || key === "trailingComments" || key === "innerComments" || key === "extra") {
        continue;
      }
      visit(child, node);
    }
  };
  visit(ast.program, null);
  return findings;
}

function inspect(file) {
  return inspectSource(readFileSync(file, "utf8"), file);
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  let failures = 0;
  let allowed = 0;
  for (const file of roots.flatMap(walk)) {
    const rel = relative(repo, file);
    const accepted = new Set(allowlist[rel]?.findings || []);
    for (const finding of inspect(file)) {
      if (accepted.has(finding.id)) {
        allowed += 1;
        continue;
      }
      failures += 1;
      console.error(`${rel}:${finding.line}: hardcoded localized copy: ${finding.id}`);
    }
  }

  if (failures) {
    console.error(`i18n literal check: ${failures} failure(s)`);
    process.exit(1);
  }

  console.log(`i18n literal check: localized copy uses translation keys (${allowed} allowlisted technical literal(s))`);
}
