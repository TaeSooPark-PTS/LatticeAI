import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { latticeApi } from "@/api/client";
import { AgentApprovalCard, formatCountdown } from "./AgentApprovalCard";
import { parseApprovalPayload, resolveApprovalRequest } from "./approvalFlow";
import type { MessageApproval } from "./types";

function pendingApproval(overrides: Partial<MessageApproval> = {}): MessageApproval {
  return {
    runId: "run-1",
    token: "token-1",
    expiresAt: "2026-07-22T09:00:00+00:00",
    planSummary: "1. write_file → page.html",
    plan: { goal: "make a page", steps: [{ action: "write_file", args: { path: "page.html" } }] },
    status: "pending",
    ...overrides,
  };
}

function mockResume(result: { ok: boolean; status: number; data?: Record<string, unknown>; error?: string }) {
  return vi.spyOn(latticeApi, "resumeAgentApproval").mockResolvedValue({
    ok: result.ok,
    status: result.status,
    data: result.data || {},
    source: result.ok ? "live" : "unavailable",
    error: result.error,
  } as never);
}

describe("parseApprovalPayload", () => {
  it("builds the approval record from an awaiting_approval payload", () => {
    const approval = parseApprovalPayload({
      status: "awaiting_approval",
      run_id: "run-9",
      approval: { token: "tok", expires_at: "2026-07-22T09:00:00+00:00", plan_summary: "summary" },
      plan: { goal: "g", steps: [] },
    });
    expect(approval).toEqual({
      runId: "run-9",
      token: "tok",
      expiresAt: "2026-07-22T09:00:00+00:00",
      planSummary: "summary",
      plan: { goal: "g", steps: [] },
      status: "pending",
    });
  });

  it("rejects payloads without a resumable token", () => {
    expect(parseApprovalPayload({ status: "awaiting_approval", run_id: "run-9" })).toBeNull();
    expect(parseApprovalPayload({ status: "ok" })).toBeNull();
  });
});

describe("AgentApprovalCard", () => {
  it("renders the plan summary and focuses the primary action", () => {
    render(
      <AgentApprovalCard language="ko" approval={pendingApproval()} onResolved={() => {}} />,
    );
    expect(screen.getByTestId("approval-plan-summary").textContent).toContain("write_file");
    expect(document.activeElement).toBe(screen.getByTestId("approval-approve"));
  });

  it("approves the run and reports the finish payload for the chat merge", async () => {
    const finish = {
      status: "ok",
      response: "완료",
      final_state: "DONE",
      created_files: [{ path: "out/page.html", filename: "page.html", bytes: 128 }],
      artifacts: [{ path: "out/page.html", previewable: true }],
    };
    const resume = mockResume({ ok: true, status: 200, data: finish });
    const onResolved = vi.fn();
    render(
      <AgentApprovalCard language="ko" approval={pendingApproval()} onResolved={onResolved} />,
    );
    await userEvent.click(screen.getByTestId("approval-approve"));
    await waitFor(() => expect(onResolved).toHaveBeenCalledWith({ kind: "finished", payload: finish }));
    expect(resume).toHaveBeenCalledWith({
      run_id: "run-1",
      approval_token: "token-1",
      approve: true,
    });
  });

  it("cancels the run when the user denies", async () => {
    const resume = mockResume({ ok: true, status: 200, data: { status: "cancelled" } });
    const onResolved = vi.fn();
    render(
      <AgentApprovalCard language="ko" approval={pendingApproval()} onResolved={onResolved} />,
    );
    await userEvent.click(screen.getByTestId("approval-cancel"));
    await waitFor(() => expect(onResolved).toHaveBeenCalledWith({ kind: "cancelled" }));
    expect(resume).toHaveBeenCalledWith({
      run_id: "run-1",
      approval_token: "token-1",
      approve: false,
    });
  });

  it("maps an HTTP 410 to the expired outcome and renders the expired note", async () => {
    mockResume({ ok: false, status: 410, error: "Approval token expired." });
    const onResolved = vi.fn();
    const { rerender } = render(
      <AgentApprovalCard language="ko" approval={pendingApproval()} onResolved={onResolved} />,
    );
    await userEvent.click(screen.getByTestId("approval-approve"));
    await waitFor(() => expect(onResolved).toHaveBeenCalledWith({ kind: "expired" }));

    // The parent persists the status on the message; the card then renders
    // the honest "expired — ask again" note instead of live actions.
    rerender(
      <AgentApprovalCard
        language="ko"
        approval={pendingApproval({ status: "expired" })}
        onResolved={onResolved}
      />,
    );
    const note = screen.getByTestId("agent-approval-note");
    expect(note.textContent).toContain("만료");
    expect(note.getAttribute("role")).toBe("alert");
    expect(screen.queryByTestId("approval-approve")).toBeNull();
  });

  it("sends an edited plan through resume and rejects broken JSON inline", async () => {
    const resume = mockResume({ ok: true, status: 200, data: { status: "ok", response: "done" } });
    const onResolved = vi.fn();
    render(
      <AgentApprovalCard language="ko" approval={pendingApproval()} onResolved={onResolved} />,
    );
    await userEvent.click(screen.getByTestId("approval-edit"));
    const textarea = screen.getByTestId("approval-edit-textarea") as HTMLTextAreaElement;
    expect(textarea.value).toContain("write_file");

    // Broken JSON never leaves the card.
    fireEvent.change(textarea, { target: { value: "not json" } });
    await userEvent.click(screen.getByTestId("approval-edit-run"));
    expect(screen.getByRole("alert")).toBeTruthy();
    expect(resume).not.toHaveBeenCalled();

    // Valid JSON goes through as edited_plan.
    fireEvent.change(textarea, { target: { value: '{"goal": "edited", "steps": []}' } });
    await userEvent.click(screen.getByTestId("approval-edit-run"));
    await waitFor(() => expect(onResolved).toHaveBeenCalled());
    expect(resume).toHaveBeenCalledWith({
      run_id: "run-1",
      approval_token: "token-1",
      approve: true,
      edited_plan: { goal: "edited", steps: [] },
    });
  });
});

