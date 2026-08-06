import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { latticeApi } from "@/api/client";
import { AgentApprovalCard, formatCountdown } from "./AgentApprovalCard";
import { parseApprovalPayload, parseReplanMessage, resolveApprovalRequest } from "./approvalFlow";
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
    // A token without a run id is just as unusable.
    expect(parseApprovalPayload({ status: "awaiting_approval", approval: { token: "tok" } })).toBeNull();
    expect(
      parseApprovalPayload({ status: "awaiting_approval", run_id: 7 as never, approval: { token: "tok" } }),
    ).toBeNull();
  });

  it("fills defensive defaults for a sparse but resumable payload", () => {
    expect(
      parseApprovalPayload({
        status: "awaiting_approval",
        run_id: "run-9",
        approval: { token: "tok" },
        plan: "broken" as never,
      }),
    ).toEqual({
      runId: "run-9",
      token: "tok",
      expiresAt: "",
      planSummary: "",
      plan: null,
      status: "pending",
    });
  });
});

describe("parseReplanMessage", () => {
  it("extracts and trims the replan message from either level", () => {
    expect(parseReplanMessage({ detail: { replan: { message: "  다시 요청  " } } })).toBe("다시 요청");
    expect(parseReplanMessage({ replan: { message: "루트 레벨" } })).toBe("루트 레벨");
  });

  it("returns an empty string for anything malformed", () => {
    expect(parseReplanMessage(null)).toBe("");
    expect(parseReplanMessage("gone")).toBe("");
    expect(parseReplanMessage({ detail: ["array"] })).toBe("");
    expect(parseReplanMessage({ detail: { replan: ["array"] } })).toBe("");
    expect(parseReplanMessage({ detail: { replan: "text" } })).toBe("");
    expect(parseReplanMessage({ detail: { replan: { message: 7 } } })).toBe("");
    expect(parseReplanMessage({ detail: {} })).toBe("");
  });
});

