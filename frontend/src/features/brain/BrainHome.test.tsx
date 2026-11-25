import { screen, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { renderPage } from "@/test/renderPage";
import { BrainHome } from "./BrainHome";


function renderHome(options: Parameters<typeof renderPage>[1] = {}) {
  return renderPage(<BrainHome brainState="idle" intensity={0.6} onBrainChange={() => {}} />, options);
}

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
    expect(composer).toBeGreaterThan(hero!);
    expect(toolbar).toBeGreaterThan(composer!);
    expect(prompts).toBeGreaterThan(toolbar!);
  });

  it("holds the first move on the station and nothing else", async () => {
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
    expect(deckIndex).toBeGreaterThan(stationIndex!);
    expect(quietIndex).toBeGreaterThan(deckIndex!);
  });

  it("opens with the dock closed, so the first screen is the composer", async () => {
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
    const { container } = renderHome();
    await waitFor(() => expect(container.querySelector(".brain-home-quiet")).toBeTruthy());

    const regions = container.querySelectorAll("header, footer");
    expect(regions.length).toBeGreaterThan(0);
    for (const region of regions) {
      expect(region.closest("article, aside, main, nav, section")).not.toBeNull();
    }
  });

  it("names the toolbar as a group so its label is announced", async () => {
    renderHome();
    await waitFor(() => expect(document.querySelector(".brain-station-toolbar")).toBeTruthy());
    const toolbar = document.querySelector(".brain-station-toolbar") as HTMLElement;
    expect(toolbar.getAttribute("role")).toBe("group");
    expect(toolbar.getAttribute("aria-label")?.trim()).toBeTruthy();
    expect(toolbar.getAttribute("aria-label")).not.toMatch(/^brain\./);
  });

  it("names the suggestion deck on the element that has a role", async () => {
    const { container } = renderHome();
    await waitFor(() => expect(container.querySelector("[data-testid='brain-secondary-deck']")).toBeTruthy());

    const deck = container.querySelector("[data-testid='brain-secondary-deck']") as HTMLElement;
    expect(deck.tagName).toBe("SECTION");
    const label = deck.getAttribute("aria-label");
    expect(label?.trim()).toBeTruthy();
    expect(label).not.toMatch(/^brain\./);
    expect(screen.getByRole("region", { name: label as string })).toBe(deck);

    expect(container.querySelector(".brain-home-prompt-strip")?.hasAttribute("aria-label")).toBe(false);
    expect(container.textContent).not.toMatch(/brain\.[a-z]+\.[a-zA-Z]+/);
  });

  it("falls back to starter pills when Brain has no suggestion of its own", async () => {
    const { container } = renderHome();
    await waitFor(() => expect(container.querySelector("[data-testid='brain-secondary-deck']")).toBeTruthy());

    const deck = container.querySelector("[data-testid='brain-secondary-deck']") as HTMLElement;
    expect(deck.querySelector(".brain-prompt-grid")).toBeNull();
    const row = deck.querySelector(".brain-prompt-pills-row") as HTMLElement;
    expect(row).toBeTruthy();
    const pills = row.querySelectorAll("button.brain-prompt-pill");
    expect(pills.length).toBeGreaterThan(0);
    for (const pill of pills) {
      expect(pill.textContent?.trim()).toBeTruthy();
      expect(pill.getAttribute("type")).toBe("button");
    }
  });

  it("renders in English when the language is en", async () => {
    const { container } = renderHome({ language: "en" });
    await waitFor(() => expect(container.querySelector("[data-testid='brain-home-station']")).toBeTruthy());
    expect(container.textContent).not.toMatch(/[가-힣]/);
  });
});
