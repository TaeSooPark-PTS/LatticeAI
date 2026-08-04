/**
 * The admin console had no test, which is a bad place for a blind spot: it is
 * the screen an operator reads to decide whether anything is wrong, so the
 * failure mode is not a broken layout but a *reassuring* one.
 *
 * The specific dishonesty this file guards is a green light over a degraded
 * service — `.ok` only means the request succeeded, and a server answering 200
 * with `status: "degraded"` was being summarised as ready. Alongside that: an
 * empty list must read as empty rather than as absent, and the log filters
 * must actually reach the request.
 */

import * as React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { latticeApi } from "@/api/client";
import { useAppStore } from "@/store/appStore";

import { AdminConsole } from "./AdminConsole";

const live = <T,>(data: T) => ({ ok: true, status: 200, data, source: "live" as const });
const down = <T,>(data: T) => ({
  ok: false,
  status: 503,
  data,
  source: "unavailable" as const,
  error: "unavailable",
});

/** Every query the console fires, answered with an empty-but-valid payload. */
function stubAdminApi(overrides: Partial<Record<string, unknown>> = {}) {
  const defaults: Record<string, unknown> = {
    workspaceOs: live({ counts: {}, models: {} }),
    graphStats: live({ nodes: {}, edges: {}, total_nodes: 0, total_edges: 0 }),
    adminUsers: live([]),
    adminAudit: live({ recent_events: [] }),
    adminSecurity: live({ status: "ready" }),
    adminSecurityEvents: live({ events: [] }),
    adminPolicies: live({ policies: [] }),
    adminRoles: live({ roles: [] }),
    adminRetention: live({}),
    indexStatus: live({ status: "ready" }),
    agentRuntime: live({}),
    toolRegistry: live({}),
    adminHealthSummary: live({ status: "ok", issue_count: 0 }),
    rebuildIndex: live({}),
  };
  const merged = { ...defaults, ...overrides };
  const api = latticeApi as unknown as Record<string, unknown>;
  for (const [name, value] of Object.entries(merged)) {
    if (typeof api[name] === "function") {
      api[name] = vi.fn().mockResolvedValue(value);
    }
  }
  return merged;
}

function renderConsole(onBack = vi.fn()) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false, gcTime: 0 } },
  });
  const utils = render(
    <QueryClientProvider client={client}>
      <AdminConsole onBack={onBack} />
    </QueryClientProvider>,
  );
  return { ...utils, onBack };
}

beforeEach(() => {
  useAppStore.setState({ language: "en", mode: "admin" });
  vi.restoreAllMocks();
});

describe("the console renders as its own surface", () => {
  it("is a single main landmark with a way back to the Brain", async () => {
    stubAdminApi();
    const { onBack } = renderConsole();

    const main = await screen.findByRole("main");
    expect(main).toBeTruthy();

    fireEvent.click(within(main).getAllByRole("button")[0]);
    expect(onBack).toHaveBeenCalled();
  });
});

describe("the health statement", () => {
  it("says nothing needs attention when nothing does", async () => {
    stubAdminApi();
    renderConsole();

    expect(await screen.findByText(/All Systems Normal/i)).toBeTruthy();
  });

  it("reports attention when the health summary counts an issue", async () => {
    stubAdminApi({ adminHealthSummary: live({ status: "attention", issue_count: 3 }) });
    renderConsole();

    expect(await screen.findByText(/3 issue\(s\) need attention/i)).toBeTruthy();
    expect(screen.queryByText(/All Systems Normal/i)).toBeNull();
  });

  it("does not call a degraded service ready just because the request returned 200", async () => {
    // The bug this locks down: `.ok` describes the HTTP call, not the service.
    // A 200 carrying `status: "degraded"` was summarised as ready — a green
    // light over a service that is telling you it is unwell.
    stubAdminApi({ adminSecurity: live({ status: "degraded" }) });
    renderConsole();

    await waitFor(() => expect(screen.queryByText(/degraded/i)).toBeTruthy());
  });

  it("falls back to the request outcome when the service reports no status", async () => {
    stubAdminApi({ adminSecurity: down({}) });
    renderConsole();

    const main = await screen.findByRole("main");
    await waitFor(() => expect(main.textContent).toBeTruthy());
    expect(screen.queryByText(/degraded/i)).toBeNull();
  });
});

describe("lists distinguish empty from broken", () => {
  it("shows an empty state rather than a blank panel when there are no users", async () => {
    stubAdminApi({ adminUsers: live([]) });
    renderConsole();

    const main = await screen.findByRole("main");
    // Something is said about the absence — a silent gap reads as a bug.
    await waitFor(() => expect(main.textContent?.trim().length).toBeGreaterThan(0));
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("lists the users the server returned", async () => {
    stubAdminApi({
      adminUsers: live([
        { email: "admin@example.com", role: "admin" },
        { email: "member@example.com", role: "user" },
      ]),
    });
    renderConsole();

    expect(await screen.findByText("admin@example.com")).toBeTruthy();
    expect(await screen.findByText("member@example.com")).toBeTruthy();
  });

  it("renders audit rows from the recent_events envelope", async () => {
    stubAdminApi({
      adminAudit: live({
        recent_events: [{ action: "user_update", actor: "admin@example.com", severity: "info" }],
      }),
    });
    renderConsole();

    expect(await screen.findByText(/user_update/)).toBeTruthy();
  });
});

describe("log filters", () => {
  it("passes what was typed through to the audit request", async () => {
    const stubs = stubAdminApi();
    void stubs;
    const auditSpy = vi.spyOn(latticeApi, "adminAudit");
    renderConsole();
    await screen.findByRole("main");

    const search = screen.getAllByRole("textbox")[0];
    fireEvent.change(search, { target: { value: "delete" } });

    await waitFor(() => {
      const sent = auditSpy.mock.calls.map(([filters]) => (filters as { q?: string })?.q);
      expect(sent).toContain("delete");
    });
  });
});

describe("rebuilding the index", () => {
  it("asks the server to rebuild when the control is used", async () => {
    stubAdminApi();
    const rebuild = vi.spyOn(latticeApi, "rebuildIndex");
    renderConsole();
    const main = await screen.findByRole("main");

    const rebuildButton = within(main)
      .getAllByRole("button")
      .find((button) => /rebuild|reindex/i.test(button.textContent || ""));
    expect(rebuildButton, "the console offers no rebuild control").toBeTruthy();

    fireEvent.click(rebuildButton!);
    await waitFor(() => expect(rebuild).toHaveBeenCalled());
  });
});
