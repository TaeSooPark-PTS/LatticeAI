import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { AgentStepTimeline, LoopRepairsNote, RunExplanationNote, stepKind } from "./AgentStepTimeline";
import type { AgentStepEvent } from "./types";

const STEPS: AgentStepEvent[] = [
  { phase: "plan", event: "planned", step: 1 },
  { phase: "execute", event: "tool", action: "write_file", path: "out/notes.html", ok: true },
  { phase: "execute", event: "tool", action: "read_file", path: "a.txt", ok: false, detail: "not found" },
  { phase: "execute", event: "blocked", action: "delete_path" },
  { phase: "execute", event: "proposed", action: "write_file", decision: "proposal" },
];

describe("stepKind", () => {
  it("classifies events into dot states", () => {
    expect(stepKind({ phase: "execute", event: "tool", ok: true })).toBe("ok");
    expect(stepKind({ phase: "execute", event: "tool", ok: false })).toBe("error");
    expect(stepKind({ phase: "execute", event: "blocked" })).toBe("blocked");
    expect(stepKind({ phase: "execute", event: "parse_error" })).toBe("error");
    expect(stepKind({ phase: "execute", event: "proposed" })).toBe("proposed");
    expect(stepKind({ phase: "rollback", event: "tool" })).toBe("ok");
    expect(stepKind({ phase: "rollback", event: "state" })).toBe("error");
    expect(stepKind({ phase: "terminal", event: "state", state: "DONE" })).toBe("info");
  });
});

describe("AgentStepTimeline", () => {
  it("renders a live expanded list with a progress header while streaming", () => {
    render(<AgentStepTimeline language="ko" steps={STEPS} streaming />);
    const timeline = screen.getByTestId("agent-step-timeline");

    expect(timeline.textContent).toContain("5단계 진행 중");
    // Tool rows show "label + action · basename" from data, not copy.
    expect(timeline.textContent).toContain("write_file · notes.html");
    expect(timeline.textContent).toContain("read_file · a.txt");
    expect(timeline.textContent).toContain("not found");
    expect(timeline.querySelectorAll(".brain-step-item").length).toBe(5);
    expect(timeline.querySelectorAll(".brain-step-item.is-ok").length).toBe(1);
    expect(timeline.querySelectorAll(".brain-step-item.is-error").length).toBe(1);
    expect(timeline.querySelectorAll(".brain-step-item.is-blocked").length).toBe(1);
    expect(timeline.querySelectorAll(".brain-step-item.is-proposed").length).toBe(1);
  });

  it("collapses to an expandable summary once the run is done", () => {
    render(<AgentStepTimeline language="ko" steps={STEPS} streaming={false} />);
    const timeline = screen.getByTestId("agent-step-timeline") as HTMLDetailsElement;

    expect(timeline.tagName).toBe("DETAILS");
    expect(timeline.open).toBe(false);
    expect(timeline.querySelector("summary")?.textContent).toContain("5단계 실행됨");
    // The list is still in the DOM, ready to expand.
    expect(timeline.querySelectorAll(".brain-step-item").length).toBe(5);
  });

  it("renders nothing without steps", () => {
    render(<AgentStepTimeline language="ko" steps={[]} streaming={false} />);
    expect(screen.queryByTestId("agent-step-timeline")).toBeNull();
  });

  it("labels the verify/terminal event family and falls back for unknown events", () => {
    render(
      <AgentStepTimeline
        language="ko"
        streaming
        steps={[
          { phase: "execute", event: "parse_error", detail: "json broke" },
          { phase: "verify", event: "verdict", verdict: "PASS" },
          { phase: "terminal", event: "final" },
          { phase: "terminal", event: "state", state: "DONE" },
          { phase: "rollback", event: "state", state: "REVERTED" },
          { phase: "plan", event: "planned" },
          { phase: "future", event: "hologram" },
        ]}
      />,
    );
    const timeline = screen.getByTestId("agent-step-timeline");
    expect(timeline.textContent).toContain("모델 응답 보정");
    expect(timeline.textContent).toContain("검증 결과");
    expect(timeline.textContent).toContain("PASS");
    expect(timeline.textContent).toContain("마무리");
    expect(timeline.textContent).toContain("상태 변경");
    expect(timeline.textContent).toContain("변경 되돌림");
    expect(timeline.textContent).toContain("계획 수립");
    // Unknown backend additions degrade to the neutral step label.
    expect(timeline.textContent).toContain("단계");
  });

  it("shows only the newest thirty steps of a very long run", () => {
    const steps: AgentStepEvent[] = Array.from({ length: 33 }, (_, index) => ({
      phase: "execute",
      event: "tool",
      action: `tool_${index}`,
      ok: true,
    }));
    render(<AgentStepTimeline language="ko" steps={steps} streaming />);
    const timeline = screen.getByTestId("agent-step-timeline");
    expect(timeline.textContent).toContain("33단계 진행 중");
    expect(timeline.querySelectorAll(".brain-step-item").length).toBe(30);
    expect(timeline.textContent).not.toContain("tool_2 ");
    expect(timeline.textContent).toContain("tool_32");
  });

  it("falls back to the raw path when it has no segment left after trimming slashes", () => {
    render(
      <AgentStepTimeline
        language="ko"
        streaming
        steps={[{ phase: "execute", event: "tool", action: "write_file", path: "///", ok: true }]}
      />,
    );
    const timeline = screen.getByTestId("agent-step-timeline");
    expect(timeline.textContent).toContain("write_file · ///");
  });
});

