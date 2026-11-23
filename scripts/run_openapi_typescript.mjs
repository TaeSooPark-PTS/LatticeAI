#!/usr/bin/env node
/**
 * Runs `openapi-typescript` while isolating its internal `import ts from "typescript"`
 * call to `openapi-ts5` (`npm:typescript@^5.9.3`), allowing the workspace
 * itself to compile and typecheck on TypeScript 7 (`tsgo`).
 */
import { register } from "node:module";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const repo = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const legacyTsUrl = pathToFileURL(
  join(repo, "node_modules", "openapi-ts5", "lib", "typescript.js"),
).href;

const hookSource = `
export async function resolve(specifier, context, nextResolve) {
  if (specifier === "typescript" && context.parentURL?.includes("/openapi-typescript/")) {
    return { url: ${JSON.stringify(legacyTsUrl)}, shortCircuit: true };
  }
  return nextResolve(specifier, context);
}
`;

register(`data:text/javascript;charset=utf-8,${encodeURIComponent(hookSource)}`);

await import(pathToFileURL(join(repo, "node_modules", "openapi-typescript", "bin", "cli.js")).href);
