import { describe, expect, it } from "vitest";
import { formatModelProvenanceChain } from "./libraryDomain";

describe("formatModelProvenanceChain", () => {
  it("formats country -> vendor -> execution location hierarchy", () => {
    expect(
      formatModelProvenanceChain(
        {
          source_country: "중국",
          source_company: "Alibaba",
          execution_method: "로컬 PC 실행",
        },
        {},
      ),
    ).toBe("중국 → Alibaba → 로컬 PC 실행");
  });

  it("falls back through recommendation fields and handles empty records", () => {
    expect(
      formatModelProvenanceChain(
        {},
        {
          source_country: "미국",
          organization: "Google",
          execution_method: "로컬 PC 실행",
        },
      ),
    ).toBe("미국 → Google → 로컬 PC 실행");
    expect(formatModelProvenanceChain({ provider: "OpenAI" }, {})).toBe("OpenAI");
    expect(formatModelProvenanceChain({}, { provider: "Anthropic" })).toBe("Anthropic");
    expect(formatModelProvenanceChain({ organization: "Meta" }, {})).toBe("Meta");
    expect(formatModelProvenanceChain({}, {})).toBe("");
  });
});