describe("LoopRepairsNote", () => {
  it("shows the repair count with the top kinds in the tooltip", () => {
    render(
      <LoopRepairsNote
        language="ko"
        summary={{ repairs: { json_fence: 2, tool_name: 1 }, parseErrors: 3, parseRecovered: 2, total: 5 }}
      />,
    );
    const note = screen.getByTestId("loop-repairs-note");
    expect(note.textContent).toContain("5회 보정");
    expect(note.getAttribute("title")).toContain("json_fence ×2");
    expect(note.getAttribute("title")).toContain("parse ×2");
  });

  it("stays silent when nothing was repaired", () => {
    render(
      <LoopRepairsNote
        language="ko"
        summary={{ repairs: {}, parseErrors: 0, parseRecovered: 0, total: 0 }}
      />,
    );
    expect(screen.queryByTestId("loop-repairs-note")).toBeNull();
  });

  it("omits the tooltip when the loop cannot name its repairs", () => {
    render(
      <LoopRepairsNote
        language="ko"
        summary={{ repairs: {}, parseErrors: 0, parseRecovered: 0, total: 2 }}
      />,
    );
    const note = screen.getByTestId("loop-repairs-note");
    expect(note.textContent).toContain("2회 보정");
    expect(note.getAttribute("title")).toBeNull();
  });
});

describe("RunExplanationNote", () => {
  it("renders a strained but successful run as ok with its details", () => {
    render(
      <RunExplanationNote
        language="ko"
        explanation={{
          code: "done",
          ok: true,
          headline: "끝났지만 힘들었어요.",
          details: ["형식을 3번 고쳤습니다."],
          strainLevel: "heavy",
        }}
      />,
    );
    const note = screen.getByTestId("run-explanation");
    expect(note.className).toContain("is-ok");
    expect(note.className).toContain("strain-heavy");
    expect(note.querySelector("strong")!.textContent).toBe("끝났지만 힘들었어요.");
    expect(note.querySelectorAll("li").length).toBe(1);
  });

  it("still renders the why-line when headline and details are empty", () => {
    render(
      <RunExplanationNote
        language="ko"
        explanation={{ code: "failed", ok: false, headline: "", details: [], strainLevel: "none" }}
      />,
    );
    const note = screen.getByTestId("run-explanation");
    expect(note.className).toContain("is-caution");
    expect(note.querySelector("strong")).toBeNull();
    expect(note.querySelector("ul")).toBeNull();
    expect(note.querySelector("small")).toBeTruthy();
  });
});
