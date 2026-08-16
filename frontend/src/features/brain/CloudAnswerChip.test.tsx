import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { t } from "@/i18n";
import { CloudAnswerChip } from "./CloudAnswerChip";
import type { MessageCloudAnswer } from "./types";

function renderChip(cloudAnswer: MessageCloudAnswer, language: "ko" | "en" = "ko") {
  return render(<CloudAnswerChip language={language} cloudAnswer={cloudAnswer} />);
}

describe("CloudAnswerChip", () => {
  it("names the model on the chip and expands the staged proposal summary", () => {
    renderChip({
      provider: "Antigravity",
      model: "gemini-3.7-flash",
      sentNodeCount: 3,
      expansion: { status: "staged", candidateCount: 2, stagedForReview: true },
    });

    expect(screen.getByTestId("cloud-answer-chip").textContent)
      .toContain(t("ko", "brain.cloud.chip.model", { model: "gemini-3.7-flash" }));
    expect(screen.getByLabelText(t("ko", "brain.cloud.chip.expandAria"))).toBeTruthy();

    fireEvent.click(screen.getByLabelText(t("ko", "brain.cloud.chip.expandAria")));
    expect(screen.getByText(t("ko", "brain.cloud.detail", { nodes: 3, proposals: 2 }))).toBeTruthy();
  });

  it("drops the model when none was reported and lists only the sent-memory count", () => {
    renderChip({
      provider: "",
      model: "",
      sentNodeCount: 0,
      expansion: { status: "ok", candidateCount: 0, stagedForReview: false },
    });

    expect(screen.getByTestId("cloud-answer-chip").textContent)
      .toContain(t("ko", "brain.cloud.chip"));
    expect(screen.getByTestId("cloud-answer-chip").textContent)
      .not.toContain("·");
    fireEvent.click(screen.getByLabelText(t("ko", "brain.cloud.chip.expandAria")));
    expect(screen.getByText(t("ko", "brain.cloud.detail.nodes", { nodes: 0 }))).toBeTruthy();
  });

  it("uses the English keys when the language is en", () => {
    renderChip({
      provider: "openai",
      model: "gpt-4o",
      sentNodeCount: 1,
      expansion: null,
    }, "en");

    expect(screen.getByTestId("cloud-answer-chip").textContent)
      .toContain(t("en", "brain.cloud.chip.model", { model: "gpt-4o" }));
    fireEvent.click(screen.getByLabelText(t("en", "brain.cloud.chip.expandAria")));
    expect(screen.getByText(t("en", "brain.cloud.detail.nodes", { nodes: 1 }))).toBeTruthy();
  });
});
