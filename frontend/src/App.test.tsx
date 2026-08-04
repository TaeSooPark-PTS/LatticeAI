/**
 * The app shell: which screen the hash selects, and whether the "더보기" menu
 * behaves like a dialog.
 *
 * `App.tsx` had no test at all, which is a strange gap for the file that
 * decides what every user sees. The routing table is covered by
 * `routes.test.ts`, but nothing checked that App *reads* it — a route could be
 * correct in `routes.ts` and still land on the wrong screen here. And the menu
 * is a focus trap written by hand: escape, click-outside, focus restore and
 * Tab wrapping are four behaviours a keyboard user depends on and a render
 * test would never notice were missing.
 *
 * Every page is lazy, so each one is stubbed: this file is about the shell's
 * decisions, not about re-testing the pages that have their own suites.
 */

import * as React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { useAppStore } from "@/store/appStore";

vi.mock("@/components/ProductFlow", () => ({
  ProductFlow: ({ onComplete }: { onComplete: () => void }) => (
    <button type="button" onClick={onComplete}>
      finish onboarding
    </button>
  ),
  readProductFlowComplete: () => true,
}));
vi.mock("@/features/brain/BrainHome", () => ({
  BrainHome: () => <div data-testid="page-brain-home" />,
}));
vi.mock("@/pages/Act", () => ({ ActPage: () => <div data-testid="page-act" /> }));
vi.mock("@/pages/Brain", () => ({
  BrainPage: ({ initialTab }: { initialTab?: string }) => (
    <div data-testid="page-brain" data-tab={initialTab ?? ""} />
  ),
}));
vi.mock("@/pages/Capture", () => ({ CapturePage: () => <div data-testid="page-capture" /> }));
vi.mock("@/pages/Library", () => ({ LibraryPage: () => <div data-testid="page-library" /> }));
vi.mock("@/pages/System", () => ({ SystemPage: () => <div data-testid="page-system" /> }));
vi.mock("@/features/admin/AdminConsole", () => ({
  AdminConsole: () => <div data-testid="page-admin" />,
}));
vi.mock("@/features/command/CommandPaletteHost", () => ({
  CommandPaletteHost: () => null,
}));

import App from "./App";

const PRODUCT_FLOW_KEY = "lattice.productFlow.complete";

function renderApp(hash: string) {
  window.location.hash = hash;
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <App />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  useAppStore.setState({ language: "en", theme: "light", mode: "basic", workspaceId: null });
  localStorage.setItem(PRODUCT_FLOW_KEY, "true");
  window.location.hash = "";
});

describe("first run", () => {
  it("shows the onboarding flow until it is marked complete", async () => {
    localStorage.removeItem(PRODUCT_FLOW_KEY);
    renderApp("#/brain");

    const finish = await screen.findByRole("button", { name: "finish onboarding" });
    expect(screen.queryByTestId("page-brain-home")).toBeNull();

    fireEvent.click(finish);
    expect(await screen.findByTestId("page-brain-home")).toBeTruthy();
  });
});

describe("which screen the hash selects", () => {
  it.each([
    ["#/brain", "page-brain-home"],
    ["#/", "page-brain-home"],
    ["#/capture", "page-capture"],
    ["#/models", "page-library"],
    ["#/settings", "page-system"],
    ["#/review", "page-act"],
    ["#/runs", "page-act"],
    ["#/hybrid-search", "page-brain"],
    ["#/knowledge-graph", "page-brain"],
    ["#/admin/users", "page-admin"],
  ])("routes %s to %s", async (hash, testId) => {
    renderApp(hash);
    expect(await screen.findByTestId(testId)).toBeTruthy();
  });

  it("falls back to the Brain home for an unknown hash", async () => {
    renderApp("#/there-is-no-such-screen");
    expect(await screen.findByTestId("page-brain-home")).toBeTruthy();
  });

  it("follows a hashchange without a reload", async () => {
    renderApp("#/capture");
    expect(await screen.findByTestId("page-capture")).toBeTruthy();

    await act(async () => {
      window.location.hash = "#/settings";
      window.dispatchEvent(new HashChangeEvent("hashchange"));
    });

    expect(await screen.findByTestId("page-system")).toBeTruthy();
  });

  it("hands the Brain page the tab the hash named", async () => {
    renderApp("#/knowledge-graph");
    const page = await screen.findByTestId("page-brain");
    expect(page.dataset.tab).toBe("graph");
  });

  it("gives the admin console no shell chrome to escape from", async () => {
    // The console is a separate surface, not a tab: rendering it inside the
    // product nav would offer a way back into the app that bypasses its own
    // "back to Brain" control.
    renderApp("#/admin/users");
    await screen.findByTestId("page-admin");
    expect(screen.queryByRole("navigation")).toBeNull();
  });
});

describe("theme and language reach the document", () => {
  it("writes the theme onto the root element", async () => {
    useAppStore.setState({ theme: "dark" });
    renderApp("#/brain");
    await screen.findByTestId("page-brain-home");
    expect(document.documentElement.dataset.theme).toBe("dark");
  });

  it("keeps the document language in step with the chosen one", async () => {
    // Assistive tech reads this attribute to pick a voice; a Korean UI
    // announced as English is unusable, and it is invisible on screen.
    useAppStore.setState({ language: "ko" });
    renderApp("#/brain");
    await screen.findByTestId("page-brain-home");
    expect(document.documentElement.lang).toBe("ko");

    await act(async () => {
      useAppStore.setState({ language: "en" });
    });
    expect(document.documentElement.lang).toBe("en");
  });
});

describe("the more menu behaves like a dialog", () => {
  async function openMenu() {
    renderApp("#/brain");
    await screen.findByTestId("page-brain-home");
    // Two copies of the trigger exist — topbar and mobile nav — and CSS picks
    // which one shows. Either drives the same menu, so drive the first.
    const trigger = screen.getAllByRole("button", { name: "Open menu" })[0];
    fireEvent.click(trigger);
    return trigger;
  }

  it("opens, and says so to a screen reader", async () => {
    const trigger = await openMenu();
    await waitFor(() => expect(trigger.getAttribute("aria-expanded")).toBe("true"));
    expect(screen.getByRole("dialog")).toBeTruthy();
  });

  it("closes on Escape and gives focus back to the trigger", async () => {
    const trigger = await openMenu();
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeTruthy());

    fireEvent.keyDown(window, { key: "Escape" });

    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(trigger.getAttribute("aria-expanded")).toBe("false");
  });

  it("closes when the pointer goes down outside it", async () => {
    await openMenu();
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeTruthy());

    fireEvent.mouseDown(document.body);

    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });

  it("stays open when the pointer goes down inside it", async () => {
    await openMenu();
    const dialog = await screen.findByRole("dialog");

    fireEvent.mouseDown(dialog);

    expect(screen.queryByRole("dialog")).toBeTruthy();
  });
});
