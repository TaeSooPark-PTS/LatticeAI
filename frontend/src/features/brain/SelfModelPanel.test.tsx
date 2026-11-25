import "@/i18n/brain";
import { fireEvent, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { latticeApi } from "@/api/client";
import * as nav from "@/features/brain/navigation";
import { fail, ok, renderPage } from "@/test/renderPage";
import { extractPendingSelfProposals, SelfModelPanel } from "./SelfModelPanel";

describe("extractPendingSelfProposals", () => {
  it("filters only pending self_model_fact items with non-empty id and text", () => {
    expect(extractPendingSelfProposals(null)).toEqual([]);
    expect(
      extractPendingSelfProposals({
        items: [
          { id: "r1", kind: "self_model_fact", status: "pending", payload: { self_model_kind: "habit", text: "매일 회고" } },
          { id: "r2", kind: "self_model_fact", effective_status: "snoozed", payload: { text: "보류됨" } },
          { id: "r3", kind: "contradiction", status: "pending", title: "무시" },
          { id: "r4", kind: "self_model_fact", title: "제목 폴백" },
          { id: "", kind: "self_model_fact", payload: { text: "빈 ID" } },
          { kind: "self_model_fact", payload: {} },
        ],
      }),
    ).toEqual([
      { id: "r1", kind: "habit", text: "매일 회고" },
      { id: "r4", kind: "preference", text: "제목 폴백" },
    ]);
  });
});

describe("SelfModelPanel", () => {
  beforeEach(() => vi.restoreAllMocks());

  it("renders empty state, adds a new fact, and handles failed upsert without clearing input", async () => {
    const upsertOk = vi.fn().mockResolvedValueOnce({ ok: false, status: 400, data: {}, source: "unavailable" }).mockResolvedValueOnce({
      ok: true,
      status: 200,
      source: "live",
      data: { ok: true },
    });

    renderPage(<SelfModelPanel />, {
      api: {
        selfModel: ok({ available: true, count: 0, facts: [], by_kind: {}, summary: "", kinds: [] }),
        automationReviews: ok({ items: [] }),
        upsertSelfModelFact: upsertOk,
      },
    });

    await screen.findByTestId("self-model-empty");
    expect(screen.queryByTestId("self-model-summary")).toBeNull();
    expect(screen.queryByTestId("self-model-proposals")).toBeNull();

    fireEvent.submit(screen.getByTestId("self-model-input").closest("form")!);
    expect(upsertOk).not.toHaveBeenCalled();

    fireEvent.change(screen.getByTestId("self-model-kind-select"), { target: { value: "decision" } });
    fireEvent.change(screen.getByTestId("self-model-input"), { target: { value: "  항상 로컬 우선 유지  " } });

    fireEvent.click(screen.getByTestId("self-model-submit"));
    await waitFor(() => expect(upsertOk).toHaveBeenCalledWith("decision", "항상 로컬 우선 유지"));
    expect((screen.getByTestId("self-model-input") as HTMLInputElement).value).toBe("  항상 로컬 우선 유지  ");

    fireEvent.click(screen.getByTestId("self-model-submit"));
    await waitFor(() => expect((screen.getByTestId("self-model-input") as HTMLInputElement).value).toBe(""));
  });

  it("renders summary, facts (user/approved/no-origin), deletes facts, approves/dismisses proposals, and opens Review Center", async () => {
    const navSpy = vi.spyOn(nav, "navigateHash").mockImplementation(() => {});
    const delSpy = vi.fn().mockResolvedValue({ ok: true, status: 200, source: "live", data: { deleted: true } });
    const applySpy = vi.fn().mockResolvedValue({ ok: true, status: 200, source: "live", data: { ok: true } });
    const reviewSpy = vi.fn().mockResolvedValue({ ok: true, status: 200, source: "live", data: { ok: true } });

    renderPage(<SelfModelPanel />, {
      api: {
        selfModel: ok({
          available: true,
          count: 3,
          summary: "- 선호: 간결한 답변",
          by_kind: {},
          kinds: [],
          facts: [
            { id: "self:1", kind: "preference", text: "간결한 답변", origin: "user" },
            { id: "self:2", kind: "habit", text: "아침 회고", origin: "proposal" },
            { id: "self:3", kind: "trait", text: "시스템 엔지니어" },
          ],
        }),
        automationReviews: ok({
          items: [
            {
              id: "prop-1",
              kind: "self_model_fact",
              status: "pending",
              payload: { self_model_kind: "preference", text: "다크 모드 선호" },
            },
          ],
        }),
        deleteSelfModelFact: delSpy,
        applySelfModelProposal: applySpy,
        dismissReviewItem: reviewSpy,
      },
    });

    expect(await screen.findByTestId("self-model-summary")).toHaveTextContent("선호: 간결한 답변");
    expect(screen.getAllByTestId("self-model-fact-row")).toHaveLength(3);
    expect(screen.getByText("직접 작성")).toBeTruthy();
    expect(screen.getByText("제안 승인")).toBeTruthy();

    const proposalRow = screen.getByTestId("self-model-proposal-row");
    expect(proposalRow).toHaveTextContent("다크 모드 선호");
    fireEvent.click(screen.getByText("승인 반영"));
    await waitFor(() => expect(applySpy).toHaveBeenCalledWith("prop-1"));

    fireEvent.click(screen.getByText("거절"));
    await waitFor(() => expect(reviewSpy).toHaveBeenCalledWith("prop-1"));

    fireEvent.click(screen.getByRole("button", { name: "삭제 간결한 답변" }));
    await waitFor(() => expect(delSpy).toHaveBeenCalledWith("self:1"));

    fireEvent.click(screen.getByTestId("self-model-open-review"));
    expect(navSpy).toHaveBeenCalledWith("/review");
  });

  it("handles unavailable API responses gracefully", async () => {
    vi.spyOn(latticeApi, "selfModel");
    renderPage(<SelfModelPanel />, {
      api: {
        selfModel: fail("offline", {}),
        automationReviews: fail("offline", {}),
      },
    });
    expect(await screen.findByTestId("self-model-empty")).toBeTruthy();
  });
});
