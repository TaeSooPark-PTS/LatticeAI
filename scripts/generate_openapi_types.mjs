#!/usr/bin/env node
/**
 * Zero-dependency OpenAPI 3.1 -> TypeScript declaration generator compatible with
 * `openapi-fetch` and TypeScript 7 (`tsgo`).
 *
 * Replaces `openapi-typescript` + `openapi-ts5` so the workspace has zero
 * dependency on the legacy TypeScript 5 compiler JS API.
 *
 * Usage:
 *   node scripts/generate_openapi_types.mjs <input.json> -o <output.ts>
 */
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { pathToFileURL } from "node:url";

const HTTP_METHODS = ["get", "put", "post", "delete", "options", "head", "patch", "trace"];

function quoteKey(key) {
  return /^[A-Za-z_$][A-Za-z0-9_$]*$/.test(key) ? key : JSON.stringify(key);
}

function indent(depth) {
  return "    ".repeat(depth);
}

function schemaToTs(schema, depth = 0) {
  if (!schema || typeof schema !== "object") return "unknown";
  if (typeof schema.$ref === "string") {
    const match = schema.$ref.match(/^#\/components\/schemas\/(.+)$/);
    if (match) return `components["schemas"][${JSON.stringify(match[1])}]`;
    return "unknown";
  }
  if (Array.isArray(schema.anyOf) && schema.anyOf.length) {
    return schema.anyOf.map((item) => schemaToTs(item, depth)).join(" | ");
  }
  if (Array.isArray(schema.oneOf) && schema.oneOf.length) {
    return schema.oneOf.map((item) => schemaToTs(item, depth)).join(" | ");
  }
  if (Array.isArray(schema.allOf) && schema.allOf.length) {
    return schema.allOf.map((item) => schemaToTs(item, depth)).join(" & ");
  }
  if (Array.isArray(schema.enum) && schema.enum.length) {
    return schema.enum.map((val) => JSON.stringify(val)).join(" | ");
  }
  if (Object.prototype.hasOwnProperty.call(schema, "const")) {
    return JSON.stringify(schema.const);
  }
  if (Array.isArray(schema.type)) {
    return schema.type.map((t) => schemaToTs({ ...schema, type: t }, depth)).join(" | ");
  }

  const type = schema.type;
  if (type === "null") return "null";
  if (type === "string") return "string";
  if (type === "integer" || type === "number") return "number";
  if (type === "boolean") return "boolean";
  if (type === "array") {
    if (Array.isArray(schema.prefixItems) && schema.prefixItems.length) {
      return `[${schema.prefixItems.map((item) => schemaToTs(item, depth)).join(", ")}]`;
    }
    const itemType = schemaToTs(schema.items, depth);
    return itemType.includes(" | ") || itemType.includes(" & ") ? `(${itemType})[]` : `${itemType}[]`;
  }
  if (type === "object" || schema.properties || schema.additionalProperties !== undefined) {
    const props = schema.properties && typeof schema.properties === "object" ? Object.entries(schema.properties) : [];
    const required = new Set(Array.isArray(schema.required) ? schema.required : []);
    const addl = schema.additionalProperties;
    if (!props.length) {
      if (addl && typeof addl === "object") {
        return `{\n${indent(depth + 1)}[key: string]: ${schemaToTs(addl, depth + 1)};\n${indent(depth)}}`;
      }
      if (addl === false) return "Record<string, never>";
      return "Record<string, unknown>";
    }
    const lines = ["{"];
    for (const [propName, propSchema] of props) {
      const opt = required.has(propName) ? "" : "?";
      lines.push(`${indent(depth + 1)}${quoteKey(propName)}${opt}: ${schemaToTs(propSchema, depth + 1)};`);
    }
    if (addl && typeof addl === "object") {
      lines.push(`${indent(depth + 1)}[key: string]: ${schemaToTs(addl, depth + 1)};`);
    } else if (addl === true) {
      lines.push(`${indent(depth + 1)}[key: string]: unknown;`);
    }
    lines.push(`${indent(depth)}}`);
    return lines.join("\n");
  }
  return "unknown";
}

function renderParameters(params, depth) {
  const buckets = { query: [], header: [], path: [], cookie: [] };
  for (const param of params || []) {
    if (param && typeof param === "object" && buckets[param.in]) {
      buckets[param.in].push(param);
    }
  }
  const lines = ["{"];
  for (const kind of ["query", "header", "path", "cookie"]) {
    const list = buckets[kind];
    if (!list.length) {
      lines.push(`${indent(depth + 1)}${kind}?: never;`);
      continue;
    }
    const hasRequired = list.some((p) => Boolean(p.required));
    const opt = hasRequired ? "" : "?";
    lines.push(`${indent(depth + 1)}${kind}${opt}: {`);
    for (const p of list) {
      const pOpt = p.required ? "" : "?";
      lines.push(`${indent(depth + 2)}${quoteKey(String(p.name))}${pOpt}: ${schemaToTs(p.schema, depth + 2)};`);
    }
    lines.push(`${indent(depth + 1)}};`);
  }
  lines.push(`${indent(depth)}}`);
  return lines.join("\n");
}

function renderContent(content, depth) {
  const entries = content && typeof content === "object" ? Object.entries(content) : [];
  if (!entries.length) return "never";
  const lines = ["{"];
  for (const [mediaType, mediaObj] of entries) {
    lines.push(`${indent(depth + 1)}${JSON.stringify(mediaType)}: ${schemaToTs(mediaObj?.schema, depth + 1)};`);
  }
  lines.push(`${indent(depth)}}`);
  return lines.join("\n");
}

function renderRequestBody(reqBody, depth) {
  if (!reqBody || typeof reqBody !== "object") return null;
  const opt = reqBody.required ? "" : "?";
  const lines = [`requestBody${opt}: {`];
  lines.push(`${indent(depth + 1)}content: ${renderContent(reqBody.content, depth + 1)};`);
  lines.push(`${indent(depth)}};`);
  return lines.join("\n");
}

function renderResponses(responses, depth) {
  const entries = responses && typeof responses === "object" ? Object.entries(responses) : [];
  const lines = ["{"];
  for (const [status, resp] of entries) {
    const statusKey = /^\d+$/.test(status) ? status : JSON.stringify(status);
    lines.push(`${indent(depth + 1)}${statusKey}: {`);
    lines.push(`${indent(depth + 2)}headers: {`);
    lines.push(`${indent(depth + 3)}[name: string]: unknown;`);
    lines.push(`${indent(depth + 2)}};`);
    const contentEntries = resp?.content && typeof resp.content === "object" ? Object.entries(resp.content) : [];
    if (contentEntries.length) {
      lines.push(`${indent(depth + 2)}content: ${renderContent(resp.content, depth + 2)};`);
    } else {
      lines.push(`${indent(depth + 2)}content?: never;`);
    }
    lines.push(`${indent(depth + 1)}};`);
  }
  lines.push(`${indent(depth)}}`);
  return lines.join("\n");
}

export function generateOpenApiTypes(spec) {
  const pathsObj = spec?.paths && typeof spec.paths === "object" ? spec.paths : {};
  const schemasObj = spec?.components?.schemas && typeof spec.components.schemas === "object"
    ? spec.components.schemas
    : {};
  const operationsMap = [];

  const pathLines = ["export interface paths {"];
  for (const [pathKey, pathItem] of Object.entries(pathsObj)) {
    if (!pathItem || typeof pathItem !== "object") continue;
    pathLines.push(`${indent(1)}${JSON.stringify(pathKey)}: {`);
    pathLines.push(`${indent(2)}parameters: ${renderParameters(pathItem.parameters, 2)};`);
    for (const method of HTTP_METHODS) {
      const op = pathItem[method];
      if (!op || typeof op !== "object") {
        pathLines.push(`${indent(2)}${method}?: never;`);
        continue;
      }
      const opId = String(op.operationId || `${method}_${pathKey.replace(/[^A-Za-z0-9]+/g, "_")}`);
      operationsMap.push([opId, op]);
      pathLines.push(`${indent(2)}${method}: operations[${JSON.stringify(opId)}];`);
    }
    pathLines.push(`${indent(1)}};`);
  }
  pathLines.push("}");

  const compLines = ["export interface components {"];
  compLines.push(`${indent(1)}schemas: {`);
  for (const [name, schema] of Object.entries(schemasObj)) {
    compLines.push(`${indent(2)}${quoteKey(name)}: ${schemaToTs(schema, 2)};`);
  }
  compLines.push(`${indent(1)}};`);
  compLines.push(`${indent(1)}responses: never;`);
  compLines.push(`${indent(1)}parameters: never;`);
  compLines.push(`${indent(1)}requestBodies: never;`);
  compLines.push(`${indent(1)}headers: never;`);
  compLines.push(`${indent(1)}pathItems: never;`);
  compLines.push("}");

  const opLines = ["export interface operations {"];
  for (const [opId, op] of operationsMap) {
    opLines.push(`${indent(1)}${quoteKey(opId)}: {`);
    opLines.push(`${indent(2)}parameters: ${renderParameters(op.parameters, 2)};`);
    const reqBody = renderRequestBody(op.requestBody, 2);
    if (reqBody) {
      opLines.push(`${indent(2)}${reqBody}`);
    } else {
      opLines.push(`${indent(2)}requestBody?: never;`);
    }
    opLines.push(`${indent(2)}responses: ${renderResponses(op.responses, 2)};`);
    opLines.push(`${indent(1)}};`);
  }
  opLines.push("}");

  return [
    "/**",
    " * This file was auto-generated by scripts/generate_openapi_types.mjs.",
    " * Do not make direct changes to the file.",
    " */",
    "",
    pathLines.join("\n"),
    "export type webhooks = Record<string, never>;",
    compLines.join("\n"),
    "export type $defs = Record<string, never>;",
    opLines.join("\n"),
    "",
  ].join("\n");
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const args = process.argv.slice(2);
  const outIdx = args.indexOf("-o");
  const inputPath = args[0];
  const outputPath = outIdx >= 0 ? args[outIdx + 1] : null;
  if (!inputPath || !outputPath) {
    console.error("Usage: node scripts/generate_openapi_types.mjs <input.json> -o <output.ts>");
    process.exit(2);
  }
  const spec = JSON.parse(readFileSync(resolve(inputPath), "utf8"));
  const output = generateOpenApiTypes(spec);
  const absOut = resolve(outputPath);
  mkdirSync(dirname(absOut), { recursive: true });
  writeFileSync(absOut, output, "utf8");
  console.log(`openapi-types: ${inputPath} -> ${outputPath} (${Object.keys(spec.paths || {}).length} paths)`);
}
