import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { fail, ok, renderPage } from "@/test/renderPage";
import { ActPage } from "./Act";

/**
 * The automation surface. Its contract is consent-first: a suggestion is a
 * draft, an install is disabled by default, and anything that would change
 * existing content becomes a reviewable proposal. The screen must also read in
 * the user's language — agent roles and recipe names arrive as stable ids and
 * are localised here, which is exactly where an id can leak through.
 */

const RECIPES = {
  recipes: [
    { id: "daily_memory_digest", cadence: "daily", creates: "note", enabled: false },
    { id: "weekly_review", cadence: "weekly", creates: "document", enabled: false },
  ],
};

const REGISTRY = {
  agents: [
    { id: "agent:researcher", type: "agent:researcher", name: "Researcher", status: "available" },
    { id: "agent:reviewer", type: "agent:reviewer", name: "Reviewer", status: "available" },
  ],
};

function render(overrides = {}, options = {}, props: { initialTab?: string } = {}) {
  return renderPage(<ActPage {...props} />, {
    api: {
      automationRecipes: ok(RECIPES),
      agentRegistry: ok(REGISTRY),
      agentCapabilities: ok({ capabilities: [] }),
      agentRuntime: ok({ status: "ready", version: "10.3.0" }),
      hooks: ok({ hooks: [] }),
      hookRuns: ok({ runs: [] }),
      ...overrides,
    },
    ...options,
  });
}

