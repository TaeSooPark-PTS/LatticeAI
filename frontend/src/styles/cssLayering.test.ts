import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";


const ROOT = resolve(__dirname, "../../..");

const SHEETS = [
  "frontend/src/styles.css",
  "frontend/src/styles/core/01-backdrop-onboarding.css",
  "frontend/src/styles/core/02-brain-shell.css",
  "frontend/src/styles/core/03-brain-organism.css",
  "frontend/src/styles/core/04-brain-first-screen.css",
  "frontend/src/styles/core/05-brain-messages.css",
  "frontend/src/styles/core/06-brain-evidence.css",
  "frontend/src/styles/core/07-brain-depths.css",
  "frontend/src/styles/core/08-graph-controls.css",
  "frontend/src/styles/core/09-care-admin.css",
  "frontend/src/styles/core/10-ritual-flow.css",
  "frontend/src/styles/core/11-ritual-onboarding.css",
  "frontend/src/styles/core/12-ritual-actions.css",
  "frontend/src/styles/core/13-living-presence.css",
  "frontend/src/styles/core/14-ingestion-jobs.css",
  "frontend/src/styles/core/15-workspace-switcher.css",
  "frontend/src/styles/core/16-home-rings.css",
  "frontend/src/styles/core/17-briefing-history.css",
  "frontend/src/styles/core/18-proposals-files.css",
  "frontend/src/styles/core/19-agent-steps.css",
  "frontend/src/styles/core/20-approval-watch.css",
  "frontend/src/styles/experience/shell.css",
  "frontend/src/styles/experience/conversation.css",
  "frontend/src/styles/experience/conversation-active.css",
  "frontend/src/styles/experience/conversation-fixes.css",
  "frontend/src/styles/experience/graph.css",
  "frontend/src/styles/experience/graph-home.css",
  "frontend/src/styles/experience/graph-home-dock.css",
  "frontend/src/styles/experience/home-simple.css",
  "frontend/src/styles/experience/home-dock.css",
  "frontend/src/styles/experience/capture.css",
  "frontend/src/styles/experience/product-pages.css",
  "frontend/src/styles/experience/chronicle.css",
  "frontend/src/styles/experience/responsive.css",
  "frontend/src/styles/experience/home-canvas.css",
  "frontend/src/styles/experience/brain-3d.css",
  "frontend/src/styles/experience/desktop-hud.css",
  "frontend/src/styles/experience/affordance.css",
];

const COMPONENTS = [
  "frontend/src/components/ProductFlow.tsx",
  "frontend/src/components/onboarding/LoginScreen.tsx",
  "frontend/src/components/onboarding/RecommendationScreen.tsx",
  "frontend/src/features/brain/BrainConversation.tsx",
  "frontend/src/features/brain/BrainHomeHero.tsx",
  "frontend/src/pages/Act.tsx",
  "frontend/src/features/act/InstalledAutomations.tsx",
  "frontend/src/features/review/ReviewInbox.tsx",
  "frontend/src/features/review/ReviewCard.tsx",
];

const read = (relative: string) => readFileSync(resolve(ROOT, relative), "utf8");

const withoutComments = (css: string) => css.replace(/\/\*[\s\S]*?\*\//g, "");

function ownedClasses(): Set<string> {
  const owned = new Set<string>();
  for (const sheet of SHEETS) {
    for (const match of read(sheet).matchAll(/\.(ritual-[\w-]+|brain-[\w-]+|data-panel)\b/g)) {
      owned.add(match[1]!);
    }
  }
  return owned;
}

const LAYOUT_UTILITY =
  /^(?:(?:p|px|py|pt|pb|pl|pr|m|mx|my|mt|mb|ml|mr|w|h|gap|gap-x|gap-y|space-x|space-y|max-w|max-h|min-w|min-h|top|bottom|left|right|inset|z|text|font|leading|tracking|bg|border|rounded|shadow|opacity|flex|grid|justify|items|self|content|order|col|row)-[\w./[\]-]+|flex|grid|block|inline-block|inline-flex|hidden|absolute|relative|fixed|sticky|truncate|rounded|border|shadow)$/;

function classNameGroups(source: string): string[][] {
  const groups: string[][] = [];
  const patterns = [/className\s*=\s*"([^"]*)"/g, /className\s*=\s*\{`([^`]*)`\}/g];
  for (const pattern of patterns) {
    for (const match of source.matchAll(pattern)) {
      const literal = match[1]!.replace(/\$\{[^}]*\}/g, " ");
      groups.push(literal.split(/\s+/).filter(Boolean));
    }
  }
  return groups;
}

describe("unlayered CSS owns the layout of the classes it styles", () => {
  const owned = ownedClasses();

  it("knows which classes the stylesheets claim", () => {
    expect(owned.size).toBeGreaterThan(100);
    expect(owned.has("ritual-action-row")).toBe(true);
    expect(owned.has("brain-home-prompt-strip")).toBe(true);
    expect(owned.has("data-panel")).toBe(true);
  });

  it("has no @layer in the project's own CSS, which is why unlayered wins", () => {
    const layered = SHEETS.filter((sheet) => /@layer\b/.test(withoutComments(read(sheet))));
    expect(layered).toEqual([]);
  });

  it.each(COMPONENTS)("%s puts no layout utility on a class the sheets style", (file) => {
    const violations: string[] = [];
    for (const tokens of classNameGroups(read(file))) {
      const claimed = tokens.filter((token) => owned.has(token));
      if (claimed.length === 0) continue;
      const utilities = tokens.filter((token) => LAYOUT_UTILITY.test(token));
      if (utilities.length === 0) continue;
      violations.push(`${claimed.join(" ")}  ←  ${utilities.join(" ")}`);
    }
    expect(
      violations,
      `These utilities sit on classes the stylesheets already style, so they either\n` +
        `lost silently or stacked on top. Move the intent into the CSS rule instead:\n\n` +
        violations.map((line) => `  ${line}`).join("\n") +
        "\n",
    ).toEqual([]);
  });
});
