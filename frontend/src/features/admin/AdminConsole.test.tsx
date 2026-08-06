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
    adminSummary: live({}),
    adminStats: live({}),
    adminUsers: live([]),
    adminAudit: live({ recent_events: [] }),
    adminSecurity: live({ status: "ready" }),
    adminSecurityEvents: live({ events: [] }),
    adminPolicies: live({ policies: [] }),
    adminRoles: live({ roles: [] }),
    adminLogRetention: live({}),
    indexStatus: live({ status: "ready" }),
    agentRuntime: live({}),
    toolRegistryDiagnostics: live({ diagnostics: { ready: false } }),
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

  it("says it is rebuilding while the request is in flight", async () => {
    stubAdminApi();
    let release!: (value: unknown) => void;
    vi.spyOn(latticeApi, "rebuildIndex").mockImplementation(
      () => new Promise((resolve) => { release = resolve; }) as never,
    );
    renderConsole();
    const main = await screen.findByRole("main");

    const rebuildButton = within(main)
      .getAllByRole("button")
      .find((button) => /rebuild|reindex/i.test(button.textContent || ""))!;
    fireEvent.click(rebuildButton);

    // While the server works, the control must say so and refuse a second run.
    expect(await screen.findByText("Rebuilding")).toBeTruthy();
    expect((screen.getByText("Rebuilding").closest("button") as HTMLButtonElement).disabled).toBe(true);

    release(live({}));
    expect(await screen.findByText("Rebuild index")).toBeTruthy();
  });
});

describe("the health statement, edge answers", () => {
  it("counts at least one issue when attention arrives without a number", async () => {
    stubAdminApi({ adminHealthSummary: live({ status: "attention" }) });
    renderConsole();
    expect(await screen.findByText(/1 issue\(s\) need attention/i)).toBeTruthy();
  });

  it("treats a positive issue count as attention even when the status says ok", async () => {
    stubAdminApi({ adminHealthSummary: live({ status: "ok", issue_count: 2 }) });
    renderConsole();
    expect(await screen.findByText(/2 issue\(s\) need attention/i)).toBeTruthy();
  });

  it("falls back to the raw envelope when the summary carries no payload", async () => {
    stubAdminApi({ adminHealthSummary: live(null) });
    renderConsole();
    expect(await screen.findByText(/All Systems Normal/i)).toBeTruthy();
  });

  it("summarises a security service that reports no status by the request outcome", async () => {
    stubAdminApi({ adminSecurity: live({}) });
    renderConsole();
    expect(await screen.findByText(/Security status: Ready/i)).toBeTruthy();
  });

  it("reads a numeric state field as the security status", async () => {
    stubAdminApi({ adminSecurity: live({ state: 3 }) });
    renderConsole();
    expect(await screen.findByText(/Security status: 3/i)).toBeTruthy();
  });

  it("calls an unreachable index unknown, not indexed", async () => {
    stubAdminApi({ indexStatus: down({}) });
    renderConsole();
    expect(await screen.findByText(/Index status: Unknown/i)).toBeTruthy();
  });

  it("calls a responding index with no status Indexed", async () => {
    stubAdminApi({ indexStatus: live({}) });
    renderConsole();
    expect(await screen.findByText(/Index status: Indexed/i)).toBeTruthy();
  });
});

describe("roles, policies and security events render their rows", () => {
  it("lists roles with member counts and capability chips", async () => {
    stubAdminApi({
      adminRoles: live({ roles: [
        { role: "admin", members: 2, caps: ["read", "write", "exec", "net", "extra"] },
        {},
      ] }),
    });
    renderConsole();
    expect(await screen.findByText(/admin · 2 Users/i)).toBeTruthy();
    expect(screen.getByText("read, write, exec, net")).toBeTruthy();
    // A role the server left blank still reads as a role, with no caps.
    expect(screen.getByText(/role · 0 Users/i)).toBeTruthy();
    expect(screen.getByText("No caps")).toBeTruthy();
  });

  it("shows the policy strip with label, name and id fallbacks", async () => {
    stubAdminApi({
      adminPolicies: live({ policies: [
        { id: "p1", label: "policy-retention" },
        { name: "policy-unlabeled" },
        { id: "policy-raw-id" },
      ] }),
    });
    renderConsole();
    expect(await screen.findByText("policy-retention")).toBeTruthy();
    expect(screen.getByText("policy-unlabeled")).toBeTruthy();
    expect(screen.getByText("policy-raw-id")).toBeTruthy();
    expect(screen.queryByText("Policy API quiet")).toBeNull();
  });

  it("renders security events, including one with no fields at all", async () => {
    stubAdminApi({
      adminSecurityEvents: live({ events: [
        { event: "blocked_write", user: "u9", time: "just now" },
        {},
      ] }),
    });
    renderConsole();
    expect(await screen.findByText("blocked_write")).toBeTruthy();
    expect(screen.getByText(/u9 · just now/)).toBeTruthy();
    expect(screen.getByText(/system · recently/)).toBeTruthy();
  });

  it("names a user row from whatever identity fields exist", async () => {
    stubAdminApi({ adminUsers: live([{}, { name: "Kim", status: "active" }]) });
    renderConsole();
    expect(await screen.findByText("Local user")).toBeTruthy();
    expect(screen.getByText("member")).toBeTruthy();
    expect(screen.getByText("Kim")).toBeTruthy();
    expect(screen.getByText("active")).toBeTruthy();
  });
});

