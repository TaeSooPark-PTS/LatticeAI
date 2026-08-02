/**
 * Three small components that carry disproportionate weight and had no test.
 *
 * `FeedbackState` is the shared "nothing here" / "that failed" surface — the
 * difference between an empty list and a broken one, which is the honesty
 * contract the whole product rests on.
 * `AdminAccessGate` is an access affordance: showing the console to someone in
 * basic mode, or hiding the promotion path, are both user-visible bugs.
 * `CoreServiceUnavailableBanner` is what tells a user the local service is down
 * rather than their Brain being empty.
 */

import * as React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AdminAccessGate } from "./AdminAccessGate";
import { CoreServiceUnavailableBanner } from "./CoreServiceUnavailableBanner";
import { FeedbackState } from "./FeedbackState";
import { LanguageSwitcher } from "./LanguageSwitcher";
import { useAppStore } from "@/store/appStore";

beforeEach(() => {
  useAppStore.setState({ language: "en", mode: "basic" });
  window.location.hash = "";
});

describe("FeedbackState", () => {
  it("announces an error assertively and an empty state politely", () => {
    const { rerender } = render(
      <FeedbackState tone="error" language="en" title="Could not load" />,
    );
    expect(screen.getByRole("alert")).toBeTruthy();

    rerender(<FeedbackState tone="empty" language="en" title="Nothing yet" />);
    expect(screen.getByRole("status")).toBeTruthy();
  });

  it("shows the title, and the body only when there is one", () => {
    const { rerender, container } = render(
      <FeedbackState tone="empty" language="en" title="Nothing yet" />,
    );
    expect(screen.getByText("Nothing yet")).toBeTruthy();
    expect(container.querySelectorAll(".feedback-state-body span")).toHaveLength(0);

    rerender(
      <FeedbackState tone="empty" language="en" title="Nothing yet" body="Add a file" />,
    );
    expect(screen.getByText("Add a file")).toBeTruthy();
  });

  it("offers a default retry action for errors", () => {
    const onAction = vi.fn();
    render(
      <FeedbackState tone="error" language="en" title="Could not load" onAction={onAction} />,
    );
    const button = screen.getByRole("button");
    fireEvent.click(button);
    expect(onAction).toHaveBeenCalledOnce();
    expect(button.textContent?.trim().length).toBeGreaterThan(0);
  });

  it("does not invent a retry button for an empty state", () => {
    render(
      <FeedbackState tone="empty" language="en" title="Nothing yet" onAction={vi.fn()} />,
    );
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("renders no action when there is a label but nothing to call", () => {
    render(
      <FeedbackState tone="error" language="en" title="Broken" actionLabel="Try again" />,
    );
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("marks the compact variant so dense panels can shrink it", () => {
    const { container } = render(
      <FeedbackState tone="empty" language="en" title="Nothing" compact />,
    );
    expect(container.querySelector(".feedback-state")?.className).toContain("is-compact");
  });
});

describe("AdminAccessGate", () => {
  it("stays out of the way in everyday (basic) mode", () => {
    useAppStore.setState({ mode: "basic" });
    const { container } = render(<AdminAccessGate language="en" />);
    expect(container.firstChild).toBeNull();
  });

  it("offers the console once the user is in advanced mode", () => {
    useAppStore.setState({ mode: "advanced" });
    render(<AdminAccessGate language="en" />);
    const open = screen.getAllByRole("button").find(
      (button) => button.className.includes("admin-access-button"),
    )!;
    expect(open.className).not.toContain("is-locked");
    fireEvent.click(open);
    expect(window.location.hash).toBe("#/admin");
  });

  it("exposes the three workspace modes with the active one pressed", () => {
    useAppStore.setState({ mode: "admin" });
    render(<AdminAccessGate language="en" />);
    const group = screen.getByRole("group");
    const buttons = Array.from(group.querySelectorAll("button"));
    expect(buttons).toHaveLength(3);
    const pressed = buttons.filter((b) => b.getAttribute("aria-pressed") === "true");
    expect(pressed).toHaveLength(1);
  });

  it("switches mode through the mini switcher", () => {
    useAppStore.setState({ mode: "advanced" });
    render(<AdminAccessGate language="en" />);
    const group = screen.getByRole("group");
    const adminButton = Array.from(group.querySelectorAll("button")).at(-1)!;
    fireEvent.click(adminButton);
    expect(useAppStore.getState().mode).toBe("admin");
  });
});

describe("LanguageSwitcher", () => {
  it("marks the active language and switches on click", () => {
    render(<LanguageSwitcher />);
    const buttons = screen.getAllByRole("button");
    expect(buttons).toHaveLength(2);
    expect(buttons.filter((b) => b.getAttribute("aria-pressed") === "true")).toHaveLength(1);

    const korean = buttons.find((b) => b.getAttribute("aria-pressed") === "false")!;
    fireEvent.click(korean);
    expect(useAppStore.getState().language).toBe("ko");
  });

  it("supports a compact variant for dense bars", () => {
    const { container } = render(<LanguageSwitcher compact />);
    expect(container.querySelector(".language-switcher")?.className).toContain("compact");
  });
});

describe("CoreServiceUnavailableBanner", () => {
  function renderWithCache(seed: (client: QueryClient) => void) {
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    seed(client);
    const view = render(
      <QueryClientProvider client={client}>
        <CoreServiceUnavailableBanner />
      </QueryClientProvider>,
    );
    return { client, ...view };
  }

  it("renders nothing when every core query is healthy", () => {
    const { container } = renderWithCache((client) => {
      client.setQueryData(["health"], { ok: true, data: {} });
    });
    expect(container.firstChild).toBeNull();
  });

  it("appears when a core query reports ok:false", () => {
    renderWithCache((client) => {
      client.setQueryData(["health"], { ok: false, error: "sidecar is not answering" });
    });
    expect(screen.getByTestId("service-unavailable-banner")).toBeTruthy();
    expect(screen.getByText(/sidecar is not answering/)).toBeTruthy();
  });

  it("ignores a failure from a query that is not core", () => {
    const { container } = renderWithCache((client) => {
      client.setQueryData(["someOptionalWidget"], { ok: false, error: "nope" });
    });
    expect(container.firstChild).toBeNull();
  });

  it("offers a retry that refetches the core queries", () => {
    const { client } = renderWithCache((c) => {
      c.setQueryData(["graph"], { ok: false, error: "graph unavailable" });
    });
    const refetch = vi.spyOn(client, "refetchQueries").mockResolvedValue(undefined);
    fireEvent.click(screen.getByRole("button"));
    expect(refetch).toHaveBeenCalledOnce();
  });

  it("falls back to generic copy when the failure carries no message", () => {
    renderWithCache((client) => {
      client.setQueryData(["chatHistory"], { ok: false });
    });
    const banner = screen.getByTestId("service-unavailable-banner");
    expect(banner.textContent?.trim().length).toBeGreaterThan(0);
  });
});
