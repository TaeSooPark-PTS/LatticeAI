import { afterEach, describe, expect, it, vi } from "vitest";
import { detectWebGLSupport, prefersReducedMotion, resetWebGLSupportCacheForTest } from "./webglSupport";

afterEach(() => {
  resetWebGLSupportCacheForTest();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

function stubContext(impl: (kind: string) => unknown) {
  return vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockImplementation(impl as never);
}

describe("detectWebGLSupport", () => {
  it("releases the probe context and probes only once", () => {
    const loseContext = vi.fn();
    const spy = stubContext(() => ({ getExtension: () => ({ loseContext }) }));
    expect(detectWebGLSupport()).toBe(true);
    expect(detectWebGLSupport()).toBe(true);
    expect(spy).toHaveBeenCalledTimes(1);
    expect(loseContext).toHaveBeenCalledTimes(1);
  });

  it("accepts a WebGL1 context that offers no way to release it", () => {
    stubContext((kind) => (kind === "webgl" ? { getExtension: () => null } : null));
    expect(detectWebGLSupport()).toBe(true);
    resetWebGLSupportCacheForTest();
    stubContext(() => ({}));
    expect(detectWebGLSupport()).toBe(true);
  });

  it("reports no support when there is no context or probing throws", () => {
    stubContext(() => null);
    expect(detectWebGLSupport()).toBe(false);
    resetWebGLSupportCacheForTest();
    stubContext(() => {
      throw new Error("blocked");
    });
    expect(detectWebGLSupport()).toBe(false);
  });
});

describe("prefersReducedMotion", () => {
  it("follows the media query and assumes motion is fine without matchMedia", () => {
    vi.stubGlobal("matchMedia", (query: string) => ({ matches: query === "(prefers-reduced-motion: reduce)" }));
    expect(prefersReducedMotion()).toBe(true);
    vi.stubGlobal("matchMedia", undefined);
    expect(prefersReducedMotion()).toBe(false);
  });
});
