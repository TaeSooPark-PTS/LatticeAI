// Standalone light/dark token-cascade verification (not part of CI).
// Loads each page via the mock server, forces light then dark, and asserts
// the computed background/text colors flip (proving tokens drive theming),
// with no page-level CSS/JS errors.
const { chromium } = require("playwright");

const PORT = process.env.LTCAI_VISUAL_PORT || 4955;
const BASE = `http://127.0.0.1:${PORT}`;
const PAGES = [
  ["chat", "/static/chat.html"],
  ["account", "/static/account.html"],
  ["admin", "/static/admin.html"],
  ["graph", "/static/graph.html"],
  ["workspace", "/static/workspace.html"],
  ["agents", "/static/agents.html"],
];

function lum(rgb) {
  const m = rgb && rgb.match(/(\d+),\s*(\d+),\s*(\d+)/);
  if (!m) return null;
  return 0.2126 * +m[1] + 0.7152 * +m[2] + 0.0722 * +m[3];
}

(async () => {
  const browser = await chromium.launch();
  let fail = 0;
  for (const [name, path] of PAGES) {
    const results = {};
    for (const theme of ["light", "dark"]) {
      const ctx = await browser.newContext();
      const page = await ctx.newPage();
      // Abort page-specific app scripts (chat.js/account.js redirect to /account when the
      // auth backend is absent in this mock). Keep ux.js + all CSS so we test pure theming.
      await page.route("**/scripts/**", (route) =>
        route.request().url().includes("ux.js") ? route.continue() : route.abort());
      await page.addInitScript((t) => {
        try { localStorage.setItem("lt-theme", t); } catch (e) {}
      }, theme);
      const errors = [];
      page.on("pageerror", (e) => errors.push(String(e)));
      try {
        await page.goto(`${BASE}${path}`, { waitUntil: "domcontentloaded", timeout: 15000 });
        await page.waitForTimeout(350);
        const data = await page.evaluate(() => {
          const rs = getComputedStyle(document.documentElement);
          const b = getComputedStyle(document.body);
          return {
            theme: document.documentElement.getAttribute("data-lt-theme"),
            uxLoaded: typeof window.toggleTheme === "function",
            tokenBg: rs.getPropertyValue("--bg").trim(),
            tokenText: rs.getPropertyValue("--text").trim(),
            bg: b.backgroundColor,
            color: b.color,
          };
        });
        results[theme] = { ...data, errors };
      } catch (e) {
        results[theme] = { error: String(e), errors };
      }
      await ctx.close();
    }
    // assertions — use the --bg token value (gradient-proof) to confirm the flip
    const L = results.light, D = results.dark;
    const themeApplied = L.theme === "light" && D.theme === "dark";
    const flips = L.tokenBg && D.tokenBg && L.tokenBg.toLowerCase() !== D.tokenBg.toLowerCase();
    const jsErr = (L.errors || []).concat(D.errors || []).filter((e) => !/fetch|network|Failed to load|ERR_/i.test(e));
    const ok = themeApplied && flips && jsErr.length === 0;
    if (!ok) fail++;
    console.log(`${ok ? "PASS" : "FAIL"}  ${name.padEnd(10)} ux=${L.uxLoaded} theme[L=${L.theme},D=${D.theme}]  --bg[L=${L.tokenBg},D=${D.tokenBg}] flips=${flips} jsErr=${jsErr.length}`);
    if (jsErr.length) console.log("      jsErrors:", jsErr.slice(0, 3));
  }
  await browser.close();
  console.log(fail === 0 ? "\nALL THEME CHECKS PASSED" : `\n${fail} PAGE(S) FAILED`);
  process.exit(fail === 0 ? 0 : 1);
})();
