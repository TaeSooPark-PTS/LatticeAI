import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { latticeApi, type EvidenceActionsPayload } from "@/api/client";
import { EvidenceActionRow } from "./AnswerProof";

function mockActions(payload: Partial<EvidenceActionsPayload>, ok = true) {
  const data: EvidenceActionsPayload = {
    sources: [], missing: [], actions: [], reason: "", ...payload,
  };
  return vi.spyOn(latticeApi, "evidenceActions").mockResolvedValue({
    ok, status: ok ? 200 : 500, source: "live", data,
  } as never);
}

const SUMMARY_ACTION = {
  id: "summary",
  kind: "chat",
  label: { ko: "이 근거로 요약 만들기", en: "Summarize from this evidence" },
  prompt: "[근거 자료]\n1. 예산 계획\n\n핵심만 요약해 주세요.",
  source_ids: ["node-a"],
};

describe("EvidenceActionRow", () => {
  it("loads actions only when the user asks for them", async () => {
    const spy = mockActions({ actions: [SUMMARY_ACTION] });
    render(
      <EvidenceActionRow language="ko" query="예산" sourceIds={["node-a"]} onUseEvidence={() => {}} />,
    );
    // Lazy by design: an answer nobody follows up on costs no request.
    expect(spy).not.toHaveBeenCalled();
    await userEvent.click(screen.getByTestId("evidence-actions-open"));
    await waitFor(() => expect(screen.getByTestId("evidence-actions")).toBeTruthy());
    expect(spy).toHaveBeenCalledWith("예산", ["node-a"], "ko");
  });

  it("sends the composed evidence-scoped prompt through the normal chat path", async () => {
    mockActions({ actions: [SUMMARY_ACTION] });
    const onUseEvidence = vi.fn();
    render(
      <EvidenceActionRow language="ko" query="예산" sourceIds={["node-a"]} onUseEvidence={onUseEvidence} />,
    );
    await userEvent.click(screen.getByTestId("evidence-actions-open"));
    await waitFor(() => expect(screen.getByTestId("evidence-action-summary")).toBeTruthy());
    await userEvent.click(screen.getByTestId("evidence-action-summary"));
    expect(onUseEvidence).toHaveBeenCalledWith(SUMMARY_ACTION.prompt);
  });

  it("shows the server's honest reason when no action is groundable", async () => {
    mockActions({ actions: [], reason: "근거로 쓸 출처를 찾지 못했습니다." });
    render(
      <EvidenceActionRow language="ko" query="q" sourceIds={["ghost"]} onUseEvidence={() => {}} />,
    );
    await userEvent.click(screen.getByTestId("evidence-actions-open"));
    await waitFor(() =>
      expect(screen.getByText("근거로 쓸 출처를 찾지 못했습니다.")).toBeTruthy(),
    );
    expect(screen.queryByTestId("evidence-actions")).toBeNull();
  });

  it("renders English labels when the surface language is English", async () => {
    mockActions({ actions: [SUMMARY_ACTION] });
    render(
      <EvidenceActionRow language="en" query="budget" sourceIds={["node-a"]} onUseEvidence={() => {}} />,
    );
    await userEvent.click(screen.getByTestId("evidence-actions-open"));
    await waitFor(() =>
      expect(screen.getByText("Summarize from this evidence")).toBeTruthy(),
    );
  });
});
