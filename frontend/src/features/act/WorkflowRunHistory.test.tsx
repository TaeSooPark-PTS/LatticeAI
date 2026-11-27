import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { latticeApi } from "@/api/client";
import { fail, ok, renderPage, type RenderPageOptions } from "@/test/renderPage";
import { downloadJson } from "./actHelpers";
import { WorkflowRunHistory } from "./WorkflowRunHistory";

const WORKFLOWS = [{ id: "wf-1", name: "주간 점검" }, { workflow_id: "wf-2" }];
const RUNS = ok({
  runs: [
    { id: "run-2", status: "awaiting_approval", created_at: "2026-11-02T09:00:00" },
    { id: "run-1", status: "ok", created_at: "2026-11-01T09:00:00" },
    { id: "run-0", created_at: "2026-10-31T09:00:00" },
  ],
});
const FRAMES = [
  { index: 0, event: "workflow_started", actor: "workflow", when: "2026-11-01T09:00:00", why: "", output: null, decision: "queued", raw: {} },
  { index: 1, event: "step_completed", actor: "draft", when: null, why: "note", output: { text: "요약본" }, decision: "ok", raw: { detail: "초안 작성" } },
  { index: 2, event: "custom_event", actor: "send", output: "보냄", decision: null },
];

function render(api: RenderPageOptions["api"] = {}, workflows: Array<Record<string, unknown>> = WORKFLOWS) {
  return renderPage(<WorkflowRunHistory language="ko" workflows={workflows} />, {
    api: {
      workflowDefinitionRuns: RUNS,
      workflowRunReplay: ok({ replay: { frames: FRAMES } }),
      ...api,
    },
  });
}

describe("WorkflowRunHistory", () => {
  it("says there is nothing to browse without a saved workflow", () => {
    render({}, []);
    expect(screen.getByText("저장된 워크플로가 아직 없어요.")).toBeTruthy();
    expect(latticeApi.workflowDefinitionRuns).not.toHaveBeenCalled();
  });

  it("replays a run step by step", async () => {
    render();
    await userEvent.click(await screen.findByRole("button", { name: /2026-11-01 09:00/ }));
    expect(latticeApi.workflowRunReplay).toHaveBeenCalledWith("run-1");
    expect(await screen.findByText("3단계 중 1단계")).toBeTruthy();
    expect(screen.getByText("이 단계에는 남은 출력이 없어요.")).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: "다음 단계" }));
    expect(screen.getByText(/초안 작성/).textContent).toContain("요약본");
    await userEvent.click(screen.getByRole("button", { name: /custom_event/ }));
    expect(screen.getByText("보냄")).toBeTruthy();
    expect(screen.getByRole("button", { name: "다음 단계" }).hasAttribute("disabled")).toBe(true);
    await userEvent.click(screen.getByRole("button", { name: "이전 단계" }));
    expect(screen.getByText("3단계 중 2단계")).toBeTruthy();
  });

  it("switches workflow and drops the open replay", async () => {
    render();
    await userEvent.click(await screen.findByRole("button", { name: /2026-11-01 09:00/ }));
    await userEvent.selectOptions(screen.getByLabelText("워크플로"), "wf-2");
    await waitFor(() => expect(latticeApi.workflowDefinitionRuns).toHaveBeenLastCalledWith("wf-2"));
    expect(screen.queryByText("3단계 중 1단계")).toBeNull();
  });

  it("reports an empty history", async () => {
    render({ workflowDefinitionRuns: ok({}) });
    expect(await screen.findByText("이 워크플로는 아직 실행한 적이 없어요.")).toBeTruthy();
  });

  it("reports why the history could not load", async () => {
    render({ workflowDefinitionRuns: fail("", { runs: [] }) });
    expect(await screen.findByText("실행 기록을 불러오지 못했어요.")).toBeTruthy();
  });

  it("shows the server reason for the list and for the replay", async () => {
    const first = render({ workflowDefinitionRuns: fail("목록 오류", { runs: [] }) });
    expect(await screen.findByText("목록 오류")).toBeTruthy();
    first.unmount();
    const second = render({ workflowRunReplay: fail("재생 오류", { replay: {} }) });
    await userEvent.click(await screen.findByRole("button", { name: /2026-11-02/ }));
    expect(await screen.findByText("재생 오류")).toBeTruthy();
    second.unmount();
    render({ workflowRunReplay: fail("", { replay: {} }) });
    await userEvent.click(await screen.findByRole("button", { name: /2026-11-02/ }));
    expect(await screen.findByText("실행 기록을 불러오지 못했어요.")).toBeTruthy();
  });

  it("says so when a run recorded no steps", async () => {
    render({ workflowRunReplay: ok({ replay: {} }) });
    await userEvent.click(await screen.findByRole("button", { name: /2026-10-31/ }));
    expect(await screen.findByText("이 실행에는 기록된 단계가 없어요.")).toBeTruthy();
  });
});

describe("downloadJson", () => {
  it("saves the payload under a filesystem-safe name", () => {
    const create = vi.fn(() => "blob:x");
    const revoke = vi.fn();
    Object.assign(URL, { createObjectURL: create, revokeObjectURL: revoke });
    const names: string[] = [];
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (this: HTMLAnchorElement) {
      names.push(this.download);
    });
    downloadJson({ a: 1 }, "a/b: c");
    downloadJson({}, "  ");
    expect(names).toEqual(["a-b- c.json", "workflow.json"]);
    expect(revoke).toHaveBeenCalledWith("blob:x");
    expect(document.querySelector("a")).toBeNull();
    click.mockRestore();
  });
});
