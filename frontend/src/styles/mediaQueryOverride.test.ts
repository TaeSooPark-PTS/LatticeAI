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
  "frontend/src/styles/experience/brain-3d.css",
  "frontend/src/styles/experience/desktop-hud.css",
  "frontend/src/styles/experience/affordance.css",
];

const read = (relative: string) => readFileSync(resolve(ROOT, relative), "utf8");
const stripComments = (css: string) => css.replace(/\/\*[\s\S]*?\*\//g, "");

type Rule = { selector: string; body: string; inMedia: boolean };

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

    if (!prelude.startsWith("@")) {
      found.push({
        selector: prelude,
        body: source.slice(open + 1, close),
        inMedia: mediaDepth > 0,
      });
    }

    index = close + 1;

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
  return match ? match[1]!.trim() : null;
};

describe("a media query that re-declares display also states the axis", () => {
  const all = SHEETS.flatMap((sheet) =>
    rules(read(sheet)).map((rule) => ({ ...rule, sheet })),
  );

  it("finds rules to check", () => {
    expect(all.length).toBeGreaterThan(500);
    expect(all.some((rule) => rule.inMedia)).toBe(true);
  });

  it.each(SHEETS)("%s restates flex-direction where it re-declares flex", (sheet) => {
    const sheetRules = all.filter((rule) => rule.sheet === sheet);

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
    const css = stripComments(read("frontend/src/styles/experience/home-simple.css"));
    const scarce = css.slice(css.indexOf("@media (max-height: 700px)"));
    const grid = scarce.slice(
      scarce.indexOf(".brain-prompt-grid"),
      scarce.indexOf(".brain-prompt-grid button"),
    );
    expect(grid).toMatch(/flex-direction:\s*row/);
  });
});
