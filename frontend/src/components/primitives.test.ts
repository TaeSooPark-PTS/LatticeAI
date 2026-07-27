import { describe, expect, it } from "vitest";

import { plainText } from "@/lib/utils";

describe("plainText", () => {
  it("strips the Markdown a model wrote into a title", () => {
    expect(plainText("**요약하자면,** 당신은 `Lattice AI`를 구축 중입니다."))
      .toBe("요약하자면, 당신은 Lattice AI를 구축 중입니다.");
  });

  it("keeps link text and drops the target", () => {
    expect(plainText("see [the plan](https://example.com/x)")).toBe("see the plan");
  });

  it("removes heading and bullet markers", () => {
    expect(plainText("## 제목\n- 첫째\n- 둘째")).toBe("제목 첫째 둘째");
  });

  it("returns empty for an absent value so callers can fall through", () => {
    expect(plainText(undefined)).toBe("");
    expect(plainText(null)).toBe("");
    expect(plainText("")).toBe("");
  });
});
