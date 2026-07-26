import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { RunExplanationNote } from "./AgentStepTimeline";
import { parseRunExplanation } from "./brainData";

const NEEDS_REVIEW = {
  code: "no_evidence",
  ok: false,
  state: "NEEDS_REVIEW",
  headline: { ko: "실행 기록이 없어 완료로 보지 않았습니다.", en: "Not marked complete." },
  details: [
    { ko: "모델이 형식을 2번 벗어났습니다.", en: "The model broke the format twice." },
  ],
  model_strain: { level: "moderate" },
};

describe("parseRunExplanation", () => {
  it("picks the surface language and keeps the strain level", () => {
    const ko = parseRunExplanation(NEEDS_REVIEW, "ko");
    expect(ko?.headline).toBe("실행 기록이 없어 완료로 보지 않았습니다.");
    expect(ko?.details).toEqual(["모델이 형식을 2번 벗어났습니다."]);
    expect(ko?.strainLevel).toBe("moderate");
    expect(parseRunExplanation(NEEDS_REVIEW, "en")?.headline).toBe("Not marked complete.");
  });

  it("stays silent for a clean, verified run", () => {
    const clean = { code: "done", ok: true, headline: { ko: "끝났습니다.", en: "Done." }, details: [] };
    expect(parseRunExplanation(clean, "ko")).toBeNull();
  });

  it("still explains a successful run that needed repairs", () => {
    const strained = {
      code: "done",
      ok: true,
      headline: { ko: "끝났습니다.", en: "Done." },
      details: [{ ko: "형식을 3번 고쳤습니다.", en: "Fixed the format 3 times." }],
      model_strain: { level: "heavy" },
    };
    expect(parseRunExplanation(strained, "ko")?.strainLevel).toBe("heavy");
  });

  it("returns null for garbage payloads", () => {
    for (const bad of [null, undefined, 7, "x", {}]) {
      expect(parseRunExplanation(bad, "ko")).toBeNull();
    }
  });
});

describe("RunExplanationNote", () => {
  it("renders a non-DONE outcome as a caution, never as success", () => {
    const explanation = parseRunExplanation(NEEDS_REVIEW, "ko")!;
    render(<RunExplanationNote language="ko" explanation={explanation} />);
    const note = screen.getByTestId("run-explanation");
    expect(note.className).toContain("is-caution");
    expect(note.getAttribute("data-code")).toBe("no_evidence");
    expect(screen.getByText("모델이 형식을 2번 벗어났습니다.")).toBeTruthy();
  });
});
