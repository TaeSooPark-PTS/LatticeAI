import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { latticeApi } from "@/api/client";
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

  it("opens directly on the runs history sub-tab when linked there", async () => {
    render({}, {}, { initialTab: "runs" });
    await waitFor(() => expect(screen.getAllByRole("tablist").length).toBe(2));
    const [top, sub] = screen.getAllByRole("tablist");
    expect(within(top).getByRole("tab", { name: "실행" }).getAttribute("aria-selected")).toBe("true");
    expect(within(sub).getByRole("tab", { name: "최근 실행 내역" }).getAttribute("aria-selected")).toBe("true");
  });

  it("ignores an initial tab it does not recognise and stays on the default", async () => {
    render({}, {}, { initialTab: "not-a-real-tab" });
    await waitFor(() => expect(screen.getAllByRole("tab").length).toBeGreaterThan(0));
    const [top] = screen.getAllByRole("tablist");
    expect(within(top).getByRole("tab", { name: "실행" }).getAttribute("aria-selected")).toBe("true");
  });

  it("hides the advanced-only tabs in basic mode", async () => {
    render({}, { mode: "basic" });
    await waitFor(() => expect(screen.getAllByRole("tab").length).toBeGreaterThan(0));
    const [top] = screen.getAllByRole("tablist");
    expect(within(top).getAllByRole("tab").map((tab) => tab.textContent)).toEqual(["실행", "목표", "레시피"]);
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

  // The approval inbox is the one screen where a person has to decide whether
  // to let the agent touch their files. It showed `act.approval.action.파일_읽기`
  // instead of an action, because the i18n key was built from the localised
  // label rather than the `action` enum and `t()` returns the key on a miss.
  // Both shapes are asserted: an unmapped English action, and a mapped action
  // arriving with a Korean label — the second is the one that regressed.
  describe("approval inbox action labels", () => {
    const pendingWith = (entry: Record<string, unknown>) =>
      ok({ pending: { "tok-1": entry }, count: 1 });

    it("names an unmapped action without leaking the i18n key", async () => {
      render({ permissionsPending: pendingWith({ action: "delete", action_label: "delete", path: "notes/old.md" }) }, {}, { initialTab: "runs" });
      await waitFor(() => expect(screen.getByText("파일 삭제")).toBeTruthy());
      expect(document.body.textContent).not.toContain("act.approval.action.");
    });

    it("keys off `action`, not the already-localised `action_label`", async () => {
      render({ permissionsPending: pendingWith({ action: "read", action_label: "파일 읽기", path: "notes/a.md" }) }, {}, { initialTab: "runs" });
      await waitFor(() => expect(screen.getByText("파일 읽기")).toBeTruthy());
      // Keying off action_label produced the token `파일_읽기`, which matches
      // nothing — this is the assertion that fails if that path comes back.
      expect(document.body.textContent).not.toContain("act.approval.action.");
    });

    it("falls back to the server's label when the action has no copy at all", async () => {
      render({ permissionsPending: pendingWith({ action: "quantum_defrag", action_label: "조각 모음", path: "x" }) }, {}, { initialTab: "runs" });
      await waitFor(() => expect(screen.getByText("조각 모음")).toBeTruthy());
      expect(document.body.textContent).not.toContain("act.approval.action.");
    });

    it("names the request with a raw action, no path, and no requester", async () => {
      // No `action`/`action_label` at all: the label falls all the way back to
      // the default copy, and the absence of a path/reason/requester must not
      // render as literal "undefined" anywhere in the row.
      render({ permissionsPending: pendingWith({}) }, {}, { initialTab: "runs" });
      await waitFor(() => expect(screen.getByText("권한 요청")).toBeTruthy());
      expect(document.body.textContent).not.toMatch(/undefined/);
    });

    it("reads the action from `tool` and shows the reason and requester when present", async () => {
      render({
        permissionsPending: pendingWith({
          tool: "custom_tool",
          path: "notes/plan.md",
          reason: "정리를 위해",
          user_email: "a@example.com",
        }),
      }, {}, { initialTab: "runs" });
      await waitFor(() => expect(screen.getByText("custom_tool")).toBeTruthy());
      expect(screen.getByText("정리를 위해")).toBeTruthy();
      expect(screen.getByText(/요청자.*a@example\.com/)).toBeTruthy();
      // The path is reduced to its filename inside a `<code>` element.
      expect(screen.getByText("plan.md")).toBeTruthy();
    });

    it("reads the action from `type` when neither `action` nor `tool` is set", async () => {
      render({ permissionsPending: pendingWith({ type: "list" }) }, {}, { initialTab: "runs" });
      await waitFor(() => expect(screen.getByText("폴더 목록 보기")).toBeTruthy());
    });

    it("shows a numbered request in basic mode instead of the raw token", async () => {
      render(
        { permissionsPending: pendingWith({ action: "read", path: "a.md" }) },
        { mode: "basic" },
        { initialTab: "runs" },
      );
      await waitFor(() => expect(screen.getByText("승인 요청 1")).toBeTruthy());
    });

    it("treats a null pending value as an empty record instead of throwing", async () => {
      render({ permissionsPending: ok({ pending: { "tok-null": null }, count: 1 }) }, {}, { initialTab: "runs" });
      await waitFor(() => expect(screen.getByText("권한 요청")).toBeTruthy());
    });

    it("falls back to the full path when it has no filename segment", async () => {
      render({ permissionsPending: pendingWith({ action: "read", path: "notes/" }) }, {}, { initialTab: "runs" });
      await waitFor(() => expect(screen.getByText("notes/")).toBeTruthy());
    });
  });

  describe("approving and denying a pending permission", () => {
    it("approves and denies from the inbox", async () => {
      render({
        permissionsPending: ok({ pending: { "tok-1": { action: "read", path: "a.md" } } }, ),
        approvePermission: () => Promise.resolve(ok({})),
        denyPermission: () => Promise.resolve(ok({})),
      }, {}, { initialTab: "runs" });
      await userEvent.click(await screen.findByRole("button", { name: "승인" }));
      await waitFor(() => expect(latticeApi.approvePermission).toHaveBeenCalledWith("tok-1"));
      await userEvent.click(screen.getByRole("button", { name: "거부" }));
      await waitFor(() => expect(latticeApi.denyPermission).toHaveBeenCalledWith("tok-1"));
    });
  });

  describe("AgentsPanel", () => {
    it("stays consent-first in basic mode and hides the advanced panels", async () => {
      render({ agentRuntime: ok({ runtime: { ready: false } }) }, { mode: "basic" }, { initialTab: "agents" });
      await waitFor(() => expect(screen.getByText("중요한 변경은 실행하기 전에 항상 확인받습니다.")).toBeTruthy());
      expect(screen.getByText("작업을 시작하려면 먼저 AI 모델을 준비해 주세요.")).toBeTruthy();
      // The readiness / agent-team / capabilities panels are advanced-only.
      expect(screen.queryByText("준비 상태")).toBeNull();
      expect(screen.queryByText("Agent 팀")).toBeNull();
    });

    it("explains why the model is unavailable in advanced mode and blocks the run", async () => {
      render(
        { agentRuntime: ok({ runtime: { ready: false, unavailable_reason: "모델이 없습니다" } }) },
        { mode: "advanced" },
        { initialTab: "agents" },
      );
      await waitFor(() => expect(screen.getByText("모델이 없습니다")).toBeTruthy());
      const startButton = screen.getByRole("button", { name: "AI 모델 준비하기" });
      expect((startButton as HTMLButtonElement).disabled).toBe(true);
    });

    it("runs a goal once the model is ready and shows the brain-saved hint", async () => {
      render(
        {
          agentRuntime: ok({ runtime: { ready: true } }),
          agentRegistry: ok({ agents: [{ id: "agent:researcher", type: "agent:researcher", name: "Researcher" }] }),
          runAgent: () => Promise.resolve(ok({ run_id: "run-1", saved: true })),
        },
        { mode: "advanced" },
        { initialTab: "agents" },
      );
      const goalInput = await screen.findByPlaceholderText("예: 이번 주 회의 자료를 읽고 결정 사항과 담당자별 할 일을 정리해줘");
      await userEvent.type(goalInput, "이번 주 회의록 정리");
      const startButton = screen.getByRole("button", { name: "작업 시작" });
      expect((startButton as HTMLButtonElement).disabled).toBe(false);
      await userEvent.click(startButton);
      await waitFor(() => expect(screen.getByText("Brain에 합성 메모리가 추가되었습니다.")).toBeTruthy());
    });

    it("completes a run without adding the brain-saved hint when nothing was saved", async () => {
      render(
        {
          agentRuntime: ok({ runtime: { ready: true } }),
          runAgent: () => Promise.resolve({ ok: true, status: 200, source: "live", data: null }),
        },
        { mode: "advanced" },
        { initialTab: "agents" },
      );
      const goalInput = await screen.findByPlaceholderText("예: 이번 주 회의 자료를 읽고 결정 사항과 담당자별 할 일을 정리해줘");
      await userEvent.type(goalInput, "정리해줘");
      await userEvent.click(screen.getByRole("button", { name: "작업 시작" }));
      await waitFor(() => expect(screen.getByText("Agent run 완료 — 결과가 Brain 기억과 그래프에 합성됨")).toBeTruthy());
      expect(screen.queryByText("Brain에 합성 메모리가 추가되었습니다.")).toBeNull();
    });

    it("registers a new custom agent from the team panel", async () => {
      render(
        {
          agentRuntime: ok({ runtime: { ready: true } }),
          agentRegistry: ok({ agents: [{ id: "agent:researcher", type: "agent:researcher", name: "Researcher" }] }),
          registerAgent: () => Promise.resolve(ok({ id: "agent:custom" })),
        },
        { mode: "advanced" },
        { initialTab: "agents" },
      );
      const nameInput = await screen.findByPlaceholderText("새 사용자 Agent 이름");
      await userEvent.type(nameInput, "나만의 Agent");
      await userEvent.click(screen.getByRole("button", { name: "등록" }));
      await waitFor(() => expect(latticeApi.registerAgent).toHaveBeenCalledWith({
        name: "나만의 Agent",
        type: "custom",
        capabilities: [],
      }));
    });
  });

  describe("runs tab: combined history and RunList", () => {
    it("falls back to agent and workflow runs when the activity feed is empty", async () => {
      render({
        activityRuns: ok({ runs: [] }),
        agentRuntime: ok({
          runtime: { ready: true },
          runs: [{ run_id: "a-1", status: "waiting_approval", goal: "회의록 요약" }],
        }),
        workflowRuns: ok({
          runs: [{ id: "w-1", workflow_id: "wf-1", status: "awaiting_approval", name: "주간 점검" }],
        }),
        stopWorkflowRun: () => Promise.resolve(ok({})),
        resumeWorkflowRun: () => Promise.resolve(ok({})),
      }, {}, { initialTab: "runs" });
      await waitFor(() => expect(screen.getByText("회의록 요약")).toBeTruthy());
      expect(screen.getByText("주간 점검")).toBeTruthy();
      // Only the workflow run offers resume actions; the agent run does not.
      const resumeApproved = screen.getAllByRole("button", { name: "승인 후 재개" });
      expect(resumeApproved).toHaveLength(1);

      await userEvent.click(resumeApproved[0]);
      await waitFor(() => expect(latticeApi.resumeWorkflowRun).toHaveBeenCalledWith("w-1", true));
      await userEvent.click(screen.getByRole("button", { name: "거부 후 재개" }));
      await waitFor(() => expect(latticeApi.resumeWorkflowRun).toHaveBeenLastCalledWith("w-1", false));

      // The workflow run's stop button calls stopWorkflowRun, not stopAgentRun.
      const stopButtons = screen.getAllByRole("button", { name: "중지" });
      await userEvent.click(stopButtons[1]);
      await waitFor(() => expect(latticeApi.stopWorkflowRun).toHaveBeenCalledWith("w-1"));
    });

    it("falls back to runtime data for the combined panel when the activity feed fails", async () => {
      render({
        activityRuns: fail("unavailable", { runs: [] }),
        agentRuntime: ok({ runtime: { ready: true }, runs: [] }),
      }, {}, { initialTab: "runs" });
      const heading = await screen.findByText("최근 실행 기록");
      const panel = heading.closest(".data-panel") as HTMLElement;
      // Once the failed activity feed and the (ok) runtime query both settle,
      // the combined-runs panel's source badge reflects runtime.data, not the
      // failed activity envelope — proof the ternary actually swapped sources.
      await waitFor(() => expect(within(panel).getByText("연결됨")).toBeTruthy());
    });

    it("switches from the runs sub-tab back to the review sub-tab", async () => {
      render({}, {}, { initialTab: "runs" });
      await waitFor(() => expect(screen.getAllByRole("tablist").length).toBe(2));
      const [, sub] = screen.getAllByRole("tablist");
      expect(within(sub).getByRole("tab", { name: "최근 실행 내역" }).getAttribute("aria-selected")).toBe("true");
      await userEvent.click(within(sub).getByRole("tab", { name: "검토함" }));
      await waitFor(() => expect(within(sub).getByRole("tab", { name: "검토함" }).getAttribute("aria-selected")).toBe("true"));
    });

    it("titles a run from a plain string input and stops it", async () => {
      render({
        activityRuns: ok({ runs: [{ run_id: "r-9", status: "ok", input: "이 문서를 요약해줘" }] }),
        stopAgentRun: () => Promise.resolve(ok({})),
      }, {}, { initialTab: "runs" });
      await waitFor(() => expect(screen.getByText("이 문서를 요약해줘")).toBeTruthy());
      await userEvent.click(screen.getByRole("button", { name: "중지" }));
      await waitFor(() => expect(latticeApi.stopAgentRun).toHaveBeenCalledWith("r-9"));
    });

    it("titles a run from a nested input object", async () => {
      render({
        activityRuns: ok({ runs: [{ run_id: "r-10", status: "succeeded", input: { goal: "파일 정리" } }] }),
      }, {}, { initialTab: "runs" });
      await waitFor(() => expect(screen.getByText("파일 정리")).toBeTruthy());
    });

    it("hides the id line, numbers unnamed runs in basic mode, and copes with no status at all", async () => {
      render({
        activityRuns: ok({ runs: [{ run_id: "r-11" }] }),
      }, { mode: "basic" }, { initialTab: "runs" });
      await waitFor(() => expect(screen.getByText("1번째 작업")).toBeTruthy());
      expect(document.body.textContent).not.toMatch(/r-11/);
      expect(screen.getByText("알 수 없음")).toBeTruthy();
    });

    it("reports an unrecognised status as unknown rather than printing the raw key", async () => {
      render({
        activityRuns: ok({ runs: [{ run_id: "r-12", status: "totally_made_up" }] }),
      }, {}, { initialTab: "runs" });
      await waitFor(() => expect(screen.getByText("알 수 없음")).toBeTruthy());
    });
  });

  describe("workflows tab: recipe install flow", () => {
    it("creates a fresh automation recipe, showing the pending and created states", async () => {
      let resolveInstall!: (value: unknown) => void;
      render({
        automationRecipes: ok({ recipes: [{ id: "brand-new-recipe", cadence: "daily", creates: [] }] }),
        workflowDefinitions: ok({ workflows: [] }),
        installAutomationRecipe: () => new Promise((resolve) => { resolveInstall = resolve; }),
      }, {}, { initialTab: "workflows" });

      const createButton = await screen.findByRole("button", { name: "검토 가능한 자동화 초안 만들기" });
      await userEvent.click(createButton);

      await screen.findByRole("button", { name: "초안 만드는 중..." });
      expect(latticeApi.installAutomationRecipe).toHaveBeenCalledWith("brand-new-recipe", false);

      resolveInstall(ok({
        workflow: { id: "wf-new", metadata: { recipe_id: "brand-new-recipe" } },
        recipe: { recipe_id: "brand-new-recipe" },
        enabled: false,
        already_installed: false,
      }));

      await screen.findByRole("button", { name: "✓ 자동화 초안 생성됨" });
      expect(screen.getByText("초안이 준비됐습니다. 내용을 확인한 뒤 활성화하세요.")).toBeTruthy();
    });

    it("enables an already-installed draft, exercising the envelope's nested `.data.recipe` shape", async () => {
      // This is the path the previous coverage pass suspected of an
      // envelope mismatch: the mutation resolves to the request envelope
      // (`{ok,status,data,source}`), and the server body with `recipe`/
      // `enabled` lives one level down, on `.data`. Reading `.recipe`
      // directly off the envelope (skipping `.data`) would leave `lastRid`
      // permanently empty and this test would fail to ever see the
      // "activated" label — so this test is the regression guard for that
      // bug, not just a coverage filler.
      let resolveInstall!: (value: unknown) => void;
      render({
        automationRecipes: ok({ recipes: [{ id: "daily-memory-digest", cadence: "daily", creates: ["memory digest"] }] }),
        workflowDefinitions: ok({
          workflows: [{
            id: "wf-draft",
            name: "Draft WF",
            metadata: { created_from: "brain_automation_recipe", recipe_id: "daily-memory-digest", automation_state: "draft_disabled" },
          }],
        }),
        installAutomationRecipe: () => new Promise((resolve) => { resolveInstall = resolve; }),
      }, {}, { initialTab: "workflows" });

      const enableButton = await screen.findByRole("button", { name: "이 기억 자동화 활성화" });
      await userEvent.click(enableButton);

      await screen.findByRole("button", { name: "자동화 연결 중..." });
      expect(latticeApi.installAutomationRecipe).toHaveBeenCalledWith("daily-memory-digest", true);

      resolveInstall(ok({
        workflow: { id: "wf-draft", metadata: { recipe_id: "daily-memory-digest", automation_state: "enabled" } },
        recipe: { recipe_id: "daily-memory-digest" },
        enabled: true,
        already_installed: true,
      }));

      await screen.findByRole("button", { name: "✓ 기억 자동화 작동 중" });
      expect(screen.getByText("새 기억이 들어오면 결과를 검토함에 초안으로 만듭니다.")).toBeTruthy();
    });

    it("renders cadence, consent and creates badges, plus the workflow graph nodes/edges", async () => {
      render({
        automationRecipes: ok({
          recipes: [
            { id: "weekly-project-review", cadence: "weekly", creates: ["decision summary", "risk list"], consent: { requires_user_enable: true } },
            { id: "custom-thing", cadence: "custom_cadence_xyz", creates: ["mystery item"], consent: {} },
            { id: "no-cadence-recipe" },
            {}, // no id at all: exercises `String(recipe.id || "")`'s empty fallback.
          ],
        }),
        workflowDefinitions: ok({
          workflows: [
            { id: "wf-enabled", name: "Enabled WF", metadata: { created_from: "brain_automation_recipe", recipe_id: "weekly-project-review", automation_state: "enabled" } },
            { id: "wf-plain", name: "Plain Flow" },
            { workflow_id: "wf-legacy", name: "Legacy Flow" },
            { id: "wf-noname" }, // no name: node label and list title fall back to the id.
            {}, // neither id, workflow_id nor name: falls back to index for both.
          ],
        }),
        runWorkflow: () => Promise.resolve(ok({})),
        exportWorkflow: () => Promise.resolve(ok({})),
        createWorkflow: () => Promise.resolve(ok({ id: "wf-created" })),
        importWorkflow: () => Promise.resolve(ok({ id: "wf-imported" })),
      }, {}, { initialTab: "workflows" });

      // Matched cadence, consent badge, matched + unmatched creates, active recipe.
      await screen.findByRole("button", { name: "✓ 기억 자동화 작동 중" });
      expect(screen.getByText("초안 확인 후 활성화")).toBeTruthy();
      expect(screen.getByText("결정 요약")).toBeTruthy();
      expect(screen.getByText("위험 목록")).toBeTruthy();
      expect(screen.getByText("새 기억이 들어오면 결과를 검토함에 초안으로 만듭니다.")).toBeTruthy();

      // Unmatched cadence falls back to the raw string; unmatched creates item
      // falls back to its raw label; not-yet-installed recipe offers "create".
      expect(screen.getByText("custom_cadence_xyz")).toBeTruthy();
      expect(screen.getByText("mystery item")).toBeTruthy();

      // No cadence at all falls back to the "draft" status word.
      expect(screen.getAllByText("초안").length).toBeGreaterThan(0);
      expect(screen.getAllByRole("button", { name: "검토 가능한 자동화 초안 만들기" }).length).toBe(2);

      // Definitions list: three workflows -> at least one graph edge, plus
      // id/workflow_id fallbacks, plus the run/export actions. Each name
      // appears twice: once as a graph node label, once in the definitions list.
      expect(screen.getAllByText("Plain Flow").length).toBeGreaterThan(0);
      expect(screen.getAllByText("Legacy Flow").length).toBeGreaterThan(0);
      await userEvent.click(screen.getAllByRole("button", { name: "실행" })[0]);
      await waitFor(() => expect(latticeApi.runWorkflow).toHaveBeenCalled());
      await userEvent.click(screen.getAllByRole("button", { name: "내보내기" })[0]);
      await waitFor(() => expect(latticeApi.exportWorkflow).toHaveBeenCalled());

      // Manual create + import.
      await userEvent.type(screen.getByPlaceholderText("워크플로 이름"), "새 워크플로");
      await userEvent.click(screen.getByRole("button", { name: "만들기" }));
      await waitFor(() => expect(screen.getByText("워크플로를 만들었습니다")).toBeTruthy());
      expect(latticeApi.createWorkflow).toHaveBeenLastCalledWith(expect.objectContaining({ name: expect.stringContaining("새 워크플로") }));

      // Clearing the name falls back to the default workflow name rather than
      // creating a blank one.
      await userEvent.clear(screen.getByPlaceholderText("워크플로 이름"));
      await userEvent.click(screen.getByRole("button", { name: "만들기" }));
      await waitFor(() => expect(latticeApi.createWorkflow).toHaveBeenLastCalledWith(expect.objectContaining({ name: "수동 워크플로" })));

      // user-event's keyboard parser treats `{` as the start of a special-key
      // token (e.g. `{enter}`); only the opening brace needs escaping (`{{`) to
      // type it literally, a bare `}` is not special.
      await userEvent.type(screen.getByPlaceholderText("워크플로 내보내기 내용을 붙여넣으세요"), '{{"name":"Imported"}');
      await userEvent.click(screen.getByRole("button", { name: "가져오기" }));
      await waitFor(() => expect(screen.getByText("워크플로를 가져왔습니다")).toBeTruthy());
    });
  });

  describe("workflows tab in basic mode: the trigger summary", () => {
    it("says nothing runs on its own when no trigger is armed", async () => {
      render({ workflowTriggers: ok({ armed: [], running: true }) }, { mode: "basic" }, { initialTab: "workflows" });
      await waitFor(() => expect(screen.getByText("지금은 자동으로 실행되는 작업이 없어요.")).toBeTruthy());
    });

    it("names a running, recognised trigger by its schedule", async () => {
      render({
        workflowTriggers: ok({
          armed: [{ workflow_id: "wf-1", kind: "schedule", name: "매일 정리" }],
          running: true,
        }),
      }, { mode: "basic" }, { initialTab: "workflows" });
      await waitFor(() => expect(screen.getByText("매일 정리")).toBeTruthy());
      expect(screen.getByText("정해진 시간에")).toBeTruthy();
      expect(screen.getByText("켜져 있어요")).toBeTruthy();
    });

    it("falls back to a numbered name and an unknown condition for a paused, unnamed trigger", async () => {
      render({
        workflowTriggers: ok({
          // The second entry has neither a kind nor any id at all: it exercises
          // the empty-kind fallback and the row key's final `|| index` fallback.
          armed: [{ id: "t-2", kind: "mystery_kind" }, {}],
          running: false,
        }),
      }, { mode: "basic" }, { initialTab: "workflows" });
      await waitFor(() => expect(screen.getByText("워크플로 1")).toBeTruthy());
      expect(screen.getAllByText("조건이 맞으면").length).toBe(2);
      expect(screen.getAllByText("지금은 멈춰 있어요").length).toBe(2);
      expect(screen.getByText("워크플로 2")).toBeTruthy();
    });
  });

  describe("HooksPanel", () => {
    it("offers only the safeguard list and a mode gate in basic mode", async () => {
      render({ hooks: ok({ hooks: [{ id: "h1", name: "위험한 명령 차단", kind: "guard" }] }) }, { mode: "basic" }, { initialTab: "hooks" });
      await waitFor(() => expect(screen.getByText("위험한 명령 차단")).toBeTruthy());
      expect(screen.getByText("상세 훅 로그")).toBeTruthy();
      expect(screen.getByRole("button", { name: "고급 모드로 전환" })).toBeTruthy();
    });

    it("runs every manual hook from the advanced panel", async () => {
      render({
        hooks: ok({ hooks: [{ id: "h1", name: "위험한 명령 차단", kind: "guard" }] }),
        hookRuns: ok({ runs: [{ hook_id: "h1", status: "ok" }] }),
        hookRun: () => Promise.resolve(ok({ ran: 1 })),
      }, { mode: "advanced" }, { initialTab: "hooks" });
      await userEvent.click(await screen.findByRole("button", { name: "모든 수동 훅 실행" }));
      await waitFor(() => expect(latticeApi.hookRun).toHaveBeenCalledWith({ event: "manual" }));
    });
  });

  describe("ToolsPanel", () => {
    it("lists tool permissions when the server reports them", async () => {
      render({ toolPermissions: ok({ permissions: [{ tool: "execute_command", risk: "high" }] }) }, {}, { initialTab: "tools" });
      await waitFor(() => expect(screen.getByText("execute_command")).toBeTruthy());
    });
  });
});
