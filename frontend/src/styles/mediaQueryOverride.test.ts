import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

/**
 * A guard for the second way this project's CSS breaks silently.
 *
 * `cssLayering.test.ts` covers Tailwind utilities losing to unlayered rules.
 * This one covers a partial override inside a media query: a block that
 * re-declares `display: flex` (or `grid`) for a selector whose base rule set a
 * `flex-direction`, but does not restate the direction. The declaration that
 * *is* written looks like the whole intent; the one that is inherited quietly
 * keeps the old axis.
 *
 * It shipped exactly once and was invisible in every automated check:
 *
 *   .brain-prompt-grid            { display: flex; flex-direction: column }
 *   @media (max-height: 800px) {
 *     .brain-prompt-grid          { display: flex; flex-wrap: wrap }   ← no row
 *     .brain-prompt-grid button   { border-radius: 999px }
 *   }
 *
 * The intent was pills. The container stayed a column, so `flex-wrap` had
 * nothing to wrap and every "pill" stretched to the deck's full ~950px to
 * hold four characters. Types passed, render tests passed, and the visual
 * suite's own screenshots contained the bug rather than catching it.
 */

const ROOT = resolve(__dirname, "../../..");

const SHEETS = [
  "frontend/src/styles.css",
  // styles.css is now only the entry; its rules live in styles/core/*.css.
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

const read = (relative: string) => readFileSync(resolve(ROOT, relative), "utf8");
const stripComments = (css: string) => css.replace(/\/\*[\s\S]*?\*\//g, "");

type Rule = { selector: string; body: string; inMedia: boolean };

/**
 * Flat scan of `selector { body }` pairs, tracking whether the rule sits
 * inside an `@media` block. Deliberately not a real parser: these sheets nest
 * one level (media → rule) and nothing else, so brace counting is enough and
 * cannot silently mis-handle a construct it does not know about.
 */
function rules(css: string): Rule[] {
  const found: Rule[] = [];
  const source = stripComments(css);
  let mediaDepth = 0;
  let index = 0;

  while (index < source.length) {
    const open = source.indexOf("{", index);
    if (open === -1) break;

    const prelude = source.slice(index, open).trim();

    if (prelude.startsWith("@media") || prelude.startsWith("@supports")) {
      mediaDepth += 1;
      index = open + 1;
      continue;
    }

    const close = source.indexOf("}", open);
    if (close === -1) break;

    // At-rules with declaration bodies that are not style rules.
    if (!prelude.startsWith("@")) {
      found.push({
        selector: prelude,
        body: source.slice(open + 1, close),
        inMedia: mediaDepth > 0,
      });
    }

    index = close + 1;

    // Consume the closing braces of any media blocks that end right here.
    while (mediaDepth > 0) {
      const next = source.slice(index).match(/^\s*\}/);
      if (!next) break;
      mediaDepth -= 1;
      index += next[0].length;
    }
  }

  return found;
}

const declares = (body: string, property: string) =>
  new RegExp(`(?:^|;)\\s*${property}\\s*:`, "m").test(body);

const valueOf = (body: string, property: string) => {
  const match = body.match(new RegExp(`(?:^|;)\\s*${property}\\s*:\\s*([^;}]+)`, "m"));
  return match ? match[1].trim() : null;
};

describe("a media query that re-declares display also states the axis", () => {
  const all = SHEETS.flatMap((sheet) =>
    rules(read(sheet)).map((rule) => ({ ...rule, sheet })),
  );

  it("finds rules to check", () => {
    // Without this, an extraction regression turns every assertion below into
    // a vacuous pass.
    expect(all.length).toBeGreaterThan(500);
    expect(all.some((rule) => rule.inMedia)).toBe(true);
  });

  it.each(SHEETS)("%s restates flex-direction where it re-declares flex", (sheet) => {
    const sheetRules = all.filter((rule) => rule.sheet === sheet);

    // Selectors whose base (non-media) rule pins a non-default axis.
    const directed = new Map<string, string>();
    for (const rule of sheetRules) {
      if (rule.inMedia) continue;
      const direction = valueOf(rule.body, "flex-direction");
      if (direction && direction !== "row") directed.set(rule.selector, direction);
    }

    const violations: string[] = [];
    for (const rule of sheetRules) {
      if (!rule.inMedia) continue;
      const base = directed.get(rule.selector);
      if (!base) continue;
      // Only a rule that re-asserts the display mode is claiming to redefine
      // the box; one that just tweaks padding inside a column is fine.
      const display = valueOf(rule.body, "display");
      if (display !== "flex" && display !== "inline-flex") continue;
      if (declares(rule.body, "flex-direction")) continue;
      violations.push(
        `${rule.selector} re-declares display:flex inside @media but inherits ` +
          `flex-direction:${base} from its base rule`,
      );
    }

    expect(
      violations,
      "A media query that re-states `display: flex` reads as a fresh box, but\n" +
        "`flex-direction` still comes from the base rule. State the axis:\n\n" +
        violations.map((line) => `  ${line}`).join("\n") +
        "\n",
    ).toEqual([]);
  });

  it("keeps the suggestion chips on a row axis when height is scarce", () => {
    // The concrete instance, asserted by name so a future edit to this block
    // cannot reintroduce it without the test saying which control broke.
    const css = stripComments(read("frontend/src/styles/experience/home-simple.css"));
    const scarce = css.slice(css.indexOf("@media (max-height: 700px)"));
    const grid = scarce.slice(
      scarce.indexOf(".brain-prompt-grid"),
      scarce.indexOf(".brain-prompt-grid button"),
    );
    expect(grid).toMatch(/flex-direction:\s*row/);
  });
});
