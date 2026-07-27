import { beforeEach, describe, expect, it } from "vitest";

import { markProductFlowComplete, readProductFlowComplete } from "./productFlowState";

const NEW_KEY = "lattice.productFlow.complete";

describe("readProductFlowComplete", () => {
  beforeEach(() => localStorage.clear());

  it("is false for a genuinely new browser", () => {
    expect(readProductFlowComplete()).toBe(false);
  });

  it("reads the current flag", () => {
    markProductFlowComplete();
    expect(readProductFlowComplete()).toBe(true);
  });

  it("honours the legacy onboarding flag", () => {
    // A user who finished onboarding on an older build has only this key.
    // Ignoring it sent them back to the first-run wake screen every visit.
    localStorage.setItem("ltcai_onboarding_complete", "true");
    expect(readProductFlowComplete()).toBe(true);
  });

  it("honours the per-account legacy flag", () => {
    localStorage.setItem("ltcai_onboarding_complete_someone@example.com", "true");
    expect(readProductFlowComplete()).toBe(true);
  });

  it("migrates the legacy flag forward so the scan happens once", () => {
    localStorage.setItem("ltcai_onboarding_complete", "true");
    readProductFlowComplete();
    expect(localStorage.getItem(NEW_KEY)).toBe("true");
  });

  it("does not treat a non-true legacy value as complete", () => {
    localStorage.setItem("ltcai_onboarding_complete", "false");
    expect(readProductFlowComplete()).toBe(false);
    expect(localStorage.getItem(NEW_KEY)).toBeNull();
  });

  it("ignores unrelated ltcai keys", () => {
    localStorage.setItem("ltcai_user_email", "you@example.com");
    localStorage.setItem("ltcai_mode", "admin");
    expect(readProductFlowComplete()).toBe(false);
  });
});