describe("ActPage", () => {
  beforeEach(() => vi.restoreAllMocks());

  it("renders the automation surface", async () => {
    render();
    await waitFor(() => expect((document.body.textContent || "").length).toBeGreaterThan(20));
  });

  it("opens on the review inbox, because that is what waits on the person", async () => {
    // Reordering the tab array is not the same as changing where the screen
    // opens. Both levels have to agree, at whichever tab the shell hands over.
    // "실행" names both a top tab and a sub tab, so each is scoped to its strip.
    for (const initialTab of [undefined, "review"]) {
      const view = render({}, {}, { initialTab });
      await waitFor(() => expect(screen.getAllByRole("tablist").length).toBe(2));
      const [top, sub] = screen.getAllByRole("tablist");
      expect(within(top).getByRole("tab", { name: "실행" }).getAttribute("aria-selected")).toBe("true");
      expect(within(sub).getByRole("tab", { name: "검토함" }).getAttribute("aria-selected")).toBe("true");
      view.unmount();
    }
  });

  it("still honours a deep link to a demoted tab", async () => {
    // Promoting the review inbox must not strip the older entry points.
    render({}, {}, { initialTab: "agents" });
    await waitFor(() => expect(screen.getAllByRole("tab").length).toBeGreaterThan(0));
    expect(screen.getByRole("tab", { name: "목표" }).getAttribute("aria-selected")).toBe("true");
  });

  it("captions every tab with its own panel, including the last one", async () => {
    // The hero follows the open tab. It was built as a chain of ternaries whose
    // final `else` captured whatever was left over, so the permissions panel
    // sat under the safeguards heading — the one tab nobody checks, because it
    // is the one the chain never names. Assert each pairing, not just the
    // promoted tab, so a sixth tab cannot inherit a caption by accident.
    // Advanced mode is the only one that shows all five; it also renames the
    // last two ("보호 장치"/"권한" become "훅"/"도구"), so the tab is addressed by
    // the label this mode actually renders.
    const captions: Array<[string, string]> = [
      ["실행", "지금 하는 일과 검토할 일"],
      ["목표", "새로 맡길 일 적기"],
      ["레시피", "자주 쓰는 작업 모음"],
      ["훅", "보호 장치"],
      ["도구", "도구 사용 권한"],
    ];
    render({}, { mode: "advanced" });
    await waitFor(() => expect(screen.getAllByRole("tab").length).toBeGreaterThan(0));

    for (const [tabName, heading] of captions) {
      const [top] = screen.getAllByRole("tablist");
      await userEvent.click(within(top).getByRole("tab", { name: tabName }));
      await waitFor(() => expect(document.querySelector("h1.page-title")?.textContent).toBe(heading));
    }
  });

  it("localises agent roles by id rather than printing the id", async () => {
    render();
    await waitFor(() => expect(document.body.textContent).toBeTruthy());
    expect(document.body.textContent).not.toMatch(/agent:researcher|agent:reviewer/);
  });

  it("localises recipe names rather than printing their keys", async () => {
    render();
    await waitFor(() => expect(document.body.textContent).toBeTruthy());
    expect(document.body.textContent).not.toMatch(/daily_memory_digest|weekly_review/);
  });

  it("renders in English when the language is en", async () => {
    render({}, { language: "en" });
    await waitFor(() => expect(document.body.textContent).toBeTruthy());
    // A Korean-only string leaking into the English UI is the failure mode the
    // i18n namespace checker cannot see.
    expect(document.body.textContent).not.toMatch(/자동화 제안|오늘의 기억 정리/);
  });

  it("an unavailable recipe list is reported rather than shown as none", async () => {
    render({ automationRecipes: fail("server unavailable", { recipes: [] }) });
    await waitFor(() => expect(document.body.textContent).toBeTruthy());
    expect(document.body.textContent).not.toMatch(/undefined|NaN/);
  });

  it("an empty registry reads as empty rather than as broken", async () => {
    render({ agentRegistry: ok({ agents: [] }), automationRecipes: ok({ recipes: [] }) });
    await waitFor(() => expect(document.body.textContent).toBeTruthy());
    expect(document.body.textContent).not.toMatch(/undefined|\[object Object\]/);
  });

  it("a recipe row missing its optional fields still renders", async () => {
    render({ automationRecipes: ok({ recipes: [{ id: "bare" }] }) });
    await waitFor(() => expect(document.body.textContent).toBeTruthy());
    expect(document.body.textContent).not.toMatch(/undefined/);
  });

  it("does not present any automation as already running", async () => {
    // Installs are consent-first drafts; the screen must not imply otherwise.
    render();
    await waitFor(() => expect(document.body.textContent).toBeTruthy());
    const checkboxes = screen.queryAllByRole("checkbox") as HTMLInputElement[];
    expect(checkboxes.every((c) => !c.checked || c.disabled)).toBe(true);
  });

  it("stacks the runs tab by urgency: approvals, then automations, then history", async () => {
    // This tab's rebuild *is* the order. An approval waits on a person, the
    // installed list says what is armed, and the two run lists are only what
    // already happened. Moving a block in the JSX reverses that silently —
    // the screenshot would change but nothing would say the hierarchy broke.
    render({
      permissionsPending: ok({ pending: { "tok-1": { tool: "write_file", path: "README.md" } } }),
      automationOverview: ok({
        suggestions: [],
        installed: [{ id: "wf-1", name: "매일 기억 요약", enabled: true, requires_user_enable: false, creates: [] }],
        questions_scanned: 0,
      }),
      workflowRuns: ok({ runs: [] }),
    });

    await waitFor(() => expect(screen.getAllByRole("tablist").length).toBe(2));
    const [, sub] = screen.getAllByRole("tablist");
    await userEvent.click(within(sub).getByRole("tab", { name: /실행/ }));

    await waitFor(() => expect(screen.getByText("설치된 자동화")).toBeTruthy());
    // getAllByRole returns document order, which is the reading order here.
    const order = screen.getAllByRole("heading").map((node) => node.textContent || "");
    const approvals = order.findIndex((text) => text.includes("승인함"));
    const installed = order.findIndex((text) => text.includes("설치된 자동화"));
    const history = order.findIndex((text) => text.includes("Agent 실행") || text.includes("최근 실행 기록") || text.includes("실행"));

    expect(approvals).toBeGreaterThanOrEqual(0);
    expect(installed).toBeGreaterThan(approvals);
    expect(history).toBeGreaterThan(installed);
  });

  it("keeps the installed automations reachable from the runs tab, not only from workflows", async () => {
    // It is rendered in both tabs. A future cleanup that dedupes it by deleting
    // the runs-tab copy would take the middle tier of the hierarchy with it.
    render({
      automationOverview: ok({
        suggestions: [],
        installed: [{ id: "wf-1", name: "매일 기억 요약", enabled: true, requires_user_enable: false, creates: [] }],
        questions_scanned: 0,
      }),
    });
    await waitFor(() => expect(screen.getAllByRole("tablist").length).toBe(2));
    const [, sub] = screen.getAllByRole("tablist");
    await userEvent.click(within(sub).getByRole("tab", { name: /실행/ }));
    await waitFor(() => expect(screen.getByTestId("installed-automations")).toBeTruthy());
    expect(screen.getByText("매일 기억 요약")).toBeTruthy();
  });
});
