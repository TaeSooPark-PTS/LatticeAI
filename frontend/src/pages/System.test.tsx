import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { fail, ok, renderPage } from "@/test/renderPage";
import { SystemPage } from "./System";

/**
 * The settings screen: seven tabs, two safety dials, and the account surface.
 *
 * Until 10.3.0 this page had no unit test — Playwright drove its happy path,
 * which cannot reach a server that is down, a workspace list that is empty, or
 * a mode that hides half the page. Those are exactly the states where a
 * settings screen misleads someone.
 */

const HEALTH = { status: "ok", version: "10.3.0", mode: "local" };
const PROFILE = { email: "me@local", nickname: "Me", role: "admin", name: "Me" };
const WORKSPACES = {
  workspaces: [
    { id: "personal", workspace_id: "personal", name: "Personal Workspace", type: "personal", status: "active" },
    { id: "team", workspace_id: "team", name: "Team", type: "organization", status: "active" },
  ],
  active_workspace: "personal",
};

function render(overrides = {}, options = {}) {
  return renderPage(<SystemPage />, {
    api: {
      health: ok(HEALTH),
      profile: ok(PROFILE),
      workspaceRegistry: ok(WORKSPACES),
      permissionMode: ok({
        mode: "strict", label: "Strict", label_ko: "엄격", risk: "low",
        requires_ack: false, circuit_breakers: true,
        catalog: [
          { id: "strict", label: "Strict", label_ko: "엄격", summary: "s", summary_ko: "엄", risk: "low", requires_ack: false },
          { id: "bypass", label: "Bypass", label_ko: "바이패스", summary: "b", summary_ko: "바", risk: "high", requires_ack: true },
        ],
      }),
      networkBoundary: ok({
        mode: "local_only", label: "Local only", label_ko: "로컬만",
        allows_cloud: false, requires_ack: false, warning_ko: null,
        policy: {}, token_budget: {},
        catalog: [
          { id: "local_only", label: "Local only", label_ko: "로컬만", summary: "l", summary_ko: "로", risk: "low", requires_ack: false },
          { id: "cloud_allowed", label: "Cloud", label_ko: "클라우드", summary: "c", summary_ko: "클", risk: "medium", requires_ack: true },
        ],
      }),
      ...overrides,
    },
    ...options,
  });
}

describe("SystemPage", () => {
  beforeEach(() => vi.restoreAllMocks());

  it("opens on the account tab and shows who is signed in", async () => {
    render();
    await waitFor(() => expect(screen.getByRole("tablist")).toBeTruthy());
    await waitFor(() => expect(screen.getByText("me@local")).toBeTruthy());
  });

  it("offers every tab as a real tab control", async () => {
    render();
    await waitFor(() => expect(screen.getByRole("tablist")).toBeTruthy());
    const tabs = screen.getAllByRole("tab");
    expect(tabs.length).toBeGreaterThanOrEqual(6);
    // aria-selected must track the active tab, or a screen reader announces
    // the wrong panel.
    expect(tabs.filter((t) => t.getAttribute("aria-selected") === "true")).toHaveLength(1);
  });

  it("moves to the settings tab and shows both safety dials together", async () => {
    render();
    await waitFor(() => expect(screen.getByRole("tablist")).toBeTruthy());
    await userEvent.click(screen.getByRole("tab", { name: "환경설정" }));

    await waitFor(() => expect(screen.getByTestId("permission-mode-panel")).toBeTruthy());
    expect(screen.getByTestId("network-boundary-panel")).toBeTruthy();
  });

  it("keeps the two dials independent: neither reads the other's state", async () => {
    render();
    await userEvent.click(screen.getByRole("tab", { name: "환경설정" }));
    await waitFor(() => expect(screen.getByTestId("permission-mode-active")).toBeTruthy());

    // The autonomy dial names its modes from the i18n table by id (the server
    // ships "엄격"/"Strict"); the boundary dial still shows the server's label.
    expect(screen.getByTestId("permission-mode-active").textContent).toBe("먼저 물어보기");
    expect(screen.getByTestId("network-boundary-active").textContent).toBe("로컬만");
  });

  it("lists workspaces on the workspace tab", async () => {
    render();
    await userEvent.click(screen.getByRole("tab", { name: "작업공간" }));
    await waitFor(() => expect(screen.getByText(/Personal Workspace/)).toBeTruthy());
    expect(screen.getByText(/Team/)).toBeTruthy();
  });

  it("reports an unavailable server rather than rendering a blank panel", async () => {
    render({ health: fail("server unavailable", {}) });
    await waitFor(() => expect(screen.getByRole("tablist")).toBeTruthy());
    await userEvent.click(screen.getByRole("tab", { name: "환경설정" }));
    // Some panel must say the request failed; silence would read as "healthy".
    await waitFor(() =>
      expect(document.body.textContent).toMatch(/요청을 처리하지 못했어요|사용할 수 없|unavailable/i));
  });

  it("renders in English when the language is en", async () => {
    render({}, { language: "en" });
    await waitFor(() => expect(screen.getByRole("tablist")).toBeTruthy());
    expect(screen.getByRole("tab", { name: "Settings" })).toBeTruthy();
    expect(screen.getByRole("tab", { name: "Account" })).toBeTruthy();
  });

  it("gates the admin panel behind the admin detail level, not just the tab", async () => {
    // The tab is always listed; opening it must not reveal admin controls.
    render({}, { mode: "advanced" });
    await userEvent.click(screen.getByRole("tab", { name: "관리자" }));
    await waitFor(() =>
      expect(document.body.textContent).toMatch(/관리자 모드|admin/i));
    // The gate, not the controls: no role table should have rendered.
    expect(screen.queryByText(/adminRoles/)).toBeNull();
  });

  it("keyboard users can move between tabs with the arrow keys", async () => {
    render();
    await waitFor(() => expect(screen.getByRole("tablist")).toBeTruthy());
    const first = screen.getAllByRole("tab")[0];
    first.focus();
    await userEvent.keyboard("{ArrowRight}");
    await waitFor(() =>
      expect(screen.getAllByRole("tab")[1].getAttribute("aria-selected")).toBe("true"));
  });

  it("shows the appearance and detail-level controls separately", async () => {
    render();
    await userEvent.click(screen.getByRole("tab", { name: "환경설정" }));
    await waitFor(() => expect(screen.getByText("화면 모양")).toBeTruthy());
    expect(screen.getByText("보여줄 내용의 양")).toBeTruthy();
  });

  it("an empty workspace list reads as empty rather than as a failure", async () => {
    render({ workspaceRegistry: ok({ workspaces: [], active_workspace: null }) });
    await userEvent.click(screen.getByRole("tab", { name: "작업공간" }));
    await waitFor(() => expect(screen.getByRole("tablist")).toBeTruthy());
    expect(document.body.textContent).not.toMatch(/undefined|NaN|\[object Object\]/);
  });
});
