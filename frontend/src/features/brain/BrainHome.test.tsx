import { screen, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { renderPage } from "@/test/renderPage";
import { BrainHome } from "./BrainHome";

/**
 * The Brain home on first entry — the screen behind capture 04.
 *
 * 10.6.x rebuilt it around a single vertical axis: greet, then the box you type
 * into, then three things to try, then the controls for what Brain may work
 * with. Everything that is not that first move was pushed into a quiet footer
 * one click away. The order *is* the design, and it lives in JSX, so a reorder
 * during a later edit would change the screenshot without any test objecting.
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
  it("leads with the box you type into, then what to try, then the controls", async () => {
    const { container } = renderHome();
    await waitFor(() => expect(container.querySelector("[data-testid='brain-home-station']")).toBeTruthy());

    const [hero, composer, prompts, toolbar] = order(container, [
      ".brain-hero",
      ".brain-composer-wrapper",
      ".brain-home-prompt-strip",
      ".brain-station-toolbar",
    ]);

    expect(hero).toBeGreaterThanOrEqual(0);
    expect(composer).toBeGreaterThan(hero);
    expect(prompts).toBeGreaterThan(composer);
    expect(toolbar).toBeGreaterThan(prompts);
  });

  it("keeps all four on one surface, and the shelves off it", async () => {
    // The station is the first move; the quiet footer is everything else. If a
    // shelf drifts back inside the station it competes with the composer again.
    const { container } = renderHome();
    await waitFor(() => expect(container.querySelector("[data-testid='brain-home-station']")).toBeTruthy());

    const station = container.querySelector("[data-testid='brain-home-station']") as HTMLElement;
    expect(station.querySelector("textarea")).toBeTruthy();
    expect(station.querySelector(".brain-station-toolbar")).toBeTruthy();
    expect(station.querySelector("[data-testid='brain-history-shelf']")).toBeNull();
    expect(station.querySelector("[data-testid='brain-insights-shelf']")).toBeNull();

    const [stationIndex, quietIndex] = order(container, [
      "[data-testid='brain-home-station']",
      ".brain-home-quiet",
    ]);
    expect(quietIndex).toBeGreaterThan(stationIndex);
  });

  it("opens with both shelves closed, so the first screen is the composer", async () => {
    const { container } = renderHome();
    await waitFor(() => expect(container.querySelector("[data-testid='brain-history-shelf']")).toBeTruthy());

    for (const id of ["brain-history-shelf", "brain-insights-shelf"]) {
      const shelf = container.querySelector<HTMLDetailsElement>(`[data-testid='${id}']`);
      expect(shelf).toBeTruthy();
      expect(shelf?.open).toBe(false);
    }
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

  it("labels the suggestion strip without printing an i18n key", async () => {
    const { container } = renderHome();
    await waitFor(() => expect(container.querySelector(".brain-home-prompt-strip")).toBeTruthy());
    const strip = container.querySelector(".brain-home-prompt-strip") as HTMLElement;
    expect(strip.getAttribute("aria-label")).not.toMatch(/^brain\./);
    expect(container.textContent).not.toMatch(/brain\.[a-z]+\.[a-zA-Z]+/);
  });

  it("renders in English when the language is en", async () => {
    const { container } = renderHome({ language: "en" });
    await waitFor(() => expect(container.querySelector("[data-testid='brain-home-station']")).toBeTruthy());
    // The Korean fallback leaks through when an `en` key is missing.
    expect(container.textContent).not.toMatch(/[가-힣]/);
  });
});
