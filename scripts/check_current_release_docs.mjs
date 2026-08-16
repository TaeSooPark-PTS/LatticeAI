import { existsSync, readdirSync, readFileSync } from "node:fs";
import path from "node:path";
import process from "node:process";

const root = process.cwd();
const pkg = JSON.parse(readFileSync(path.join(root, "package.json"), "utf8"));
const version = pkg.version;
const releaseDir = `output/release/v${version}`;
const releaseTheme = "Travel Light";
const title = `${version} — ${releaseTheme}`;
const escapedVersion = version.replaceAll(".", "\\.");

const currentReleaseFiles = [
  "AGENTS.md",
  "README.md",
  "ARCHITECTURE.md",
  "FEATURE_STATUS.md",
  "PRIVACY.md",
  "SECURITY.md",
  "docs/COMMUNITY_AND_PLUGINS.md",
  "docs/DEVELOPMENT.md",
  "docs/ONBOARDING.md",
  "docs/TRUST_MODEL.md",
  "docs/WHY_LATTICE.md",
  "docs/kg-schema.md",
  "vscode-extension/README.md",
];

const errors = [];

function read(rel) {
  return readFileSync(path.join(root, rel), "utf8");
}

function requireIncludes(rel, needle) {
  const text = read(rel);
  if (!text.includes(needle)) {
    errors.push(`${rel}: missing ${JSON.stringify(needle)}`);
  }
}

function assertNoCurrentDrift(rel) {
  const text = read(rel);
  const stale = text.match(
    new RegExp(`Current release:\\s+\\*\\*(?!${escapedVersion}\\b)[^*]+\\*\\*`, "i"),
  );
  if (stale) {
    errors.push(`${rel}: stale current-release marker ${JSON.stringify(stale[0])}`);
  }
  for (const match of text.matchAll(/Latest\b[^\n]*\b(\d+\.\d+\.\d+)\b/gi)) {
    if (match[1] !== version) {
      errors.push(`${rel}: stale Latest version reference ${match[1]}`);
    }
  }
  if (new RegExp(`current release is\\s+\\*\\*(?!${escapedVersion}\\b)`, "i").test(text)) {
    errors.push(`${rel}: stale README current release sentence`);
  }
}

for (const rel of currentReleaseFiles) {
  assertNoCurrentDrift(rel);
}

for (const rel of [
  "README.md",
  "ARCHITECTURE.md",
  "FEATURE_STATUS.md",
  "PRIVACY.md",
  "SECURITY.md",
]) {
  requireIncludes(rel, version);
}

// The Release Artifact Map in ARCHITECTURE.md must name current-version
// artifacts exactly — this block drifted silently before 9.9.1.
requireIncludes("ARCHITECTURE.md", `${version} exact artifact names:`);
for (const artifact of [
  `dist/ltcai-${version}-py3-none-any.whl`,
  `dist/ltcai-${version}.tar.gz`,
  `ltcai-${version}.tgz`,
  `dist/ltcai-${version}.vsix`,
  `src-tauri/target/release/bundle/dmg/Lattice AI_${version}_aarch64.dmg`,
]) {
  requireIncludes("ARCHITECTURE.md", artifact);
}
const architecture = read("ARCHITECTURE.md");
for (const match of architecture.matchAll(/ltcai-(\d+\.\d+\.\d+)/g)) {
  if (match[1] !== version) {
    errors.push(`ARCHITECTURE.md: stale artifact version reference ${match[0]}`);
  }
}

requireIncludes("README.md", `The current release is **${title}**`);
requireIncludes("README.md", `![v${version} Living Brain walkthrough]`);
requireIncludes("RELEASE.md", `## v${version} — ${releaseTheme}`);
requireIncludes("docs/CHANGELOG.md", `## [${version}]`);
requireIncludes("RELEASE_NOTES.md", `[v${version} - ${releaseTheme}]`);
requireIncludes("CHANGELOG.md", "starts at v8.0.0");
// 10.10.0 moved the public history floor from 8.0.0 to 9.0.0; 11.6.0 moved it
// again, to 11.0.0, because "One Door" rebuilt the product server in Rust and
// SECURITY.md now supports only 11.x. This gate follows the boundary the docs
// state, and `test_markdown_current_release_references_match_release` holds the
// README table to the same floor.
requireIncludes("RELEASE_NOTES.md", `11.0.0 through ${version}`);
requireIncludes("SECURITY.md", `${version.split(".").slice(0, 2).join(".")}.x (latest)`);

for (const rel of ["README.md", "RELEASE.md", "docs/CHANGELOG.md", "RELEASE_NOTES.md"]) {
  const text = read(rel);
  for (const forbidden of ["RELEASE_NOTES_v7", "output/release/v7", "## v7.", "## [7."]) {
    if (text.includes(forbidden)) {
      errors.push(`${rel}: forbidden pre-8.0 release-history reference ${forbidden}`);
    }
  }
}

const readme = read("README.md");
const mediaRefs = [...readme.matchAll(/\]\((output\/release\/v[^)]+)\)/g)].map((m) => m[1]);
if (mediaRefs.length === 0) {
  errors.push("README.md: no release evidence media references found");
}
for (const ref of mediaRefs) {
  if (!ref.startsWith(`${releaseDir}/`)) {
    errors.push(`README.md: release media must point at ${releaseDir}, found ${ref}`);
  }
  if (!existsSync(path.join(root, ref))) {
    errors.push(`README.md: missing release media target ${ref}`);
  }
}

const evidenceRoot = path.join(root, releaseDir);
if (!existsSync(evidenceRoot)) {
  errors.push(`missing current release evidence directory ${releaseDir}`);
} else {
  for (const rel of [
    "SCREENSHOT_INDEX.md",
    "screenshots/01-login.png",
    "screenshots/02-recommended-models.png",
    "screenshots/03-install-load-progress.png",
    "screenshots/04-brain-chat-home.png",
    "screenshots/12-review-center.png",
    `gifs/v${version}-living-brain-walkthrough.gif`,
  ]) {
    const target = path.join(evidenceRoot, rel);
    if (!existsSync(target)) {
      errors.push(`missing current release evidence file ${releaseDir}/${rel}`);
    }
  }
}

const releaseRoot = path.join(root, "output", "release");
if (existsSync(releaseRoot)) {
  for (const entry of readdirSync(releaseRoot)) {
    if (/^v7\./.test(entry)) {
      errors.push(`pre-8.0 release evidence should not be tracked: output/release/${entry}`);
    }
  }
}

if (errors.length) {
  console.error("Current release documentation check failed:");
  for (const error of errors) {
    console.error(`- ${error}`);
  }
  process.exit(1);
}

console.log(`Current release documentation is synchronized for ${version}.`);
