import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { latticeApi } from "@/api/client";
import { t } from "@/i18n";
import { useAppStore } from "@/store/appStore";
import { renderPage } from "@/test/renderPage";
import { VaultExportCard } from "./VaultExportCard";

const result = (overrides: Record<string, unknown> = {}) => ({
  ok: true, notes: 12, history: 2, withheld: 0, omitted: 0, truncated: 0, ...overrides,
});

const button = () => screen.getByRole("button", { name: t("en", "system.vault.export") });

async function exportWith(response: unknown) {
  renderPage(<VaultExportCard />, { language: "en", api: { exportBrainVault: response } });
  await userEvent.click(button());
}

afterEach(() => {
  vi.restoreAllMocks();
  useAppStore.setState({ workspaceId: null });
});

describe("VaultExportCard", () => {
  it("exports the personal workspace by default and reports what was downloaded", async () => {
    await exportWith(result());

    expect(await screen.findByRole("status")).toHaveTextContent(t("en", "system.vault.done", { notes: 12, history: 2 }));
    expect(screen.getByRole("status")).not.toHaveTextContent("left out");
    expect(latticeApi.exportBrainVault).toHaveBeenCalledWith("personal");
  });

  it("exports the selected workspace and names what was left out", async () => {
    useAppStore.setState({ workspaceId: "org-1" });
    await exportWith(result({ withheld: 3, omitted: 1 }));

    const status = await screen.findByRole("status");
    expect(status).toHaveTextContent(t("en", "system.vault.withheld", { count: 3 }));
    expect(status).toHaveTextContent(t("en", "system.vault.limited", { omitted: 1, truncated: 0 }));
    expect(latticeApi.exportBrainVault).toHaveBeenCalledWith("org-1");
  });

  it("says so when the vault is empty and mentions shortened notes", async () => {
    await exportWith(result({ notes: 0, history: 0, truncated: 2 }));

    const status = await screen.findByRole("status");
    expect(status).toHaveTextContent(t("en", "system.vault.empty"));
    expect(status).toHaveTextContent(t("en", "system.vault.limited", { omitted: 0, truncated: 2 }));
  });

  it("shows the failure reason", async () => {
    await exportWith(result({ ok: false, error: "Admin access required." }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Admin access required.");
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("locks the button while the vault is being built", async () => {
    await exportWith(() => new Promise(() => {}));

    const busy = await screen.findByRole("button", { name: t("en", "system.vault.exporting") });
    expect(busy).toBeDisabled();
  });
});
