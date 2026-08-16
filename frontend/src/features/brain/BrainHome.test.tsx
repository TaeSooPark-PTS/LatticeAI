import { screen, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { renderPage } from "@/test/renderPage";
import { BrainHome } from "./BrainHome";

/**
 * The Brain home on first entry — the screen behind capture 04.
 *
 * 10.6.2 split what used to be one tall card into two surfaces. The station is
 * the first move and nothing else: greet, the box you type into, then the row
 * that answers "with what, and how far" — add material and the autonomy dial.
 * The three things to try moved *out* of that card onto a deck of their own
 * below it, because a suggestion is a second choice, not part of the first one.
 * Everything quieter still lives in the footer one click away.
 *
 * The order and the split are both the design and both live in JSX, so a
 * reorder or a re-nesting during a later edit would change the screenshot
 * without any test objecting. Hence the structural assertions here.
 *
 * This file had no unit coverage at all until now, because `LivingBrain` calls
 * `window.matchMedia` on mount and jsdom leaves it undefined — every render
 * threw before its assertions ran. The shim is in `test/setup.ts`.
 */

function renderHome(options: Parameters<typeof renderPage>[1] = {}) {
  return renderPage(<BrainHome brainState="idle" intensity={0.6} onBrainChange={() => {}} />, options);
}

/** Index of a node in document order, so "before"/"after" is assertable. */
function order(container: HTMLElement, selectors: string[]) {
  const all = Array.from(container.querySelectorAll<HTMLElement>("*"));
  return selectors.map((selector) => all.indexOf(container.querySelector<HTMLElement>(selector) as HTMLElement));
}

describe("BrainHome first entry", () => {
  it("leads with the box you type into, then controls on station floor, then discovery deck prompts", async () => {
    const { container } = renderHome();
    await waitFor(() => expect(container.querySelector("[data-testid='brain-home-station']")).toBeTruthy());

    const [hero, composer, toolbar, prompts] = order(container, [
      ".brain-hero",
      ".brain-composer-wrapper",
      ".brain-station-toolbar",
      ".brain-home-prompt-strip",
    ]);

    expect(hero).toBeGreaterThanOrEqual(0);
    expect(composer).toBeGreaterThan(hero);
    expect(toolbar).toBeGreaterThan(composer);
    expect(prompts).toBeGreaterThan(toolbar);
  });

  it("holds the first move on the station and nothing else", async () => {
    // The station is the first move; the deck and the quiet footer are the
    // second and third. If a shelf — or the suggestions — drift back inside the
    // station they compete with the composer again, which is the whole reason
    // 10.6.2 split them out.
    const { container } = renderHome();
    await waitFor(() => expect(container.querySelector("[data-testid='brain-home-station']")).toBeTruthy());

    const station = container.querySelector("[data-testid='brain-home-station']") as HTMLElement;
    expect(station.querySelector("textarea")).toBeTruthy();
    expect(station.querySelector(".brain-station-toolbar")).toBeTruthy();
    expect(station.querySelector(".brain-writing-desk .brain-composer-wrapper")).toBeTruthy();
    expect(station.querySelector(".brain-writing-desk .brain-station-toolbar")).toBeTruthy();
    expect(station.querySelector(".brain-home-prompt-strip")).toBeNull();
    expect(station.querySelector("[data-testid='brain-secondary-deck']")).toBeNull();
    expect(station.querySelector("[data-testid='brain-home-dock']")).toBeNull();
    expect(station.querySelector("[data-testid='brain-home-drawer']")).toBeNull();
  });

  it("stacks station, then deck, then footer as three siblings of the stage", async () => {
    // The three surfaces are siblings, not nested. `.brain-centered-home > *`
    // in home-simple.css sets `flex: none` on each, so a surface that stopped
    // being a direct child would silently start shrinking.
    const { container } = renderHome();
    await waitFor(() => expect(container.querySelector("[data-testid='brain-secondary-deck']")).toBeTruthy());

    const stage = container.querySelector("[data-testid='brain-home-stage']") as HTMLElement;
    for (const selector of [
      "[data-testid='brain-home-station']",
      "[data-testid='brain-secondary-deck']",
      ".brain-home-quiet",
    ]) {
      expect(stage.querySelector(`:scope > ${selector}`)).toBeTruthy();
    }

    const [stationIndex, deckIndex, quietIndex] = order(container, [
      "[data-testid='brain-home-station']",
      "[data-testid='brain-secondary-deck']",
      ".brain-home-quiet",
    ]);
    expect(deckIndex).toBeGreaterThan(stationIndex);
    expect(quietIndex).toBeGreaterThan(deckIndex);
  });

  it("opens with the dock closed, so the first screen is the composer", async () => {
    // 10.10.0 moved the shelves onto a dock: a rail with 대화 · 통계 · 기억 지도
    // that opens a drawer. On first paint the drawer must not exist — the rail
    // alone is on screen, and nothing competes with the composer.
    const { container } = renderHome();
    await waitFor(() => expect(container.querySelector("[data-testid='brain-home-dock']")).toBeTruthy());

    for (const id of ["brain-dock-conversations", "brain-dock-stats", "brain-dock-map"]) {
      const button = container.querySelector<HTMLButtonElement>(`[data-testid='${id}']`);
      expect(button).toBeTruthy();
      expect(button?.getAttribute("aria-expanded")).toBe("false");
    }
    expect(document.querySelector("[data-testid='brain-home-drawer']")).toBeNull();
  });

  it("keeps the capture chips folded behind the composer's + until asked", async () => {
    // The six capture chips (문서 · 이미지 · 파일 · 폴더 · 노트 · 웹) fold
    // behind one Add control; an always-open row is the noise 10.10.0 removed.
    const { container } = renderHome();
    await waitFor(() => expect(container.querySelector("[data-testid='brain-attach-toggle']")).toBeTruthy());

    const toggle = container.querySelector<HTMLButtonElement>("[data-testid='brain-attach-toggle']");
    expect(toggle?.getAttribute("aria-expanded")).toBe("false");
    expect(container.querySelector("[data-testid='brain-attach-menu']")).toBeNull();

    toggle?.click();
    await waitFor(() => expect(container.querySelector("[data-testid='brain-attach-menu']")).toBeTruthy());
    expect(toggle?.getAttribute("aria-expanded")).toBe("true");
    expect(container.querySelector("[data-testid='brain-ingestion-dock']")).toBeTruthy();
  });

  it("scopes its header and footer so they are not page landmarks", async () => {
    // Per HTML-AAM a <header>/<footer> maps to banner/contentinfo unless it sits
    // inside article/aside/main/nav/section. The home renders a <footer> per
    // visit and a <header> once a conversation starts; both must stay scoped.
    // Asserted structurally because @testing-library/dom does not evaluate the
    // constraint and reports the landmark either way.
    const { container } = renderHome();
    await waitFor(() => expect(container.querySelector(".brain-home-quiet")).toBeTruthy());

    const regions = container.querySelectorAll("header, footer");
    expect(regions.length).toBeGreaterThan(0);
    for (const region of regions) {
      expect(region.closest("article, aside, main, nav, section")).not.toBeNull();
    }
  });

  it("names the toolbar as a group so its label is announced", async () => {
    // "무엇을 가지고, 어디까지" is one question; the row answering it is one
    // named group rather than two unlabelled strips.
    renderHome();
    await waitFor(() => expect(document.querySelector(".brain-station-toolbar")).toBeTruthy());
    const toolbar = document.querySelector(".brain-station-toolbar") as HTMLElement;
    expect(toolbar.getAttribute("role")).toBe("group");
    expect(toolbar.getAttribute("aria-label")?.trim()).toBeTruthy();
    // A label sourced from a missing key renders the key itself.
    expect(toolbar.getAttribute("aria-label")).not.toMatch(/^brain\./);
  });

  it("names the suggestion deck on the element that has a role", async () => {
    // `aria-label` on a plain <div> is discarded: an element with no role has
    // nothing to name. The label has to sit on the <section>, which a name
    // promotes to a `region`. The strip inside it must not carry one, or the
    // next reader assumes a div can be labelled and repeats the mistake.
    const { container } = renderHome();
    await waitFor(() => expect(container.querySelector("[data-testid='brain-secondary-deck']")).toBeTruthy());

    const deck = container.querySelector("[data-testid='brain-secondary-deck']") as HTMLElement;
    expect(deck.tagName).toBe("SECTION");
    const label = deck.getAttribute("aria-label");
    expect(label?.trim()).toBeTruthy();
    // A label sourced from a missing key renders the key itself.
    expect(label).not.toMatch(/^brain\./);
    expect(screen.getByRole("region", { name: label as string })).toBe(deck);

    expect(container.querySelector(".brain-home-prompt-strip")?.hasAttribute("aria-label")).toBe(false);
    expect(container.textContent).not.toMatch(/brain\.[a-z]+\.[a-zA-Z]+/);
  });

  it("falls back to starter pills when Brain has no suggestion of its own", async () => {
    // The two suggestion branches are covered by different suites, which is
    // easy to lose track of: `stubApi` resolves the brief to `{}`, so every
    // render here takes the starter-pill path, while the Playwright mock
    // returns two questions and only ever renders the card grid. Asserted so
    // that a harness change flipping the branch shows up as a failure rather
    // than as a silently uncovered path.
    const { container } = renderHome();
    await waitFor(() => expect(container.querySelector("[data-testid='brain-secondary-deck']")).toBeTruthy());

    const deck = container.querySelector("[data-testid='brain-secondary-deck']") as HTMLElement;
    expect(deck.querySelector(".brain-prompt-grid")).toBeNull();
    const row = deck.querySelector(".brain-prompt-pills-row") as HTMLElement;
    expect(row).toBeTruthy();
    const pills = row.querySelectorAll("button.brain-prompt-pill");
    expect(pills.length).toBeGreaterThan(0);
    // A starter pill fills the composer rather than sending; the empty state
    // must never post a question the reader did not choose to ask.
    for (const pill of pills) {
      expect(pill.textContent?.trim()).toBeTruthy();
      expect(pill.getAttribute("type")).toBe("button");
    }
  });

  it("renders in English when the language is en", async () => {
    const { container } = renderHome({ language: "en" });
    await waitFor(() => expect(container.querySelector("[data-testid='brain-home-station']")).toBeTruthy());
    // The Korean fallback leaks through when an `en` key is missing.
    expect(container.textContent).not.toMatch(/[가-힣]/);
  });
});
