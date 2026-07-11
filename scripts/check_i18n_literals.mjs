#!/usr/bin/env node
import { existsSync, readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative } from "node:path";
import ts from "typescript";

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
  if (ts.isIdentifier(node) || ts.isStringLiteral(node)) return node.text;
  return "";
}

function literalText(node) {
  if (ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node)) return node.text.trim();
  if (ts.isTemplateExpression(node)) {
    return [node.head.text, ...node.templateSpans.map((span) => span.literal.text)].join(" ").trim();
  }
  return "";
}

function renderedLiterals(expression) {
  if (!expression) return [];
  const direct = literalText(expression);
  if (direct) return [direct];
  if (ts.isParenthesizedExpression(expression)) return renderedLiterals(expression.expression);
  if (ts.isConditionalExpression(expression)) {
    return [...renderedLiterals(expression.whenTrue), ...renderedLiterals(expression.whenFalse)];
  }
  if (ts.isBinaryExpression(expression) && [ts.SyntaxKind.AmpersandAmpersandToken, ts.SyntaxKind.BarBarToken, ts.SyntaxKind.QuestionQuestionToken].includes(expression.operatorToken.kind)) {
    return renderedLiterals(expression.right);
  }
  return [];
}

function looksLocalized(text) {
  return /[A-Za-z\u3131-\uD79D]/u.test(text);
}

function inspect(file) {
  const source = readFileSync(file, "utf8");
  const sourceFile = ts.createSourceFile(
    file,
    source,
    ts.ScriptTarget.Latest,
    true,
    file.endsWith(".tsx") ? ts.ScriptKind.TSX : ts.ScriptKind.TS,
  );
  const findings = [];
  const add = (kind, node, value) => {
    const normalized = String(value || "").trim();
    if (!looksLocalized(normalized)) return;
    const { line } = sourceFile.getLineAndCharacterOfPosition(node.getStart(sourceFile));
    findings.push({ id: `${kind}: ${normalized}`, line: line + 1 });
  };

  const visit = (node) => {
    if (ts.isJsxText(node)) add("JSX text", node, node.getText(sourceFile));

    if (ts.isJsxAttribute(node) && localizedAttributes.has(node.name.text)) {
      if (node.initializer && ts.isStringLiteral(node.initializer)) add(`JSX ${node.name.text}`, node, node.initializer.text);
      if (node.initializer && ts.isJsxExpression(node.initializer)) {
        for (const value of renderedLiterals(node.initializer.expression)) add(`JSX ${node.name.text}`, node, value);
      }
    }

    if (ts.isJsxExpression(node) && !ts.isJsxAttribute(node.parent)) {
      for (const value of renderedLiterals(node.expression)) add("JSX expression", node, value);
    }

    if (file.endsWith(".tsx") && ts.isPropertyAssignment(node) && localizedProperties.has(propertyName(node.name))) {
      for (const value of renderedLiterals(node.initializer)) add(`property ${propertyName(node.name)}`, node, value);
    }

    ts.forEachChild(node, visit);
  };
  visit(sourceFile);
  return findings;
}

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