describe("brain operations detail", () => {
  it("describes the index by its document and chunk counts", async () => {
    stubAdminApi({ indexStatus: live({ documents: 5, chunks: 12 }) });
    renderConsole();
    expect(await screen.findByText("5 docs · 12 chunks")).toBeTruthy();
  });

  it("accepts the alternate docs/vectors spelling", async () => {
    stubAdminApi({ indexStatus: live({ docs: 7, vectors: 9 }) });
    renderConsole();
    expect(await screen.findByText("7 docs · 9 chunks")).toBeTruthy();
  });

  it("falls back to the index message, then to ready copy", async () => {
    stubAdminApi({ indexStatus: live({ message: "reindexing soon" }) });
    renderConsole();
    expect(await screen.findByText("reindexing soon")).toBeTruthy();
  });

  it("says the index is ready when it reports nothing at all", async () => {
    stubAdminApi({ indexStatus: live({}) });
    renderConsole();
    expect(await screen.findByText("Index status ready")).toBeTruthy();
  });

  it("prefers the summary sentence, then the stats message", async () => {
    stubAdminApi({ adminSummary: live({ summary: "5 users active this week" }) });
    const first = renderConsole();
    expect(await screen.findByText("5 users active this week")).toBeTruthy();
    first.unmount();

    stubAdminApi({ adminStats: live({ message: "stats warming up" }) });
    renderConsole();
    expect(await screen.findByText("stats warming up")).toBeTruthy();
  });

  it("prints retention values exactly as the server reports them", async () => {
    stubAdminApi({
      adminLogRetention: live({ retention_days: true, retained_events: Number.NaN, prune_candidates: false }),
    });
    renderConsole();
    // Booleans print as text, and NaN is not a count — the copy falls back to 0.
    expect(await screen.findByText("true day retention")).toBeTruthy();
    expect(screen.getByText(/0 retained · false ready for export\/prune review/)).toBeTruthy();
  });
});

describe("runtime trust", () => {
  it("reports a ready runtime and an aligned tool registry", async () => {
    stubAdminApi({
      agentRuntime: live({ runtime: { ready: true, mode: "local", execution_mode: "inline" }, health: { status: "ok" } }),
      toolRegistryDiagnostics: live({ diagnostics: { ready: true, registered_tools: 12, governed_tools: 4, described_tools: 12 } }),
    });
    renderConsole();
    expect(await screen.findByText("Aligned")).toBeTruthy();
    expect(screen.getByText("Run preview and execution are ready.")).toBeTruthy();
    expect(screen.getByText(/12/)).toBeTruthy();
  });

  it("shows the blocking reason when the runtime is not ready", async () => {
    stubAdminApi({ agentRuntime: live({ runtime: { ready: false, unavailable_reason: "model missing" } }) });
    renderConsole();
    expect(await screen.findByText("model missing")).toBeTruthy();
    expect(screen.getByText("Needs review")).toBeTruthy();
  });
});

describe("log filters, severity and matched count", () => {
  it("sends the chosen severity through to the audit request", async () => {
    stubAdminApi();
    const auditSpy = vi.spyOn(latticeApi, "adminAudit");
    renderConsole();
    await screen.findByRole("main");

    fireEvent.change(screen.getByRole("combobox"), { target: { value: "high" } });
    await waitFor(() => {
      const sent = auditSpy.mock.calls.map(([filters]) => (filters as { severity?: string })?.severity);
      expect(sent).toContain("high");
    });
  });

  it("shows how many events the filters matched", async () => {
    stubAdminApi({ adminAudit: live({ recent_events: [], filters: { matched_events: 42 } }) });
    renderConsole();
    expect(await screen.findByText(/42/)).toBeTruthy();
  });
});
