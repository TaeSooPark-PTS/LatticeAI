import { describe, expect, it } from "vitest";

import { APP_VERSION, COPY, resolveAppVersion, t } from "./i18n";
// Namespaces register on import. The app pulls them in per lazy route; the
// parity check needs all of them, so import every one explicitly.
import "./i18n/brain";
import "./i18n/onboarding";
import "./i18n/workspace";

describe("i18n namespaces", () => {
  it("keeps Korean and English namespace keys aligned", () => {
    expect(Object.keys(COPY.ko).sort()).toEqual(Object.keys(COPY.en).sort());
  });

  it("injects the package version into release copy", () => {
    expect(t("ko", "brain.edition.tip")).toContain(APP_VERSION);
    expect(t("en", "brain.edition.tip")).toContain(APP_VERSION);
  });

  it("localizes primary work, library, and system actions", () => {
    expect(t("ko", "act.action.approve")).toBe("승인");
    expect(t("en", "library.model.recommended")).toBe("recommended");
    expect(t("ko", "system.archive.verify")).toBe("검증");
    expect(t("en", "system.workspace.activate")).toBe("Activate");
  });
});

describe("resolveAppVersion", () => {
  it("uses the string the build injected", () => {
    expect(resolveAppVersion(() => "10.9.0")).toBe("10.9.0");
  });

  it("says 'dev' when the identifier does not exist at all", () => {
    // The bare `__APP_VERSION__` reference throws a ReferenceError outside the
    // bundle (plain tsc output, a REPL). That is the case the thunk exists for.
    expect(
      resolveAppVersion(() => {
        throw new ReferenceError("__APP_VERSION__ is not defined");
      }),
    ).toBe("dev");
  });

  it("says 'dev' when something non-string was injected", () => {
    expect(resolveAppVersion(() => 10.9)).toBe("dev");
    expect(resolveAppVersion(() => undefined)).toBe("dev");
  });
});

describe("t() fallbacks", () => {
  it("uses defaultValue instead of printing the raw key", () => {
    // The bug this locks down: `t()` accepted `values` and interpolated them,
    // but never read `defaultValue`, so a caller that passed one still got the
    // key rendered into the UI.
    expect(t("ko", "no.such.key.anywhere", { defaultValue: "준비 중입니다." })).toBe("준비 중입니다.");
    expect(t("en", "no.such.key.anywhere", { defaultValue: "Getting ready." })).toBe("Getting ready.");
  });

  it("never leaks defaultValue into the interpolated text", () => {
    // It was spread into the replacement map, so any copy containing the token
    // `{defaultValue}` would have had the fallback substituted into it.
    expect(t("ko", "flow.install.stage.load", { defaultValue: "SHOULD-NOT-APPEAR" })).not.toContain(
      "SHOULD-NOT-APPEAR",
    );
  });

  it("still falls back to the key when no default is offered", () => {
    expect(t("ko", "no.such.key.anywhere")).toBe("no.such.key.anywhere");
  });

  it("prefers a real translation over the default", () => {
    expect(t("ko", "flow.install.stage.load", { defaultValue: "fallback" })).toBe(
      "Brain을 불러오는 중입니다.",
    );
  });

  it("has copy for every install stage the screen can be in", () => {
    // InstallScreen builds the key from its stage union. A stage without copy
    // renders `flow.install.stage.<stage>` to the person installing.
    const stages = ["idle", "install", "download", "validate", "load", "done", "error"];
    for (const stage of stages) {
      const key = `flow.install.stage.${stage}`;
      expect(t("ko", key), `missing ko copy for ${key}`).not.toBe(key);
      expect(t("en", key), `missing en copy for ${key}`).not.toBe(key);
    }
  });
});
