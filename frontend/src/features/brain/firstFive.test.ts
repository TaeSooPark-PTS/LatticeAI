import { describe, expect, it } from "vitest";

import {
  countFirstFiveDone,
  dismissFirstFive,
  FIRST_FIVE_STEPS,
  isFirstFiveComplete,
  markFirstFiveStepDone,
  readFirstFiveState,
  shouldShowFirstFive,
} from "./firstFive";

describe("firstFive checklist state", () => {
  it("starts with nothing done and the card visible", () => {
    const state = readFirstFiveState();
    expect(state.dismissed).toBe(false);
    expect(countFirstFiveDone(state)).toBe(0);
    expect(shouldShowFirstFive(state)).toBe(true);
  });

  it("persists step completion in localStorage", () => {
    let state = markFirstFiveStepDone("ask");
    expect(state.done.ask).toBe(true);
    expect(countFirstFiveDone(state)).toBe(1);
    expect(localStorage.getItem("lattice.firstFive.done.ask")).toBe("true");
    // A fresh read (new session) sees the same progress.
    state = readFirstFiveState();
    expect(state.done.ask).toBe(true);
    expect(state.done.add).toBe(false);
    expect(shouldShowFirstFive(state)).toBe(true);
  });

  it("hides the card once every step is done", () => {
    for (const step of FIRST_FIVE_STEPS) markFirstFiveStepDone(step);
    const state = readFirstFiveState();
    expect(isFirstFiveComplete(state)).toBe(true);
    expect(shouldShowFirstFive(state)).toBe(false);
  });

  it("never shows again after dismissal, even with steps left", () => {
    markFirstFiveStepDone("ask");
    const state = dismissFirstFive();
    expect(state.dismissed).toBe(true);
    expect(isFirstFiveComplete(state)).toBe(false);
    expect(shouldShowFirstFive(state)).toBe(false);
    expect(localStorage.getItem("lattice.firstFive.dismissed")).toBe("true");
  });
});