describe("resolveApprovalRequest replan passthrough", () => {
  it("carries the 410 detail's replan message into the expired resolution", async () => {
    mockResume({
      ok: false,
      status: 410,
      error: "Approval token expired.",
      data: {
        detail: {
          error: "approval_expired",
          message: "The approval window expired.",
          replan: { message: "원래 사용자 요청" },
        },
      },
    });
    const resolution = await resolveApprovalRequest({ runId: "run-1", token: "token-1" }, { approve: true });
    expect(resolution).toEqual({ kind: "expired", replanMessage: "원래 사용자 요청" });
  });

  it("stays a plain expired resolution when the 410 carries no replan hint", async () => {
    mockResume({ ok: false, status: 410, error: "Approval token expired.", data: { detail: "gone" } });
    const resolution = await resolveApprovalRequest({ runId: "run-1", token: "token-1" }, { approve: true });
    expect(resolution).toEqual({ kind: "expired" });
  });
});

describe("AgentApprovalCard TTL countdown", () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it("formats remaining time as m:ss and clamps at zero", () => {
    expect(formatCountdown(90_000)).toBe("1:30");
    expect(formatCountdown(605_000)).toBe("10:05");
    expect(formatCountdown(-2_000)).toBe("0:00");
  });

  it("shows a live countdown with urgency styling under two minutes", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-07-26T09:00:00+09:00"));
    render(
      <AgentApprovalCard
        language="ko"
        approval={pendingApproval({ expiresAt: new Date(Date.now() + 90_000).toISOString() })}
        onResolved={() => {}}
      />,
    );

    expect(screen.getByTestId("approval-countdown").textContent).toContain("1:30");
    expect(screen.getByTestId("agent-approval-card").className).toContain("is-urgent");
    expect(screen.getByTestId("approval-urgency")).toBeTruthy();

    act(() => {
      vi.advanceTimersByTime(30_000);
    });
    expect(screen.getByTestId("approval-countdown").textContent).toContain("1:00");
  });

  it("keeps the calm treatment while more than two minutes remain", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-07-26T09:00:00+09:00"));
    render(
      <AgentApprovalCard
        language="ko"
        approval={pendingApproval({ expiresAt: new Date(Date.now() + 10 * 60_000).toISOString() })}
        onResolved={() => {}}
      />,
    );

    expect(screen.getByTestId("approval-countdown").textContent).toContain("10:00");
    expect(screen.getByTestId("agent-approval-card").className).not.toContain("is-urgent");
    expect(screen.queryByTestId("approval-urgency")).toBeNull();
  });

  it("flips to expired client-side at zero without calling the server", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-07-26T09:00:00+09:00"));
    const resume = vi.spyOn(latticeApi, "resumeAgentApproval");
    const onResolved = vi.fn();
    render(
      <AgentApprovalCard
        language="ko"
        approval={pendingApproval({ expiresAt: new Date(Date.now() + 5_000).toISOString() })}
        onResolved={onResolved}
      />,
    );

    act(() => {
      vi.advanceTimersByTime(6_000);
    });
    expect(onResolved).toHaveBeenCalledTimes(1);
    expect(onResolved).toHaveBeenCalledWith({ kind: "expired" });
    expect(resume).not.toHaveBeenCalled();
  });

  it("offers 다시 계획하기 on an expired card that carries the replan message", () => {
    const onReplan = vi.fn();
    render(
      <AgentApprovalCard
        language="ko"
        approval={pendingApproval({ status: "expired", replanMessage: "정리 페이지 다시 만들어줘" })}
        onResolved={() => {}}
        onReplan={onReplan}
      />,
    );

    const note = screen.getByTestId("agent-approval-note");
    expect(note.textContent).toContain("만료");
    fireEvent.click(screen.getByTestId("approval-replan"));
    expect(onReplan).toHaveBeenCalledWith("정리 페이지 다시 만들어줘");
  });

  it("shows no replan action after a client-side expiry (no server hint)", () => {
    render(
      <AgentApprovalCard
        language="ko"
        approval={pendingApproval({ status: "expired" })}
        onResolved={() => {}}
        onReplan={() => {}}
      />,
    );
    expect(screen.queryByTestId("approval-replan")).toBeNull();
  });
});