describe("resolveApprovalRequest failures", () => {
  it("treats a lost run (404) exactly like an expired token", async () => {
    mockResume({ ok: false, status: 404, error: "Run not found." });
    const resolution = await resolveApprovalRequest({ runId: "run-1", token: "token-1" }, { approve: true });
    expect(resolution).toEqual({ kind: "expired" });
  });

  it("reports other failures with the error text or the bare status", async () => {
    mockResume({ ok: false, status: 500, error: "internal" });
    expect(
      await resolveApprovalRequest({ runId: "run-1", token: "token-1" }, { approve: true }),
    ).toEqual({ kind: "error", reason: "internal" });

    mockResume({ ok: false, status: 500 });
    expect(
      await resolveApprovalRequest({ runId: "run-1", token: "token-1" }, { approve: true }),
    ).toEqual({ kind: "error", reason: "500" });

    mockResume({ ok: false, status: 0 });
    expect(
      await resolveApprovalRequest({ runId: "run-1", token: "token-1" }, { approve: true }),
    ).toEqual({ kind: "error", reason: "" });
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

  it("rejects JSON that is valid but not a plan object", async () => {
    const resume = mockResume({ ok: true, status: 200, data: { status: "ok" } });
    render(
      <AgentApprovalCard language="ko" approval={pendingApproval()} onResolved={() => {}} />,
    );
    await userEvent.click(screen.getByTestId("approval-edit"));
    const textarea = screen.getByTestId("approval-edit-textarea") as HTMLTextAreaElement;

    for (const value of ["null", "7", "[1, 2]"]) {
      fireEvent.change(textarea, { target: { value } });
      await userEvent.click(screen.getByTestId("approval-edit-run"));
      expect(screen.getByRole("alert")).toBeTruthy();
    }
    expect(resume).not.toHaveBeenCalled();
  });

  it("lets the user leave the editor without running anything", async () => {
    const resume = mockResume({ ok: true, status: 200, data: { status: "ok" } });
    render(
      <AgentApprovalCard language="ko" approval={pendingApproval({ plan: null })} onResolved={() => {}} />,
    );
    await userEvent.click(screen.getByTestId("approval-edit"));
    // Without a normalized plan the editor seeds from the summary.
    const textarea = screen.getByTestId("approval-edit-textarea") as HTMLTextAreaElement;
    expect(textarea.value).toContain("1. write_file → page.html");

    await userEvent.click(screen.getByRole("button", { name: "수정 닫기" }));
    expect(screen.queryByTestId("approval-edit-textarea")).toBeNull();
    expect(screen.getByTestId("approval-approve")).toBeTruthy();
    expect(resume).not.toHaveBeenCalled();
  });

  it("shows the busy label on the editor's run button while resuming", async () => {
    let release: (value: unknown) => void = () => {};
    vi.spyOn(latticeApi, "resumeAgentApproval").mockReturnValue(
      new Promise((resolve) => { release = resolve; }) as never,
    );
    render(
      <AgentApprovalCard language="ko" approval={pendingApproval()} onResolved={() => {}} />,
    );
    await userEvent.click(screen.getByTestId("approval-edit"));
    await userEvent.click(screen.getByTestId("approval-edit-run"));
    expect(screen.getByTestId("approval-edit-run").textContent).toContain("실행 중");
    // A second press while busy is ignored.
    fireEvent.click(screen.getByTestId("approval-edit-run"));
    expect(latticeApi.resumeAgentApproval).toHaveBeenCalledTimes(1);
    release({ ok: true, status: 200, source: "live", data: { status: "ok" } });
    await waitFor(() =>
      expect((screen.getByTestId("approval-edit-run") as HTMLButtonElement).disabled).toBe(false),
    );
  });

  it("renders without countdown or expiry line when the TTL is unknown", () => {
    render(
      <AgentApprovalCard
        language="ko"
        approval={pendingApproval({ expiresAt: "", planSummary: "" })}
        onResolved={() => {}}
      />,
    );
    expect(screen.queryByTestId("approval-countdown")).toBeNull();
    expect(document.querySelector(".brain-approval-expiry")).toBeNull();
    expect(screen.queryByTestId("approval-plan-summary")).toBeNull();
  });

  it("treats an unparseable expiry timestamp like no TTL at all", () => {
    render(
      <AgentApprovalCard
        language="en"
        approval={pendingApproval({ expiresAt: "definitely-not-a-date" })}
        onResolved={() => {}}
      />,
    );
    expect(screen.queryByTestId("approval-countdown")).toBeNull();
    expect(document.querySelector(".brain-approval-expiry")).toBeNull();
  });

  it("formats the expiry clock time in en-US for English", () => {
    render(
      <AgentApprovalCard
        language="en"
        approval={pendingApproval({ expiresAt: "2026-07-22T09:00:00+00:00" })}
        onResolved={() => {}}
      />,
    );
    const expected = new Date("2026-07-22T09:00:00+00:00").toLocaleTimeString("en-US", {
      hour: "2-digit",
      minute: "2-digit",
    });
    expect(document.querySelector(".brain-approval-expiry")!.textContent).toContain(expected);
  });

  it("renders each terminal status with its honest note", () => {
    const { rerender } = render(
      <AgentApprovalCard
        language="ko"
        approval={pendingApproval({ status: "approved" })}
        onResolved={() => {}}
      />,
    );
    expect(screen.getByTestId("agent-approval-note").getAttribute("role")).toBe("status");

    rerender(
      <AgentApprovalCard
        language="ko"
        approval={pendingApproval({ status: "cancelled" })}
        onResolved={() => {}}
      />,
    );
    const cancelled = screen.getByTestId("agent-approval-note");
    expect(cancelled.getAttribute("role")).toBe("alert");
    expect(cancelled.className).toContain("is-cancelled");

    rerender(
      <AgentApprovalCard
        language="ko"
        approval={pendingApproval({ status: "error", errorReason: "resume broke" })}
        onResolved={() => {}}
      />,
    );
    const errored = screen.getByTestId("agent-approval-note");
    expect(errored.textContent).toContain("resume broke");
    expect(errored.className).toContain("is-error");
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
