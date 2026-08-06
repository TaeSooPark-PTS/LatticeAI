import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { latticeApi, type PermissionModeOption } from "@/api/client";
import { fail, ok, renderPage } from "@/test/renderPage";
import { BrainQuickControls } from "./BrainQuickControls";

const option = (
  id: string,
  risk: string,
  overrides: Partial<PermissionModeOption> = {},
): PermissionModeOption => ({
  id,
  label: `${id} label`,
  label_ko: `${id} 라벨`,
  summary: `${id} summary`,
  summary_ko: `${id} 요약`,
  risk,
  requires_ack: false,
  ...overrides,
});

const catalog = [
  option("strict", "low"),
  option("trusted", "medium"),
  option("bypass", "high", { requires_ack: true, warning: "risky", warning_ko: "위험합니다" }),
  option("serverside", "mystery"), // unknown id → server copy, unknown risk → fallback dot
];

const modeState = (mode = "strict") => ok({ mode, catalog });

function renderControls(api: Record<string, unknown> = {}) {
  return renderPage(<BrainQuickControls language="ko" />, {
    api: { permissionMode: modeState(), ...api },
  });
}

describe("BrainQuickControls", () => {
  it("renders the dial from the server catalog with localized labels and risk dots", async () => {
    renderControls();
    const dial = await screen.findByRole("radiogroup", { name: "혼자 해도 되는 일" });
    expect(dial).toBeTruthy();

    const strict = screen.getByTestId("quick-mode-strict");
    expect(strict.textContent).toContain("먼저 물어보기"); // localized by id, not server copy
    expect(strict.getAttribute("aria-checked")).toBe("true");
    expect(strict.className).toContain("is-active");
    expect(strict.querySelector(".brain-quick-dot")?.className).toContain("is-low");

    const trusted = screen.getByTestId("quick-mode-trusted");
    expect(trusted.getAttribute("aria-checked")).toBe("false");
    expect(trusted.querySelector(".brain-quick-dot")?.className).toContain("is-medium");
    expect(screen.getByTestId("quick-mode-bypass").querySelector(".brain-quick-dot")?.className).toContain("is-high");

    // A mode the client copy tables don't know falls back to the server label
    // and the medium risk dot.
    const serverside = screen.getByTestId("quick-mode-serverside");
    expect(serverside.textContent).toContain("serverside 라벨");
    expect(serverside.querySelector(".brain-quick-dot")?.className).toContain("is-medium");
  });

  const quietStates: Array<[string, unknown]> = [
    ["empty catalog", ok({ mode: "strict", catalog: [] })],
    ["failed envelope", fail("down", {})],
    ["malformed null envelope", () => Promise.resolve(null)],
  ];
  it.each(quietStates)("renders no dial on a %s", async (_name, permissionMode) => {
    renderControls({ permissionMode });
    await waitFor(() => expect(vi.mocked(latticeApi.permissionMode)).toHaveBeenCalled());
    expect(screen.queryByRole("radiogroup")).toBeNull();
    expect(screen.getByTestId("brain-quick-controls").children).toHaveLength(0);
  });

  it("ignores a click on the mode that is already active", async () => {
    renderControls();
    await userEvent.click(await screen.findByTestId("quick-mode-strict"));
    expect(vi.mocked(latticeApi.setPermissionMode)).not.toHaveBeenCalled();
  });

  it("applies a no-ack mode directly and refreshes the dial", async () => {
    renderControls({ setPermissionMode: ok({ mode: "trusted" }) });
    await userEvent.click(await screen.findByTestId("quick-mode-trusted"));

    await waitFor(() =>
      expect(vi.mocked(latticeApi.setPermissionMode)).toHaveBeenCalledWith("trusted", false),
    );
    // Success invalidates the state query → a second fetch.
    await waitFor(() =>
      expect(vi.mocked(latticeApi.permissionMode).mock.calls.length).toBeGreaterThan(1),
    );
    expect(screen.queryByRole("status")).toBeNull(); // no error line on success
  });

  it("interposes the confirmation for a requires_ack mode and applies only after consent", async () => {
    renderControls({ setPermissionMode: ok({ mode: "bypass" }) });
    await userEvent.click(await screen.findByTestId("quick-mode-bypass"));

    const confirm = screen.getByTestId("quick-mode-confirm");
    expect(confirm.getAttribute("role")).toBe("alertdialog");
    expect(confirm.textContent).toContain("이 설정에서는 평소의 확인 창이 뜨지 않습니다");
    expect(vi.mocked(latticeApi.setPermissionMode)).not.toHaveBeenCalled();

    await userEvent.click(screen.getByTestId("quick-mode-confirm-apply"));
    await waitFor(() =>
      expect(vi.mocked(latticeApi.setPermissionMode)).toHaveBeenCalledWith("bypass", true),
    );
    await waitFor(() => expect(screen.queryByTestId("quick-mode-confirm")).toBeNull());
  });

  it("drops the confirmation on cancel without calling the server", async () => {
    renderControls();
    await userEvent.click(await screen.findByTestId("quick-mode-bypass"));
    expect(screen.getByTestId("quick-mode-confirm")).toBeTruthy();

    await userEvent.click(screen.getByRole("button", { name: "취소" }));
    expect(screen.queryByTestId("quick-mode-confirm")).toBeNull();
    expect(vi.mocked(latticeApi.setPermissionMode)).not.toHaveBeenCalled();
  });

  it("keeps the confirmation open and surfaces the server's reason when the apply fails", async () => {
    renderControls({ setPermissionMode: fail("금지된 모드입니다", {}) });
    await userEvent.click(await screen.findByTestId("quick-mode-bypass"));
    await userEvent.click(screen.getByTestId("quick-mode-confirm-apply"));

    const error = await screen.findByRole("status");
    expect(error.textContent).toBe("금지된 모드입니다");
    // onSuccess bails on !ok → the risky confirmation stays for another try.
    expect(screen.getByTestId("quick-mode-confirm")).toBeTruthy();
  });

  it("falls back to the generic failure line when the error has no message", async () => {
    renderControls({ setPermissionMode: fail("", {}) });
    await userEvent.click(await screen.findByTestId("quick-mode-trusted"));
    const error = await screen.findByRole("status");
    expect(error.textContent).toBe("요청을 처리하지 못했어요");
  });

  it("disables every segment and the confirm button while an apply is in flight", async () => {
    let release!: (value: unknown) => void;
    renderControls({
      setPermissionMode: () => new Promise((resolve) => { release = resolve; }),
    });
    await userEvent.click(await screen.findByTestId("quick-mode-bypass"));
    await userEvent.click(screen.getByTestId("quick-mode-confirm-apply"));

    await waitFor(() => expect(screen.getByTestId("quick-mode-confirm-apply")).toBeDisabled());
    expect(screen.getByTestId("quick-mode-strict")).toBeDisabled();
    expect(screen.getByTestId("quick-mode-trusted")).toBeDisabled();

    release(ok({ mode: "bypass" }));
    await waitFor(() => expect(screen.getByTestId("quick-mode-strict")).toBeEnabled());
  });
});
