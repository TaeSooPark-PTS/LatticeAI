#!/usr/bin/env node
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { parse } from "@babel/parser";

const REPO = resolve(import.meta.dirname, "..");

const EXCLUDED_PREFIXES = [
  "static/app/assets/",
  "static/vendor/",
  "vscode-extension/out/",
  "tests/fixtures/",
  "rust/fixtures/",
  "node_modules/",
  ".venv/",
  "dist/",
];

function gitLs(patterns) {
  const out = execFileSync("git", ["ls-files", ...patterns], {
    cwd: REPO,
    encoding: "utf8",
  });
  return out
    .split("\n")
    .filter(Boolean)
    .filter((rel) => !EXCLUDED_PREFIXES.some((prefix) => rel.startsWith(prefix)));
}

function checkTsJs(rel) {
  const abs = resolve(REPO, rel);
  const text = readFileSync(abs, "utf8");
  const isTs =
    rel.endsWith(".ts") ||
    rel.endsWith(".tsx") ||
    rel.endsWith(".mts") ||
    rel.endsWith(".cts");
  const isJsx = rel.endsWith(".tsx") || rel.endsWith(".jsx") || rel.endsWith(".js");
  const plugins = [];
  if (isTs) plugins.push("typescript");
  if (isJsx) plugins.push("jsx");

  let ast;
  try {
    ast = parse(text, {
      sourceType: "unambiguous",
      allowReturnOutsideFunction: true,
      allowImportExportEverywhere: true,
      plugins,
    });
  } catch {
    ast = parse(text, {
      sourceType: "unambiguous",
      allowReturnOutsideFunction: true,
      allowImportExportEverywhere: true,
      plugins: isTs ? ["typescript"] : [],
    });
  }

  const hits = [];
  for (const c of ast.comments || []) {
    const raw = text.slice(c.start, c.end);
    if (c.start === 0 && raw.startsWith("#!")) continue;
    if (raw.startsWith("/// <reference")) continue;
    hits.push(`${rel}:${c.loc.start.line}`);
  }
  return hits;
}

function checkRust(rel) {
  const text = readFileSync(resolve(REPO, rel), "utf8");
  const hits = [];
  const n = text.length;
  let i = 0;
  let line = 1;

  const advance = (count) => {
    for (let k = 0; k < count && i < n; k += 1) {
      if (text[i] === "\n") line += 1;
      i += 1;
    }
  };

  while (i < n) {
    const ch = text[i];
    if ((ch === "b" || ch === "c") && i + 1 < n && text[i + 1] === "r") {
      let j = i + 2;
      let hashes = 0;
      while (j < n && text[j] === "#") {
        hashes += 1;
        j += 1;
      }
      if (j < n && text[j] === '"') {
        const close = '"' + "#".repeat(hashes);
        const idx = text.indexOf(close, j + 1);
        advance(idx === -1 ? n - i : idx + close.length - i);
        continue;
      }
    }
    if (ch === "r") {
      let j = i + 1;
      let hashes = 0;
      while (j < n && text[j] === "#") {
        hashes += 1;
        j += 1;
      }
      if (j < n && text[j] === '"') {
        const close = '"' + "#".repeat(hashes);
        const idx = text.indexOf(close, j + 1);
        advance(idx === -1 ? n - i : idx + close.length - i);
        continue;
      }
    }
    if ((ch === "b" || ch === "c") && i + 1 < n && text[i + 1] === '"') {
      let j = i + 2;
      while (j < n) {
        if (text[j] === "\\" && j + 1 < n) j += 2;
        else if (text[j] === '"') {
          j += 1;
          break;
        } else j += 1;
      }
      advance(j - i);
      continue;
    }
    if (ch === '"') {
      let j = i + 1;
      while (j < n) {
        if (text[j] === "\\" && j + 1 < n) j += 2;
        else if (text[j] === '"') {
          j += 1;
          break;
        } else j += 1;
      }
      advance(j - i);
      continue;
    }
    if (ch === "b" && i + 1 < n && text[i + 1] === "'") {
      let j = i + 2;
      if (j < n && text[j] === "\\" && j + 1 < n) {
        j += 2;
        while (j < n && text[j] !== "'") j += 1;
        if (j < n) j += 1;
        advance(j - i);
        continue;
      }
      if (j + 1 < n && text[j + 1] === "'") {
        advance(4);
        continue;
      }
    }
    if (ch === "'") {
      if (i + 1 < n && text[i + 1] === "\\") {
        let j = i + 2;
        while (j < n && text[j] !== "'" && text[j] !== "\n") j += 1;
        if (j < n && text[j] === "'") j += 1;
        advance(j - i);
        continue;
      }
      if (i + 2 < n && text[i + 2] === "'" && text[i + 1] !== "\n") {
        advance(3);
        continue;
      }
      advance(1);
      continue;
    }
    if (ch === "/" && i + 1 < n && text[i + 1] === "/") {
      hits.push(`${rel}:${line}`);
      while (i < n && text[i] !== "\n") advance(1);
      continue;
    }
    if (ch === "/" && i + 1 < n && text[i + 1] === "*") {
      hits.push(`${rel}:${line}`);
      let depth = 1;
      advance(2);
      while (i < n && depth > 0) {
        if (text[i] === "/" && i + 1 < n && text[i + 1] === "*") {
          depth += 1;
          advance(2);
        } else if (text[i] === "*" && i + 1 < n && text[i + 1] === "/") {
          depth -= 1;
          advance(2);
        } else {
          advance(1);
        }
      }
      continue;
    }
    advance(1);
  }
  return hits;
}

