/* ============================================================================
 * Lattice AI v3 — Hash router
 * Maps location.hash → { key, params }. Hash routing keeps the SPA shell
 * served by a single static route (/app) with no server-side rewrites.
 *   #/home                  → { key: "home" }
 *   #/admin/users           → { key: "admin/users" }
 *   #/chat?new=1            → { key: "chat", params: { new: "1" } }
 * ========================================================================== */

export function createRouter({ onRoute, fallback = "home" }) {
  function parse() {
    let hash = location.hash.replace(/^#\/?/, "");
    let query = "";
    const qi = hash.indexOf("?");
    if (qi >= 0) { query = hash.slice(qi + 1); hash = hash.slice(0, qi); }
    const key = hash.replace(/\/+$/, "") || fallback;
    const params = {};
    if (query) new URLSearchParams(query).forEach((v, k) => { params[k] = v; });
    return { key, params };
  }

  function handle() { onRoute(parse()); }

  return {
    start() {
      window.addEventListener("hashchange", handle);
      if (!location.hash) { location.replace("#/" + fallback); }
      else { handle(); }
    },
    current: parse,
    navigate(key, params) {
      const qs = params && Object.keys(params).length ? "?" + new URLSearchParams(params).toString() : "";
      const next = "#/" + String(key).replace(/^#?\/?/, "") + qs;
      if (location.hash === next) handle(); else location.hash = next;
    },
  };
}
