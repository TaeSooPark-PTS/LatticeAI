import { describe, expect, it } from "vitest";

import { APP_VERSION, COPY, t } from "./i18n";

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
