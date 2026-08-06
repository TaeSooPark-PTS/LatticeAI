/**
 * The formatting helpers every screen leans on.
 *
 * All of them take `unknown` on purpose — the values come from API envelopes
 * that may be partial, null, or a string where a number was expected — so the
 * contract that matters is what each one does with the bad input, not the good
 * one. These tests drive both halves of every guard.
 */

import { describe, expect, it } from "vitest";

import {
  asArray,
  clamp,
  cn,
  fmtNumber,
  humanizeModelId,
  isRecord,
  pct,
  plainText,
  shortId,
  titleize,
} from "./utils";

describe("cn", () => {
  it("merges class names and lets the later Tailwind utility win", () => {
    expect(cn("p-2", "p-4")).toBe("p-4");
  });

  it("drops falsy and conditional entries", () => {
    expect(cn("a", false, null, undefined, ["b"], { c: true, d: false })).toBe("a b c");
  });
});

describe("fmtNumber", () => {
  it("formats a finite number with thousands separators", () => {
    expect(fmtNumber(1234567)).toBe(new Intl.NumberFormat().format(1234567));
    expect(fmtNumber("42")).toBe("42");
  });

  it("falls back rather than printing NaN", () => {
    expect(fmtNumber(undefined)).toBe("0");
    expect(fmtNumber("not a number")).toBe("0");
    expect(fmtNumber({}, "—")).toBe("—");
    // `Number(null)` is 0, not NaN — a null counter still prints as zero.
    expect(fmtNumber(null)).toBe("0");
  });
});

describe("pct", () => {
  it("renders a 0..1 ratio as a rounded percentage", () => {
    expect(pct(0.256)).toBe("26%");
    expect(pct(1)).toBe("100%");
  });

  it("degrades to 0% for a value that is not a number", () => {
    expect(pct("high")).toBe("0%");
    expect(pct(Infinity)).toBe("0%");
  });
});

describe("shortId", () => {
  it("passes short values through untouched", () => {
    expect(shortId("abc")).toBe("abc");
  });

  it("truncates past the limit and honours a custom length", () => {
    expect(shortId("0123456789abcdef")).toBe("0123456789...");
    expect(shortId("0123456789abcdef", 4)).toBe("0123...");
  });

  it("treats an absent id as an empty string", () => {
    expect(shortId(null)).toBe("");
    expect(shortId(undefined, 3)).toBe("");
  });
});

describe("asArray / isRecord", () => {
  it("asArray returns arrays as-is and everything else as empty", () => {
    const source = [{ id: 1 }];
    expect(asArray(source)).toBe(source);
    expect(asArray(undefined)).toEqual([]);
    expect(asArray({ items: [] })).toEqual([]);
  });

  it("isRecord accepts plain objects only", () => {
    expect(isRecord({ a: 1 })).toBe(true);
    expect(isRecord([])).toBe(false);
    expect(isRecord(null)).toBe(false);
    expect(isRecord("text")).toBe(false);
  });
});

describe("clamp", () => {
  it("keeps a value inside its bounds", () => {
    expect(clamp(5, 0, 10)).toBe(5);
    expect(clamp(-3, 0, 10)).toBe(0);
    expect(clamp(42, 0, 10)).toBe(10);
  });
});

describe("titleize", () => {
  it("turns an identifier into words with capitals", () => {
    expect(titleize("kg_change-digest")).toBe("Kg Change Digest");
  });

  it("returns an empty string for an absent value", () => {
    expect(titleize(null)).toBe("");
  });
});

describe("plainText", () => {
  it("returns an empty string for every absent value", () => {
    expect(plainText(null)).toBe("");
    expect(plainText(undefined)).toBe("");
    expect(plainText("")).toBe("");
  });

  it("strips the Markdown a model leaves in titles and summaries", () => {
    const source = [
      "## 제목",
      "> 인용문",
      "- **요약하자면,** _중요_ 합니다",
      "`code` and [링크](https://example.com) and ![img](a.png)",
      "```",
      "dropped = True",
      "```",
    ].join("\n");
    const result = plainText(source);
    expect(result).toBe("제목 인용문 요약하자면, 중요 합니다 code and 링크 and img");
    expect(result).not.toContain("**");
    expect(result).not.toContain("```");
  });

  it("treats a lone dash placeholder as no text at all", () => {
    // Backends write "-" for "nothing here"; a caller must be able to fall
    // through to the next candidate instead of rendering a stray hyphen.
    expect(plainText("-")).toBe("");
  });
});

describe("humanizeModelId", () => {
  it("drops the registry and the quantisation suffix", () => {
    expect(humanizeModelId("mlx-community/gemma-4-26b-a4b-it-4bit")).toBe("Gemma 4 26b A4b It");
  });

  it("leaves words that already start with a capital or a digit alone", () => {
    expect(humanizeModelId("Qwen3-8B-bf16")).toBe("Qwen3 8B");
  });

  it("keeps the original id when nothing recognisable survives", () => {
    expect(humanizeModelId("mlx")).toBe("mlx");
    expect(humanizeModelId("")).toBe("");
  });
});
