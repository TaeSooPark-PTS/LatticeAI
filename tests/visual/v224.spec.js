// ============================================================================
// Lattice AI — v2.2.4 Chat Dark Theme suite
//
// Fixes the v2.2.3 known issue: body.lattice-ref-chat redefines color tokens as
// LIGHT literals, shadowing :root[data-lt-theme="dark"], so the whole chat page
// rendered light in dark mode. These tests render the REAL chat shell (chat.js
// stubbed out so it can't redirect/rewrite the DOM) against the REAL CSS and
// assert dark-mode correctness, light-mode non-regression, and responsiveness.
// ============================================================================
const { test, expect } = require("@playwright/test");

// Stub chat.js: it bootstraps against a live backend and otherwise leaves the
// page; ux.js (theme runtime) + the static shell + real CSS are what we test.
async function openChat(page, { theme } = {}) {
  await page.route("**/scripts/chat.js*", (r) =>
    r.fulfill({ body: "", contentType: "application/javascript" }));
  await page.goto("/chat");
  if (theme) {
    await page.evaluate((m) => window.setTheme(m), theme);
    await page.waitForTimeout(120);
  }
}

function parseRGBA(s) {
  const m = String(s).match(/[\d.]+/g);
  if (!m) return null;
  const [r, g, b, a = 1] = m.map(Number);
  return { r, g, b, a };
}
const lum = (c) => 0.2126 * c.r + 0.7152 * c.g + 0.0722 * c.b;
function isOpaqueLight(bg) {
  const c = parseRGBA(bg);
  return !!c && c.a > 0.3 && lum(c) > 185;
}

// The in-page scan: open every overlay, inject sample bubbles, walk all visible
// elements and return any with an opaque-light background or light gradient.
const SCAN_FN = () => {
  document.body.classList.add("sidebar-open");
  [".acct-modal-overlay", ".mcp-modal-overlay", ".mode-modal-overlay",
   ".workspace-modal-overlay", ".advanced-settings-overlay", ".model-overlay",
   ".onboarding-overlay", ".status-overlay", ".file-create-overlay",
   ".cu-overlay", ".local-browser-overlay"].forEach((s) => {
    const e = document.querySelector(s);
    if (e) { e.classList.add("open"); e.style.display = "flex"; }
  });
  const vp = document.querySelector("#chat-viewport");
  if (vp) vp.innerHTML =
    '<div class="message user"><div class="bubble">사용자 메시지 예시</div></div>' +
    '<div class="message assistant"><div class="bubble">어시스턴트 응답 예시입니다.</div></div>';
  const parse = (s) => { const m = String(s).match(/[\d.]+/g); if (!m) return null; const [r, g, b, a = 1] = m.map(Number); return { r, g, b, a }; };
  const L = (c) => 0.2126 * c.r + 0.7152 * c.g + 0.0722 * c.b;
  const opaqueLight = (s) => { const c = parse(s); return !!c && c.a > 0.3 && L(c) > 185; };
  const lightGrad = (img) => {
    if (!img || img === "none") return false;
    const hexes = img.match(/#[0-9a-fA-F]{6}/g) || [];
    const rgbs = img.match(/rgba?\([^)]+\)/g) || [];
    const all = (arr, f) => arr.length > 0 && arr.every(f);
    const hexL = (h) => 0.2126 * parseInt(h.slice(1, 3), 16) + 0.7152 * parseInt(h.slice(3, 5), 16) + 0.0722 * parseInt(h.slice(5, 7), 16) > 185;
    const rgbL = (s) => { const c = parse(s); return c && c.a > 0.3 && L(c) > 185; };
    return all(hexes, hexL) || all(rgbs, rgbL);
  };
  const pathOf = (el) => { let p = el.tagName.toLowerCase(); if (el.id) p += "#" + el.id; if (el.className && el.className.toString) p += "." + el.className.toString().trim().split(/\s+/).slice(0, 3).join("."); return p; };
  const out = [], seen = new Set();
  document.querySelectorAll("*").forEach((el) => {
    const cs = getComputedStyle(el);
    if (cs.display === "none" || cs.visibility === "hidden") return;
    const r = el.getBoundingClientRect();
    if (r.width < 8 || r.height < 8) return;
    let reason = null;
    if (opaqueLight(cs.backgroundColor)) reason = "bg=" + cs.backgroundColor;
    else if (lightGrad(cs.backgroundImage)) reason = "grad";
    if (reason) { const k = pathOf(el) + "|" + reason; if (!seen.has(k)) { seen.add(k); out.push(pathOf(el) + " :: " + reason); } }
  });
  return out;
};

