import { fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { t } from "@/i18n";
import { fail, ok, renderPage } from "@/test/renderPage";
import { CloudBoundaryHint, openNetworkBoundaryPanel } from "./CloudBoundaryHint";
import { useConversationSession } from "./conversationSession";

function renderHint(allowsCloud: boolean | "fail" = true, language: "ko" | "en" = "ko") {
  return renderPage(<CloudBoundaryHint language={language} />, {
    language,
    api: {
      networkBoundary: allowsCloud === "fail"
        ? fail("down", { allows_cloud: false })
        : ok({ allows_cloud: allowsCloud }),
    },
  });
}

describe("CloudBoundaryHint", () => {
  beforeEach(() => {
    useConversationSession.getState().resetConversation();
    window.location.hash = "";
  });

  afterEach(() => {
    window.location.hash = "";
  });

  it("stays quiet while the dial is local-only, missing, or unavailable", async () => {
    const { unmount } = renderHint(false);
    await waitFor(() => expect(screen.queryByTestId("cloud-boundary-hint")).toBeNull());
    unmount();

    renderHint("fail");
    await waitFor(() => expect(screen.queryByTestId("cloud-boundary-hint")).toBeNull());
  });

  it("shows the allowed chip and the local-only override only when the dial allows cloud", async () => {
    renderHint(true);
    await waitFor(() => expect(screen.getByTestId("cloud-boundary-hint")).toBeTruthy());
    expect(screen.getByTestId("cloud-boundary-allowed").textContent)
      .toBe(t("ko", "brain.cloud.allowed"));
    expect(screen.getByLabelText(t("ko", "brain.cloud.allowed.aria"))).toBeTruthy();

    const toggle = screen.getByTestId("cloud-local-only-toggle") as HTMLInputElement;
    expect(toggle.checked).toBe(false);
    fireEvent.click(toggle);
    expect(useConversationSession.getState().preferLocalOnly).toBe(true);
    expect(screen.getByLabelText(t("ko", "brain.cloud.localOnly.aria"))).toBeTruthy();
  });

  it("opens the settings boundary panel and scrolls it into view", async () => {
    const scrollIntoView = vi.fn();
    const panel = document.createElement("div");
    panel.id = "network-boundary-panel";
    panel.scrollIntoView = scrollIntoView;
    document.body.appendChild(panel);

    renderHint(true);
    await waitFor(() => expect(screen.getByTestId("cloud-boundary-allowed")).toBeTruthy());
    fireEvent.click(screen.getByTestId("cloud-boundary-allowed"));

    expect(window.location.hash).toBe("#/settings");
    expect(scrollIntoView).toHaveBeenCalled();
    panel.remove();
  });

  it("gives up scrolling if the settings panel never mounts", () => {
    vi.useFakeTimers();
    openNetworkBoundaryPanel();
    vi.advanceTimersByTime(50 * 10);
    expect(document.getElementById("network-boundary-panel")).toBeNull();
    vi.useRealTimers();
  });

  it("retries the scroll until the settings panel mounts", async () => {
    vi.useFakeTimers();
    const scrollIntoView = vi.fn();
    openNetworkBoundaryPanel();
    expect(window.location.hash).toBe("#/settings");
    expect(scrollIntoView).not.toHaveBeenCalled();

    const panel = document.createElement("div");
    panel.id = "network-boundary-panel";
    panel.scrollIntoView = scrollIntoView;
    document.body.appendChild(panel);
    vi.advanceTimersByTime(50);
    expect(scrollIntoView).toHaveBeenCalled();
    panel.remove();
    vi.useRealTimers();
  });

  it("uses English copy when the language is en", async () => {
    renderHint(true, "en");
    await waitFor(() => expect(screen.getByTestId("cloud-boundary-allowed").textContent)
      .toBe(t("en", "brain.cloud.allowed")));
    expect(screen.getByLabelText(t("en", "brain.cloud.localOnly.aria"))).toBeTruthy();
  });
});
