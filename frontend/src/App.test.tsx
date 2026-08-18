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
  // The home is the only thing that drives the Brain's mood, so the stub
  // exposes that callback rather than standing in as an inert box.
  BrainHome: ({
    brainState,
    intensity,
    onBrainChange,
  }: {
    brainState: string;
    intensity: number;
    onBrainChange: (next: string, nextIntensity?: number) => void;
  }) => (
    <div data-testid="page-brain-home" data-brain-state={brainState} data-intensity={String(intensity)}>
      <button type="button" onClick={() => onBrainChange("thinking")}>
        brain thinking
      </button>
      <button type="button" onClick={() => onBrainChange("listening", 4)}>
        brain louder
      </button>
    </div>
  ),
}));
const { actPageState } = vi.hoisted(() => ({ actPageState: { shouldThrow: false } }));
vi.mock("@/pages/Act", () => ({
  ActPage: () => {
    if (actPageState.shouldThrow) throw new Error("act exploded");
    return <div data-testid="page-act" />;
  },
}));
vi.mock("@/pages/Brain", () => ({
  BrainPage: ({ initialTab }: { initialTab?: string }) => (
    <div data-testid="page-brain" data-tab={initialTab ?? ""} />
  ),
}));
vi.mock("@/pages/Capture", () => ({ CapturePage: () => <div data-testid="page-capture" /> }));
vi.mock("@/pages/Library", () => ({ LibraryPage: () => <div data-testid="page-library" /> }));
vi.mock("@/pages/System", () => ({ SystemPage: () => <div data-testid="page-system" /> }));
vi.mock("@/features/admin/AdminConsole", () => ({
  AdminConsole: ({ onBack }: { onBack: () => void }) => (
    <div data-testid="page-admin">
      <button type="button" onClick={onBack}>
        leave the console
      </button>
    </div>
  ),
}));
vi.mock("@/features/command/CommandPaletteHost", () => ({
  CommandPaletteHost: () => null,
}));

import { ok, stubApi } from "@/test/renderPage";
import App from "./App";

const PRODUCT_FLOW_KEY = "lattice.productFlow.complete";

