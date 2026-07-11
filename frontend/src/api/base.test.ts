import { describe, expect, it } from "vitest";

import { emptyFor } from "./base";

describe("emptyFor", () => {
  it("returns an empty array for list response shapes", () => {
    expect(emptyFor([{ id: "not-a-fallback" }])).toEqual([]);
  });

  it("preserves the declared object shape without returning the same object", () => {
    const shape = { items: [], total: 0, available: false };
    const fallback = emptyFor(shape);

    expect(fallback).toEqual(shape);
    expect(fallback).not.toBe(shape);
  });

  it("preserves primitive empty shapes", () => {
    expect(emptyFor(0)).toBe(0);
    expect(emptyFor("")).toBe("");
    expect(emptyFor(null)).toBeNull();
  });
});