function checkShell(rel) {
  const lines = readFileSync(resolve(REPO, rel), "utf8").split("\n");
  const hits = [];
  for (let idx = 0; idx < lines.length; idx += 1) {
    const line = lines[idx];
    if (idx === 0 && line.startsWith("#!")) continue;
    let inSq = false;
    let inDq = false;
    let esc = false;
    for (let i = 0; i < line.length; i += 1) {
      const ch = line[i];
      if (esc) {
        esc = false;
        continue;
      }
      if (ch === "\\" && !inSq) {
        esc = true;
        continue;
      }
      if (ch === "'" && !inDq) inSq = !inSq;
      else if (ch === '"' && !inSq) inDq = !inDq;
      else if (ch === "#" && !inSq && !inDq && (i === 0 || /\s/.test(line[i - 1]))) {
        hits.push(`${rel}:${idx + 1}`);
        break;
      }
    }
  }
  return hits;
}

function checkCss(rel) {
  const text = readFileSync(resolve(REPO, rel), "utf8");
  const hits = [];
  let line = 1;
  for (let i = 0; i < text.length; i += 1) {
    const ch = text[i];
    if (ch === "\n") line += 1;
    else if (ch === '"' || ch === "'") {
      const q = ch;
      i += 1;
      while (i < text.length) {
        if (text[i] === "\n") line += 1;
        if (text[i] === "\\" && i + 1 < text.length) i += 2;
        else if (text[i] === q) break;
        else i += 1;
      }
    } else if (ch === "/" && i + 1 < text.length && text[i + 1] === "*") {
      hits.push(`${rel}:${line}`);
      const end = text.indexOf("*/", i + 2);
      if (end === -1) break;
      for (let k = i; k < end + 2; k += 1) {
        if (text[k] === "\n") line += 1;
      }
      i = end + 1;
    }
  }
  return hits;
}

function checkHtml(rel) {
  if (rel === "static/app/index.html") return [];
  const text = readFileSync(resolve(REPO, rel), "utf8");
  const hits = [];
  let idx = text.indexOf("<!--");
  while (idx !== -1) {
    const line = text.slice(0, idx).split("\n").length;
    hits.push(`${rel}:${line}`);
    idx = text.indexOf("<!--", idx + 4);
  }
  return hits;
}

function checkPython(pyFiles) {
  if (pyFiles.length === 0) return [];
  const pyScript = [
    "import ast, io, json, sys, tokenize",
    "from pathlib import Path",
    "hits = []",
    "for rel in sys.argv[1:]:",
    "    text = Path(rel).read_text(encoding='utf-8')",
    "    try:",
    "        tree = ast.parse(text)",
    "    except SyntaxError as exc:",
    "        hits.append(f'{rel}:{exc.lineno or 1}')",
    "        continue",
    "    for node in ast.walk(tree):",
    "        body = getattr(node, 'body', None)",
    "        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and isinstance(body, list) and body:",
    "            first = body[0]",
    "            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):",
    "                hits.append(f'{rel}:{first.lineno}')",
    "    for tok in tokenize.generate_tokens(io.StringIO(text).readline):",
    "        if tok.type == tokenize.COMMENT:",
    "            if tok.start == (1, 0) and tok.string.startswith('#!'):",
    "                continue",
    "            hits.append(f'{rel}:{tok.start[0]}')",
    "print(json.dumps(hits))",
  ].join("\n");
  const pyBin = process.env.PYTHON || "python3";
  const out = execFileSync(pyBin, ["-c", pyScript, ...pyFiles], {
    cwd: REPO,
    encoding: "utf8",
    maxBuffer: 16 * 1024 * 1024,
  });
  return JSON.parse(out);
}

const failures = [];
for (const rel of gitLs(["*.ts", "*.tsx", "*.js", "*.jsx", "*.mjs", "*.cjs", "*.mts", "*.cts"])) {
  failures.push(...checkTsJs(rel));
}
for (const rel of gitLs(["*.rs"])) {
  failures.push(...checkRust(rel));
}
for (const rel of gitLs(["*.sh"])) {
  failures.push(...checkShell(rel));
}
for (const rel of gitLs(["*.css"])) {
  failures.push(...checkCss(rel));
}
for (const rel of gitLs(["*.html"])) {
  failures.push(...checkHtml(rel));
}
failures.push(...checkPython(gitLs(["*.py"])));

if (failures.length > 0) {
  console.error(`no-comments gate: found ${failures.length} prohibited comment(s)/docstring(s):`);
  for (const item of failures.slice(0, 50)) {
    console.error(`  ${item}`);
  }
  if (failures.length > 50) {
    console.error(`  ... and ${failures.length - 50} more`);
  }
  process.exit(1);
}
console.log("no-comments gate: 0 comments/docstrings found across tracked source files.");