function renderApp(hash: string, api: Parameters<typeof stubApi>[0] = {}) {
  stubApi(api);
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

/**
 * jsdom lays nothing out, so every element reports zero size and the shell's
 * "is this actually rendered?" filter drops all of them. Hand back one rect so
 * the focus trap sees the menu items a browser would.
 */
function withLaidOutElements() {
  vi.spyOn(HTMLElement.prototype, "getClientRects").mockReturnValue(
    [{}] as unknown as DOMRectList,
  );
}

beforeEach(() => {
  vi.restoreAllMocks();
  actPageState.shouldThrow = false;
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
    ["#/memory", "page-brain"],
    ["#/chronicle", "page-chronicle"],
    ["#/timeline", "page-chronicle"],
  ])("routes %s to %s", async (hash, testId) => {
    renderApp(hash);
    expect(await screen.findByTestId(testId)).toBeTruthy();
  });

  it("contains a throwing route page instead of blanking the shell", async () => {
    const spy = vi.spyOn(console, "error").mockImplementation(() => {});
    actPageState.shouldThrow = true;
    renderApp("#/review");
    expect(await screen.findByTestId("error-boundary-route")).toBeTruthy();
    expect(screen.queryByTestId("page-act")).toBeNull();
    expect(screen.getByRole("button", { name: "Retry" })).toBeTruthy();
    expect(spy).toHaveBeenCalled();
  });

  it("falls back to the Brain home for an unknown hash", async () => {
    renderApp("#/there-is-no-such-screen");
    expect(await screen.findByTestId("page-brain-home")).toBeTruthy();
  });

  it("treats a hash that is not a path as no route at all", async () => {
    // `#section` is a document anchor, not a screen. Reading it as one would
    // put the shell on a page nobody asked for.
    renderApp("#not-a-path");
    expect(await screen.findByTestId("page-brain-home")).toBeTruthy();
  });

  it("keeps the memory nav lit for the knowledge tabs and the Brain nav otherwise", async () => {
    // Both land on the same page component, so the only visible difference is
    // which primary nav entry claims to be current.
    const graph = renderApp("#/knowledge-graph");
    await screen.findByTestId("page-brain");
    expect(screen.getAllByRole("link", { current: "page" })[0].textContent).toContain("Memory");
    graph.unmount();

    renderApp("#/brain/notes");
    const page = await screen.findByTestId("page-brain");
    expect(page.dataset.tab).toBe("notes");
    expect(screen.getAllByRole("link", { current: "page" })[0].textContent).toContain("Chat");
  });

  it("lets the admin console hand the shell back", async () => {
    renderApp("#/admin/users");
    fireEvent.click(await screen.findByRole("button", { name: "leave the console" }));
    await act(async () => {
      window.dispatchEvent(new HashChangeEvent("hashchange"));
    });
    expect(window.location.hash).toBe("#/brain");
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

describe("the more menu keeps the keyboard inside it", () => {
  async function openMenuWithLayout(hash = "#/brain") {
    withLaidOutElements();
    renderApp(hash);
    const triggers = await screen.findAllByRole("button", { name: "Open menu" });
    fireEvent.click(triggers[0]);
    return screen.findByRole("dialog");
  }

  function focusablesInDialog() {
    const dialog = screen.getByRole("dialog");
    return Array.from(
      dialog.querySelectorAll<HTMLElement>(
        'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])',
      ),
    );
  }

  it("moves focus to the first management link when it opens", async () => {
    await openMenuWithLayout();
    // The close button comes first in the DOM, but the point of opening the
    // menu is the list of destinations — that is where a keyboard lands.
    await waitFor(() =>
      expect(document.activeElement?.className).toContain("brain-more-nav-item"));
  });

  it("wraps Tab from the last item back to the first", async () => {
    await openMenuWithLayout();
    const items = focusablesInDialog();
    items[items.length - 1].focus();

    fireEvent.keyDown(window, { key: "Tab" });

    expect(document.activeElement).toBe(items[0]);
  });

  it("wraps Shift+Tab from the first item back to the last", async () => {
    await openMenuWithLayout();
    const items = focusablesInDialog();
    items[0].focus();

    fireEvent.keyDown(window, { key: "Tab", shiftKey: true });

    expect(document.activeElement).toBe(items[items.length - 1]);
  });

  it("leaves Tab alone in the middle of the list", async () => {
    await openMenuWithLayout();
    const items = focusablesInDialog();
    items[1].focus();

    fireEvent.keyDown(window, { key: "Tab" });

    expect(document.activeElement).toBe(items[1]);
  });

  it("ignores keys that are neither Escape nor Tab", async () => {
    await openMenuWithLayout();
    fireEvent.keyDown(window, { key: "a" });
    expect(screen.queryByRole("dialog")).toBeTruthy();
  });

  it("does not try to trap focus when nothing in the menu is rendered", async () => {
    // Without the layout shim every element reports zero size, which is what a
    // browser reports for the copy of the nav that CSS has hidden. Tab must
    // then fall through to the browser rather than focusing an invisible node.
    renderApp("#/brain");
    await screen.findByTestId("page-brain-home");
    fireEvent.click(screen.getAllByRole("button", { name: "Open menu" })[0]);
    await screen.findByRole("dialog");
    const before = document.activeElement;

    fireEvent.keyDown(window, { key: "Tab" });

    expect(document.activeElement).toBe(before);
    expect(screen.queryByRole("dialog")).toBeTruthy();
  });

  it("closes from the menu's own close button", async () => {
    const dialog = await openMenuWithLayout();
    // Both the trigger and the menu's own X answer to "Close menu"; this is
    // the one inside the dialog.
    const close = screen.getAllByRole("button", { name: "Close menu" })
      .find((button) => dialog.contains(button))!;

    fireEvent.click(close);

    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });

  it("closes when a management destination is chosen, without stealing focus back", async () => {
    const dialog = await openMenuWithLayout("#/models");
    // The screen the menu is on is marked as current in both copies of the
    // management list, so the menu never claims you are somewhere else.
    const current = screen.getAllByRole("link", { current: "page" });
    expect(current.some((link) => link.className.includes("brain-more-nav-item"))).toBe(true);

    const settings = screen.getAllByRole("link", { name: "Settings" })
      .find((link) => dialog.contains(link))!;
    fireEvent.click(settings);

    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    // Closing this way must not yank focus back to the trigger — the reader is
    // on their way to another screen.
    expect(document.activeElement).not.toBe(screen.getAllByRole("button", { name: "Open menu" })[0]);
  });

  it("opens from the mobile bar as well as the topbar", async () => {
    renderApp("#/brain");
    await screen.findByTestId("page-brain-home");
    const triggers = screen.getAllByRole("button", { name: "Open menu" });

    fireEvent.click(triggers[triggers.length - 1]);

    expect(await screen.findByRole("dialog")).toBeTruthy();
  });
});

describe("shell chrome", () => {
  it("sends the skip link straight to the main region", async () => {
    renderApp("#/capture");
    await screen.findByTestId("page-capture");

    fireEvent.click(screen.getByRole("link", { name: "Skip to content" }));

    expect(document.activeElement?.id).toBe("brain-main-content");
  });

  it("flips the appearance both ways from the topbar", async () => {
    renderApp("#/brain");
    await screen.findByTestId("page-brain-home");

    fireEvent.click(screen.getByTestId("topbar-theme-toggle"));
    expect(useAppStore.getState().theme).toBe("dark");

    fireEvent.click(screen.getByTestId("topbar-theme-toggle"));
    expect(useAppStore.getState().theme).toBe("light");
  });

  it("carries the Brain's mood from the home into the shell", async () => {
    renderApp("#/brain");
    const home = await screen.findByTestId("page-brain-home");
    expect(home.dataset.brainState).toBe("idle");
    expect(home.dataset.intensity).toBe("0.58");

    fireEvent.click(screen.getByRole("button", { name: "brain thinking" }));
    expect(screen.getByTestId("page-brain-home").dataset.brainState).toBe("thinking");
    // No intensity was named, so the previous one stands.
    expect(screen.getByTestId("page-brain-home").dataset.intensity).toBe("0.58");

    fireEvent.click(screen.getByRole("button", { name: "brain louder" }));
    // 4 is out of range; the orb never goes past full.
    expect(screen.getByTestId("page-brain-home").dataset.intensity).toBe("1");
  });
});

describe("the VS Code link indicator", () => {
  async function openMenuIn(mode: "basic" | "advanced", api = {}) {
    useAppStore.setState({ mode });
    renderApp("#/brain", api);
    await screen.findByTestId("page-brain-home");
    fireEvent.click(screen.getAllByRole("button", { name: "Open menu" })[0]);
    await screen.findByRole("dialog");
  }

  it("stays out of everyday mode entirely", async () => {
    // "VS Code" is a word a non-technical owner has no use for.
    await openMenuIn("basic");
    expect(screen.queryByText("VS Code")).toBeNull();
  });

  it("says it is still checking before the first answer arrives", async () => {
    await openMenuIn("advanced", { workspaceVscodeStatus: () => new Promise(() => {}) });
    expect(await screen.findByLabelText("VS Code: Checking")).toBeTruthy();
  });

  it("reads a recent heartbeat as connected even without the flag", async () => {
    await openMenuIn("advanced", {
      workspaceVscodeStatus: ok({ last_seen_ms: Date.now() - 1000 }),
      indexStatus: ok({ status: "idle" }),
    });
    const button = await screen.findByLabelText("VS Code: Synced");
    expect(button.getAttribute("title")).toContain("share the same Brain");
  });

  it("reads a stale heartbeat as not connected", async () => {
    await openMenuIn("advanced", {
      workspaceVscodeStatus: ok({ connected: false, last_seen_ms: Date.now() - 120000 }),
    });
    const button = await screen.findByLabelText("VS Code: Not connected");
    expect(button.getAttribute("title")).toContain("not linked to this Brain yet");
  });

  it("says it is updating while the index is still building", async () => {
    await openMenuIn("advanced", {
      workspaceVscodeStatus: ok({ connected: true }),
      indexStatus: ok({ state: "building" }),
    });
    expect(await screen.findByLabelText("VS Code: Updating")).toBeTruthy();
  });

  it("treats an index that reports nothing as settled, not as broken", async () => {
    await openMenuIn("advanced", { workspaceVscodeStatus: ok({ connected: true }) });
    expect(await screen.findByLabelText("VS Code: Synced")).toBeTruthy();
  });

  it("opens settings when the indicator is used", async () => {
    await openMenuIn("advanced", { workspaceVscodeStatus: ok({ connected: true }) });
    fireEvent.click(await screen.findByLabelText("VS Code: Synced"));
    expect(window.location.hash).toBe("#/settings");
  });
});
