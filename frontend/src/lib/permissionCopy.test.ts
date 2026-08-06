/**
 * The autonomy dial reads its own words, not the server's.
 *
 * `latticeai/core/permission_mode.py` ships each mode with English and Korean
 * text, and the Korean was the engineering term transliterated. So the rule
 * here is: translate by **id**, and fall back to whatever the server sent when
 * this app has no copy for that id — a mode added server-side must still
 * render rather than disappear. Both halves are asserted below.
 */

import { describe, expect, it } from "vitest";

import {
  activeModeLabel,
  permissionModeLabel,
  permissionModeSummary,
  permissionModeWarning,
} from "./permissionCopy";

// A mode this app has no copy for: every lookup must fall through to the
// server's own strings.
const unknownMode = {
  id: "shadow_mode",
  label: "Shadow mode",
  label_ko: "그림자 모드",
  summary: "Runs quietly.",
  summary_ko: "조용히 실행합니다.",
  warning: "Experimental.",
  warning_ko: "실험 기능입니다.",
};

describe("permissionModeLabel", () => {
  it("prefers this app's copy over the server label", () => {
    expect(permissionModeLabel({ id: "strict", label: "Ask me first" }, "ko")).toBe("먼저 물어보기");
    expect(permissionModeLabel({ id: "bypass", label: "Bypass" }, "en")).toBe(
      "Almost everything on its own",
    );
  });

  it("falls back to the server label for a mode it has never heard of", () => {
    expect(permissionModeLabel(unknownMode, "ko")).toBe("그림자 모드");
    expect(permissionModeLabel(unknownMode, "en")).toBe("Shadow mode");
  });

  it("falls back to the English label when the server sent no Korean one", () => {
    expect(permissionModeLabel({ ...unknownMode, label_ko: null }, "ko")).toBe("Shadow mode");
  });

  it("renders nothing rather than 'undefined' when the server sent no label", () => {
    expect(permissionModeLabel({ id: "shadow_mode", label: "" }, "en")).toBe("");
  });
});

describe("permissionModeSummary", () => {
  it("prefers this app's copy", () => {
    expect(permissionModeSummary({ id: "trusted", label: "x", summary: "server" }, "ko")).toContain(
      "작업 공간",
    );
  });

  it("falls back per language for an unknown mode", () => {
    expect(permissionModeSummary(unknownMode, "ko")).toBe("조용히 실행합니다.");
    expect(permissionModeSummary(unknownMode, "en")).toBe("Runs quietly.");
    expect(permissionModeSummary({ ...unknownMode, summary_ko: null }, "ko")).toBe("Runs quietly.");
    expect(permissionModeSummary({ id: "shadow_mode", label: "x" }, "en")).toBe("");
  });
});

describe("permissionModeWarning", () => {
  it("uses this app's warning where one exists", () => {
    expect(permissionModeWarning({ id: "bypass", label: "x" }, "en")).toContain("confirmation");
  });

  it("falls back to the server warning per language, and to nothing when absent", () => {
    // `strict` has a label and a summary in this app but deliberately no
    // warning, so this exercises the per-key fallback rather than a whole
    // unknown mode.
    expect(permissionModeWarning({ id: "strict", label: "x", warning_ko: "조심", warning: "Careful" }, "ko")).toBe("조심");
    expect(permissionModeWarning({ id: "strict", label: "x", warning: "Careful" }, "ko")).toBe("Careful");
    expect(permissionModeWarning(unknownMode, "en")).toBe("Experimental.");
    expect(permissionModeWarning({ id: "strict", label: "x" }, "en")).toBe("");
  });
});

describe("activeModeLabel", () => {
  it("returns nothing before the active mode has loaded", () => {
    expect(activeModeLabel(undefined, "ko")).toBe("");
  });

  it("translates the flat active-mode payload by id", () => {
    expect(activeModeLabel({ mode: "trusted", label: "Trusted" }, "ko")).toBe("웬만하면 알아서");
    expect(activeModeLabel({ mode: "trusted", label: "Trusted" }, "en")).toBe("Mostly on its own");
  });

  it("falls back to the payload's own text for an unknown mode", () => {
    expect(activeModeLabel({ mode: "shadow_mode", label: "Shadow", label_ko: "그림자" }, "ko")).toBe("그림자");
  });

  it("survives a payload with neither a mode nor a label", () => {
    expect(activeModeLabel({}, "en")).toBe("");
  });
});
