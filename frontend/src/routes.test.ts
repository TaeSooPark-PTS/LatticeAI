/**
 * Routing is the one module every surface depends on and nothing tested.
 *
 * `parseHash` decides which page a URL lands on, including the compatibility
 * aliases that keep old bookmarks (`#/hybrid-search`, `#/admin/audit`) working
 * after the v3 shell renamed the primary routes. A silent regression here does
 * not throw — it just quietly sends someone to the wrong page.
 */

import { beforeEach, describe, expect, it } from "vitest";

import {
  commandRoutes,
  compatibilityRouteAliases,
  directProductRoutes,
  go,
  parseHash,
  primaryRoutes,
  productShellRoutes,
  routeAliases,
} from "./routes";

function setHash(value: string) {
  window.location.hash = value;
}

beforeEach(() => {
  setHash("");
});

describe("parseHash", () => {
  it("defaults to the Brain when there is no hash", () => {
    expect(parseHash()).toEqual({ primary: "brain", tab: "conversation", path: "brain" });
  });

  it.each([
    ["#/brain", "brain", "conversation"],
    ["#/capture", "capture", "files"],
    ["#/knowledge-graph", "brain", "graph"],
    ["#/models", "library", "models"],
    ["#/settings", "system", "settings"],
    ["#/review", "act", "review"],
  ])("routes the direct path %s", (hash, primary, tab) => {
    setHash(hash);
    expect(parseHash()).toMatchObject({ primary, tab });
  });

  it.each([
    ["#/hybrid-search", "brain", "knowledge"],
    ["#/chat", "brain", "conversation"],
    ["#/runs", "act", "runs"],
    ["#/skills", "library", "skills"],
    ["#/snapshots", "system", "snapshots"],
    ["#/admin/audit", "system", "admin"],
    ["#/admin/private-vpc", "system", "admin"],
  ])("keeps the compatibility alias %s working", (hash, primary, tab) => {
    setHash(hash);
    expect(parseHash()).toMatchObject({ primary, tab });
  });

  it("resolves a primary route id with no tab of its own", () => {
    setHash("#/memory");
    // `memory` is both a shell route and an alias; the shell route wins and
    // leaves the tab unset so the page picks its own default.
    expect(parseHash()).toMatchObject({ primary: "memory", tab: undefined });
  });

  it("falls back to the Brain for an unknown path rather than rendering nothing", () => {
    setHash("#/this-route-does-not-exist");
    expect(parseHash()).toMatchObject({ primary: "brain", tab: undefined });
  });

  it("strips a query string before matching", () => {
    setHash("#/models?engine=ollama");
    const parsed = parseHash();
    expect(parsed).toMatchObject({ primary: "library", tab: "models" });
    // The raw path is preserved so a page can still read its own query.
    expect(parsed.path).toBe("models?engine=ollama");
  });

  it.each(["#models", "#/models", "#//models", "#///models"])(
    "tolerates leading slash variations in %s",
    (hash) => {
      setHash(hash);
      expect(parseHash()).toMatchObject({ primary: "library", tab: "models" });
    },
  );
});

describe("go", () => {
  it("writes a normalised hash", () => {
    go("capture");
    expect(window.location.hash).toBe("#/capture");
  });

  it("does not double the leading slash", () => {
    go("/settings");
    expect(window.location.hash).toBe("#/settings");
    go("///settings");
    expect(window.location.hash).toBe("#/settings");
  });

  it("round-trips through parseHash", () => {
    go("agents");
    expect(parseHash()).toMatchObject({ primary: "act", tab: "agents" });
  });
});

describe("route tables", () => {
  it("exposes six primary shell routes", () => {
    expect(productShellRoutes).toHaveLength(6);
    expect(primaryRoutes).toBe(productShellRoutes);
    expect(routeAliases).toBe(compatibilityRouteAliases);
  });

  it("gives every shell route a label key and an icon", () => {
    for (const route of productShellRoutes) {
      expect(route.labelKey).toMatch(/^shell\.route\./);
      expect(route.icon).toBeTruthy();
      expect(route.description.length).toBeGreaterThan(0);
    }
  });

  it("only ever points aliases at a real primary route", () => {
    const primaries = new Set(productShellRoutes.map((route) => route.id));
    for (const [name, target] of Object.entries({
      ...directProductRoutes,
      ...compatibilityRouteAliases,
    })) {
      expect(primaries.has(target.primary), `${name} → ${target.primary}`).toBe(true);
    }
  });

  it("keeps command palette entries resolvable by parseHash", () => {
    for (const entry of commandRoutes) {
      setHash(`#/${entry.key}`);
      expect(parseHash().primary, entry.key).toBeTruthy();
    }
  });

  it("has no alias that shadows a direct route with a different target", () => {
    for (const [name, direct] of Object.entries(directProductRoutes)) {
      const alias = compatibilityRouteAliases[name];
      if (!alias) continue;
      expect(alias, `${name} resolves two ways`).toEqual(direct);
    }
  });
});
