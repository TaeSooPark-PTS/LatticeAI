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

function parseHashOf(value: string) {
  setHash(value);
  return parseHash();
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

  it("lands the Work entry point on the review inbox, not the goal composer", () => {
    // The Work screen leads with what is waiting on a decision. The nav href
    // has to agree: pointing it at `agents` would reorder the tab strip while
    // still opening the screen on the panel that got demoted.
    const work = productShellRoutes.find((route) => route.id === "act");
    setHash(`#/${work?.path}`);
    expect(parseHash()).toMatchObject({ primary: "act", tab: "review" });
  });

  it("opens the chronicle from its own path and from the name it was designed under", () => {
    // The screen has one view, so it names no tab and the page picks its own
    // starting day. `timeline` is the word the design document uses; keeping it
    // resolvable costs one alias and saves a dead bookmark.
    expect(parseHashOf("#/chronicle").primary).toBe("chronicle");
    expect(parseHashOf("#/chronicle").tab).toBeUndefined();
    expect(parseHashOf("#/timeline").primary).toBe("chronicle");
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
  it("exposes seven primary shell routes", () => {
    // Six through 11.2.0; 연대기 is the seventh and the fourth everyday one.
    expect(productShellRoutes).toHaveLength(7);
    expect(productShellRoutes.map((route) => route.id)).toContain("chronicle");
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
    // "Resolvable" was too weak to catch anything: parseHash never returns a
    // falsy primary, it falls back to `brain`. Every entry named a real screen
    // and three of them silently landed on the Brain home instead. Assert the
    // destination is the one the entry names.
    const expected: Record<string, string> = {
      "page-brain": "brain",
      "page-capture": "capture",
      "page-memory": "brain",
      "page-chronicle": "chronicle",
      "page-library": "library",
      "page-act": "act",
      "page-review": "act",
      "page-system": "system",
    };
    for (const entry of commandRoutes) {
      setHash(`#${entry.target}`);
      expect(parseHash().primary, entry.target).toBe(expected[entry.id]);
    }
  });

  it("sends both palette entries for Work to the review inbox the shell opens", () => {
    // The shell's "작업" link and the palette's carry the same label, so they
    // have to reach the same panel. They were re-pointed in two different
    // lists, only one of which the palette read.
    for (const id of ["page-act", "page-review"]) {
      setHash(`#${commandRoutes.find((entry) => entry.id === id)?.target}`);
      expect(parseHash(), id).toMatchObject({ primary: "act", tab: "review" });
    }
  });

  it("resolves a <primary>/<tab> path instead of dropping it on the home screen", () => {
    // The palette and the daily briefing emit this shape. Nothing parsed it, so
    // `#/act/review` — the one destination this layout promotes — rendered the
    // Brain home.
    expect(parseHashOf("#/act/review")).toMatchObject({ primary: "act", tab: "review" });
    expect(parseHashOf("#/act/workflows")).toMatchObject({ primary: "act", tab: "workflows" });
    expect(parseHashOf("#/brain/graph")).toMatchObject({ primary: "brain", tab: "graph" });
  });

  it("lets a named alias win over the generic <primary>/<tab> form", () => {
    // `admin/audit` is an alias onto the system screen. Reading it as
    // primary=admin/tab=audit would break every old admin bookmark, so the
    // generic branch has to run last.
    expect(parseHashOf("#/admin/audit")).toMatchObject({ primary: "system", tab: "admin" });
    // A head that is not a primary route still falls back rather than throwing.
    expect(parseHashOf("#/nonsense/deeper")).toMatchObject({ primary: "brain", tab: undefined });
  });

  it("has no alias that shadows a direct route with a different target", () => {
    for (const [name, direct] of Object.entries(directProductRoutes)) {
      const alias = compatibilityRouteAliases[name];
      if (!alias) continue;
      expect(alias, `${name} resolves two ways`).toEqual(direct);
    }
  });
});