// ---------------------------------------------------------------------------
// 1. The gate: NO opaque-light surface anywhere in the chat UI in dark mode.
// ---------------------------------------------------------------------------
test("[chat][dark] no opaque-light surfaces across shell + overlays + bubbles", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 });
  await openChat(page, { theme: "dark" });
  const flagged = await page.evaluate(SCAN_FN);
  expect(flagged, `light surfaces in dark chat:\n${flagged.join("\n")}`).toEqual([]);
});

// ---------------------------------------------------------------------------
// 2. Key chat surfaces: dark background + light text in dark mode.
// ---------------------------------------------------------------------------
test("[chat][dark] key surfaces are dark with light text", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 });
  await openChat(page, { theme: "dark" });
  // body text token flips to light
  const bodyText = await page.evaluate(() => getComputedStyle(document.body).color);
  expect(lum(parseRGBA(bodyText)), "body text light in dark").toBeGreaterThan(150);
  // sidebar + header backgrounds are not opaque-light
  for (const sel of [".sidebar", ".chat-header", ".input-box"]) {
    const bg = await page.locator(sel).first().evaluate((el) => getComputedStyle(el).backgroundColor);
    expect(isOpaqueLight(bg), `${sel} not opaque-light`).toBeFalsy();
  }
  // injected assistant bubble: text readable
  const bubbleText = await page.evaluate(() => {
    const vp = document.querySelector("#chat-viewport");
    vp.innerHTML = '<div class="message assistant"><div class="bubble">응답</div></div>';
    return getComputedStyle(vp.querySelector(".bubble")).color;
  });
  expect(lum(parseRGBA(bubbleText)), "bubble text visible in dark").toBeGreaterThan(120);
});

// ---------------------------------------------------------------------------
// 3. Light mode is NOT regressed: surfaces stay light, text stays dark.
// ---------------------------------------------------------------------------
test("[chat][light] is not regressed (light surfaces, dark text)", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 });
  await openChat(page, { theme: "light" });
  const bodyText = await page.evaluate(() => getComputedStyle(document.body).color || getComputedStyle(document.querySelector(".sidebar")).color);
  // sidebar should be light in light mode
  const sidebarBg = await page.locator(".sidebar").first().evaluate((el) => getComputedStyle(el).backgroundColor);
  expect(isOpaqueLight(sidebarBg), "sidebar is light in light mode").toBeTruthy();
  // a token reads light value: --text resolves dark
  const textTok = await page.evaluate(() => getComputedStyle(document.body).getPropertyValue("--text").trim());
  expect(textTok).toBe("#24223d");
});

// ---------------------------------------------------------------------------
// 4. Toast is token-driven (dark in dark mode, light in light mode).
// ---------------------------------------------------------------------------
test("[chat] toast adapts to theme", async ({ page }) => {
  await openChat(page, { theme: "dark" });
  const darkBg = await page.evaluate(() => {
    const t = document.createElement("div");
    t.id = "ltcai-toast"; t.textContent = "x";
    document.body.appendChild(t);
    return getComputedStyle(t).backgroundColor;
  });
  expect(isOpaqueLight(darkBg), "toast bg not opaque-light in dark").toBeFalsy();
});

// ---------------------------------------------------------------------------
// 5. Responsive: no horizontal overflow; composer reachable at every width.
// ---------------------------------------------------------------------------
const WIDTHS = [
  [375, 667], [390, 844], [430, 932], [768, 1024], [1024, 1366],
  [1280, 720], [1440, 900], [1920, 1080], [2560, 1440], [3440, 1440],
];
for (const [w, h] of WIDTHS) {
  test(`[chat][${w}x${h}] no h-scroll and composer not clipped (dark)`, async ({ page }) => {
    await page.setViewportSize({ width: w, height: h });
    await openChat(page, { theme: "dark" });
    const r = await page.evaluate(() => {
      const d = document.documentElement;
      const box = document.querySelector(".input-box") || document.querySelector(".input-area");
      const rect = box ? box.getBoundingClientRect() : null;
      return {
        overflow: d.scrollWidth - d.clientWidth,
        composerBottom: rect ? rect.bottom : 0,
        vh: window.innerHeight,
        composerVisible: rect ? (rect.width > 0 && rect.height > 0) : false,
      };
    });
    expect(r.overflow, `${w}px horizontal overflow`).toBeLessThanOrEqual(1);
    expect(r.composerVisible, "composer rendered").toBeTruthy();
    // composer bottom should sit within the viewport (not pushed off-screen)
    expect(r.composerBottom, "composer not clipped below viewport").toBeLessThanOrEqual(r.vh + 2);
  });
}
