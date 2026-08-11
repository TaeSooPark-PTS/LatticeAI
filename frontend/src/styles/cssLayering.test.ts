import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

/**
 * A guard for the one rule this frontend keeps breaking.
 *
 * `frontend/src/styles.css` pulls in Tailwind, which puts every utility in
 * `@layer utilities`. The project's own CSS — the rest of styles.css and all of
 * `styles/experience/*.css` — declares no `@layer` at all, and unlayered CSS
 * beats layered CSS regardless of specificity or source order. So a Tailwind
 * utility written on an element that the project's CSS already styles goes one
 * of two ways:
 *
 *   1. It dies, when the stylesheet sets that property. `justify-between` on
 *      `.ritual-action-row` (which sets `justify-content: center`) reads like a
 *      left/right split and renders centred.
 *   2. It stacks, when the stylesheet does not. This one is worse, because it
 *      silently grows the box — `p-6` and `space-y-*` on `.brain-home-station`
 *      once added ~150px and pushed a control strip off a short screen.
 *
 * Neither shows up in a type check or a render test, and the second barely
 * shows up in a screenshot. So it is asserted here instead: layout for a class
 * this project styles is owned by the stylesheet, full stop.
 */

const ROOT = resolve(__dirname, "../../..");

/** Sheets whose rules are unlayered and therefore always win. */
const SHEETS = [
  "frontend/src/styles.css",
  // styles.css is now only the entry — its rules live in styles/core/*.css,
  // pulled back in by @import in this order. Every one of them is unlayered.
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
  "frontend/src/styles/experience/capture.css",
  "frontend/src/styles/experience/chronicle.css",
  "frontend/src/styles/experience/responsive.css",
  "frontend/src/styles/experience/affordance.css",
];

/** The components reorganised for the 10.6.x layout pass. */
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

/** Comments explain this rule in several of these sheets; they are not code. */
const withoutComments = (css: string) => css.replace(/\/\*[\s\S]*?\*\//g, "");

/**
 * Every hand-written class the sheets style. Only the project's own prefixes:
 * a Tailwind utility never appears as a selector in these files.
 */
function ownedClasses(): Set<string> {
  const owned = new Set<string>();
  for (const sheet of SHEETS) {
    for (const match of read(sheet).matchAll(/\.(ritual-[\w-]+|brain-[\w-]+|data-panel)\b/g)) {
      owned.add(match[1]);
    }
  }
  return owned;
}

/**
 * Utilities that set a box's geometry or surface — the families that collide.
 * Behavioural ones (`cursor-*`, `transition-*`, `sr-only`, `animate-*`) and
 * responsive/state variants are left out: they are additive on purpose.
 */
const LAYOUT_UTILITY =
  /^(?:(?:p|px|py|pt|pb|pl|pr|m|mx|my|mt|mb|ml|mr|w|h|gap|gap-x|gap-y|space-x|space-y|max-w|max-h|min-w|min-h|top|bottom|left|right|inset|z|text|font|leading|tracking|bg|border|rounded|shadow|opacity|flex|grid|justify|items|self|content|order|col|row)-[\w./[\]-]+|flex|grid|block|inline-block|inline-flex|hidden|absolute|relative|fixed|sticky|truncate|rounded|border|shadow)$/;

/** Static class tokens from every `className` in a component. */
function classNameGroups(source: string): string[][] {
  const groups: string[][] = [];
  const patterns = [/className\s*=\s*"([^"]*)"/g, /className\s*=\s*\{`([^`]*)`\}/g];
  for (const pattern of patterns) {
    for (const match of source.matchAll(pattern)) {
      // Interpolations are conditional and cannot be judged statically.
      const literal = match[1].replace(/\$\{[^}]*\}/g, " ");
      groups.push(literal.split(/\s+/).filter(Boolean));
    }
  }
  return groups;
}

describe("unlayered CSS owns the layout of the classes it styles", () => {
  const owned = ownedClasses();

  it("knows which classes the stylesheets claim", () => {
    // A sanity check on the extraction: if this ever empties out, every
    // assertion below would pass by finding nothing to check.
    expect(owned.size).toBeGreaterThan(100);
    expect(owned.has("ritual-action-row")).toBe(true);
    expect(owned.has("brain-home-prompt-strip")).toBe(true);
    expect(owned.has("data-panel")).toBe(true);
  });

  it("has no @layer in the project's own CSS, which is why unlayered wins", () => {
    // The rule this whole file rests on. If someone wraps these sheets in a
    // layer, the precedence flips and the guard below stops being the truth.
    // Reported as a list of filenames — asserting on the text itself would
    // print an entire stylesheet on failure.
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
