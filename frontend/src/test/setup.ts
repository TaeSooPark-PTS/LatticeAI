import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

// Copy namespaces register on import, and in the app each lazy route imports
// only the ones it reads. A unit test renders a component directly, without its
// route, so register every namespace here — a test has no bundle budget to
// protect. Production coverage is proven separately by
// `scripts/check_i18n_namespace_coverage.mjs`, which fails the build if a chunk
// reads a key it never imported; do not rely on this file for that guarantee.
import "@/i18n/brain";
import "@/i18n/onboarding";
import "@/i18n/shell";
import "@/i18n/workspace";

// jsdom ships no ResizeObserver, and react-flow constructs one on mount. Any
// panel that draws a canvas therefore threw before its assertions ran, which is
// why those panels had unit coverage only up to the point they rendered. This
// is a gap in the environment, not behaviour under test: observe nothing and
// report nothing, so layout-dependent code takes its zero-size branch.
if (!("ResizeObserver" in globalThis)) {
  globalThis.ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  };
}

afterEach(() => {
  cleanup();
  localStorage.clear();
});
