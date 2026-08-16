/**
 * Small components that carry disproportionate weight and had no test.
 *
 * `AdminAccessGate` is an access affordance: showing the console to someone in
 * basic mode, or hiding the promotion path, are both user-visible bugs.
 * `CoreServiceUnavailableBanner` is what tells a user the local service is down
 * rather than their Brain being empty.
 */

import * as React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AdminAccessGate } from "./AdminAccessGate";
import { CoreServiceUnavailableBanner } from "./CoreServiceUnavailableBanner";
import { LanguageSwitcher } from "./LanguageSwitcher";
import { useAppStore, type WorkspaceMode } from "@/store/appStore";

beforeEach(() => {
  useAppStore.setState({ language: "en", mode: "basic" });
  window.location.hash = "";
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

  it("locks the console behind a promotion for an out-of-contract mode", () => {
    // The store validates what it reads from persistence, but the gate still
    // guards against a value outside the three known modes (corrupted storage,
    // a future mode this build predates): the console is locked behind an
    // explicit promotion to admin rather than shown or silently dropped.
    useAppStore.setState({ mode: "expert" as unknown as WorkspaceMode });
    render(<AdminAccessGate language="en" />);
    const locked = screen.getAllByRole("button").find((b) => b.className.includes("is-locked"));
    expect(locked).toBeTruthy();
    fireEvent.click(locked!);
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

  it("notices a core failure that arrives after mount", async () => {
    const { client, container } = renderWithCache(() => {});
    expect(container.firstChild).toBeNull();

    act(() => {
      client.setQueryData(["health"], { ok: false, error: "went away" });
    });

    expect(await screen.findByTestId("service-unavailable-banner")).toBeTruthy();
    expect(screen.getByText(/went away/)).toBeTruthy();
  });

  it("retries through the real cache and leaves non-core queries alone", async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const coreFn = vi.fn().mockResolvedValue({ ok: false, error: "still down" });
    const optionalFn = vi.fn().mockResolvedValue({ ok: true });
    await client.prefetchQuery({ queryKey: ["graph"], queryFn: coreFn });
    // A query whose key starts with a falsy segment must not crash the predicate.
    await client.prefetchQuery({ queryKey: [""], queryFn: optionalFn });

    render(
      <QueryClientProvider client={client}>
        <CoreServiceUnavailableBanner />
      </QueryClientProvider>,
    );
    fireEvent.click(screen.getByRole("button"));

    await waitFor(() => expect(coreFn).toHaveBeenCalledTimes(2));
    expect(optionalFn).toHaveBeenCalledTimes(1);
  });
});
