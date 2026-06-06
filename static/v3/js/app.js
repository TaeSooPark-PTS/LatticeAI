/* ============================================================================
 * Lattice AI v3 — Entry point
 * Boots the shell. Views are lazy-loaded by the router (see core/routes.js).
 * ========================================================================== */

import { boot } from "./core/shell.js";

const root = document.getElementById("app");
if (root) boot(root);

// Best-effort PWA hook (silent if unsupported / not served).
if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("/sw.js").catch(() => {});
}
