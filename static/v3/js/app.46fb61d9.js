/* ============================================================================
 * Lattice AI v3 — Entry point
 * Boots the shell. Views are lazy-loaded by the router (see core/routes.js).
 * ========================================================================== */

import { boot } from "./core/shell.1b6199d6.js";

const root = document.getElementById("app");
if (root) boot(root);

// CDN fonts/icons are progressive enhancement. If Tabler's webfont is blocked
// or offline, compact text fallbacks keep icon-only controls identifiable.
if (document.fonts && document.fonts.ready) {
  document.fonts.ready.then(() => {
    if (!document.fonts.check('16px "tabler-icons"')) {
      document.documentElement.dataset.ltIcons = "fallback";
    }
  }).catch(() => { document.documentElement.dataset.ltIcons = "fallback"; });
} else {
  document.documentElement.dataset.ltIcons = "fallback";
}

// Best-effort PWA hook (silent if unsupported / not served).
if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("/sw.js").catch(() => {});
}
