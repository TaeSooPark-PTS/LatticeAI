/**
 * The workspace/profile switcher is the shell's answer to "whose Brain am I
 * looking at" — the active workspace, the signed-in owner, and the one-click
 * path between workspaces. Until now it had no test at all, so a broken
 * popover or a switch that silently kept the old workspace id would have
 * shipped unseen.
 */

import { fireEvent, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { useAppStore } from "@/store/appStore";
import { ok, renderPage } from "@/test/renderPage";
import { WorkspaceProfileSwitcher } from "./WorkspaceProfileSwitcher";

const REGISTRY = {
  workspaces: [
    { workspace_id: "w1", name: "Personal" },
    { id: "w2", name: " " },
    {},
  ],
};

describe("WorkspaceProfileSwitcher", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    window.location.hash = "";
    useAppStore.setState({ workspaceId: null });
  });

  it("names the active workspace and softens the seeded developer profile", async () => {
    useAppStore.setState({ workspaceId: "w1" });
    renderPage(<WorkspaceProfileSwitcher language="ko" />, {
      api: {
        profile: ok({ name: "Local User" }),
        workspaceRegistry: ok(REGISTRY),
      },
    });

    // "Local User" is the backend seed, not a person — show the friendly label.
    await waitFor(() => expect(screen.getByText("내 계정")).toBeTruthy());
    expect(screen.getByText("Personal")).toBeTruthy();

    const trigger = screen.getByRole("button", { name: "작업공간 및 프로필" });
    await userEvent.click(trigger);
    expect(trigger.getAttribute("aria-expanded")).toBe("true");

    const dialog = screen.getByRole("dialog");
    // The active row is flagged; the others offer a switch.
    expect(screen.getByText("사용 중")).toBeTruthy();
    expect(dialog.textContent).toContain("w2");
    // A row with no usable name or id still reads as a personal Brain.
    expect(dialog.textContent).toContain("개인 Brain");

    await userEvent.click(screen.getByText("w2"));
    expect(useAppStore.getState().workspaceId).toBe("w2");
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("keeps the current workspace when its own row or a blank row is pressed", async () => {
    useAppStore.setState({ workspaceId: "w1" });
    renderPage(<WorkspaceProfileSwitcher language="ko" />, {
      api: { workspaceRegistry: ok(REGISTRY) },
    });
    await waitFor(() => expect(screen.getByText("Personal")).toBeTruthy());

    await userEvent.click(screen.getByRole("button", { name: "작업공간 및 프로필" }));
    await userEvent.click(screen.getByText("Personal", { selector: ".workspace-profile-item-name" }));
    expect(useAppStore.getState().workspaceId).toBe("w1");
    expect(screen.queryByRole("dialog")).toBeNull();

    await userEvent.click(screen.getByRole("button", { name: "작업공간 및 프로필" }));
    await userEvent.click(screen.getByText("개인 Brain", { selector: ".workspace-profile-item-name" }));
    expect(useAppStore.getState().workspaceId).toBe("w1");
  });

  it("shows the signed-in email and links to account and workspace settings", async () => {
    renderPage(<WorkspaceProfileSwitcher language="ko" />, {
      api: { profile: ok({ email: "me@x.io" }) },
    });
    await waitFor(() => expect(screen.getAllByText("me@x.io").length).toBeGreaterThan(0));
    // No workspace selected and none registered: a personal Brain, said plainly.
    expect(screen.getByText("개인 Brain")).toBeTruthy();

    await userEvent.click(screen.getByRole("button", { name: "작업공간 및 프로필" }));
    expect(screen.getByText("아직 작업공간이 없습니다")).toBeTruthy();
    await userEvent.click(screen.getByText("계정 설정 열기"));
    expect(window.location.hash).toBe("#/account");
    expect(screen.queryByRole("dialog")).toBeNull();

    await userEvent.click(screen.getByRole("button", { name: "작업공간 및 프로필" }));
    await userEvent.click(screen.getByText("작업공간 관리"));
    expect(window.location.hash).toBe("#/workspace-admin");
  });

  it("admits when nobody is signed in and shows an unmatched workspace id as-is", async () => {
    useAppStore.setState({ workspaceId: "ghost-7" });
    renderPage(<WorkspaceProfileSwitcher language="ko" />, {
      api: { profile: ok({}), workspaceRegistry: ok({ workspaces: [] }) },
    });
    await waitFor(() => expect(screen.getAllByText("로그인하지 않음").length).toBeGreaterThan(0));
    expect(screen.getByText("ghost-7")).toBeTruthy();
  });

  it("closes on an outside press or Escape, and only then", async () => {
    renderPage(<WorkspaceProfileSwitcher language="ko" />, {
      api: { workspaceRegistry: ok(REGISTRY) },
    });
    await waitFor(() => expect(screen.getByRole("button", { name: "작업공간 및 프로필" })).toBeTruthy());

    await userEvent.click(screen.getByRole("button", { name: "작업공간 및 프로필" }));
    expect(screen.getByRole("dialog")).toBeTruthy();
    // Pressing inside the popover must not dismiss it.
    fireEvent.mouseDown(screen.getByRole("dialog"));
    expect(screen.getByRole("dialog")).toBeTruthy();
    // An unrelated key must not either.
    fireEvent.keyDown(window, { key: "a" });
    expect(screen.getByRole("dialog")).toBeTruthy();

    fireEvent.mouseDown(document.body);
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());

    await userEvent.click(screen.getByRole("button", { name: "작업공간 및 프로필" }));
    fireEvent.keyDown(window, { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });
});
