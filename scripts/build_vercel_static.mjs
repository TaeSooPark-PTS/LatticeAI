import { mkdir, writeFile } from "node:fs/promises";
import { readFileSync } from "node:fs";

const pkg = JSON.parse(readFileSync(new URL("../package.json", import.meta.url), "utf8"));
const outDir = new URL("../vercel-static/", import.meta.url);

await mkdir(outDir, { recursive: true });

const html = `<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>Lattice AI ${pkg.version}</title>
    <style>
      :root {
        color-scheme: dark;
        font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
        background: #0f141b;
        color: #e5edf4;
      }
      body {
        margin: 0;
        min-height: 100vh;
        display: grid;
        place-items: center;
        padding: 32px;
      }
      main {
        max-width: 760px;
        border: 1px solid #2d3745;
        border-radius: 8px;
        padding: 32px;
        background: #151b24;
      }
      h1 {
        margin: 0 0 12px;
        font-size: 32px;
      }
      p {
        line-height: 1.6;
        color: #b8c4d2;
      }
      a {
        color: #41ddd2;
      }
      code {
        background: #0f141b;
        border: 1px solid #2d3745;
        border-radius: 4px;
        padding: 2px 6px;
      }
    </style>
  </head>
  <body>
    <main>
      <h1>Lattice AI ${pkg.version}</h1>
      <p>
        Lattice AI is a local-first desktop Digital Brain. The product runtime is the
        Tauri desktop app plus a localhost FastAPI sidecar; it is not hosted on Vercel.
      </p>
      <p>
        This Vercel build is intentionally documentation-only so Git integration checks
        do not try to deploy the desktop runtime or a fake cloud app.
      </p>
      <p>
        Use the validated desktop/package artifacts from the GitHub release process.
        Repository: <a href="${pkg.homepage}">${pkg.homepage}</a>
      </p>
      <p>Runtime route when installed locally: <code>http://127.0.0.1:4825/app</code></p>
    </main>
  </body>
</html>
`;

await writeFile(new URL("index.html", outDir), html, "utf8");
console.log(`Vercel static placeholder generated for Lattice AI ${pkg.version}`);
