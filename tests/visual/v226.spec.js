// ============================================================================
// Lattice AI — v2.2.6 token-native CSS foundation suite
//
// v2.2.6 deletes the 7,985-line legacy lattice-reference.css and replaces it
// with token-native modules under static/css/reference/ (base/account/admin/
// graph/chat). The foggy/washed-out dark mode is fixed at SOURCE: the chat skin
// no longer redefines color tokens with light literals (it is gated to the
// light theme), every active surface is var(--token), and the loaded-last
// dark-override stack in responsive.css is gone.
//
// These tests are a CSS contract: each page's own JS is stubbed out so it can't
// redirect or rewrite the DOM — we render the REAL static shell against the REAL
// CSS + the ux.js theme runtime, then walk every visible element and assert:
//   - DARK: no opaque-light surface (no fog / washed-out panels)
//   - DARK/LIGHT: no unreadable text (WCAG contrast >= 2.2 vs effective bg)
//   - no full-page backdrop-filter blur on the page canvas
// ============================================================================
const { test, expect } = require("@playwright/test");

// Pages that load the reference modules. Each owns a body.lattice-ref-* class.
// We stub the page's own bootstrap script (like v224 does for chat.js) so the
// test is a pure CSS/markup contract and never depends on a live backend.
const PAGES = [
  { name: "account", route: "/account", stub: "**/scripts/account.js*" },
  { name: "admin", route: "/admin", stub: "**/scripts/admin.js*" },
  { name: "graph", route: "/graph", stub: "**/scripts/graph.js*" },
  { name: "chat", route: "/chat", stub: "**/scripts/chat.js*" },
];

async function openPage(page, p, { theme, viewport } = {}) {
  if (viewport) await page.setViewportSize(viewport);
  await page.route(p.stub, (r) =>
    r.fulfill({ body: "", contentType: "application/javascript" }));
  await page.goto(p.route);
  if (theme) {
    await page.evaluate((m) => window.setTheme(m), theme);
    await page.waitForTimeout(140);
  }
}

// Force every modal/overlay/panel open + inject sample chat content so floating
// surfaces (Pipeline modal, My Computer panels, account/MCP/model modals) are in
// the scan. Mirrors v224's overlay list and is a no-op on pages without them.
const OPEN_OVERLAYS = () => {
  document.body.classList.add("sidebar-open", "modal-open");
  [".acct-modal-overlay", ".mcp-modal-overlay", ".mode-modal-overlay",
   ".workspace-modal-overlay", ".advanced-settings-overlay", ".model-overlay",
   ".onboarding-overlay", ".status-overlay", ".file-create-overlay",
   ".cu-overlay", ".local-browser-overlay", ".perm-overlay", ".admin-overlay",
   "#setup-overlay"].forEach((s) => {
    document.querySelectorAll(s).forEach((e) => {
      e.classList.add("open"); e.setAttribute("aria-hidden", "false");
      e.style.display = "flex"; e.style.pointerEvents = "auto";
    });
  });
  const vp = document.querySelector("#chat-viewport") || document.querySelector(".main-chat");
  if (vp && vp.id === "chat-viewport") vp.innerHTML =
    '<div class="message user"><div class="bubble">사용자 메시지 예시</div></div>' +
    '<div class="message assistant ai"><div class="bubble">어시스턴트 응답 예시입니다.</div></div>';
};

// The walker. mode = "dark" | "light". Returns { fog, contrast }.
const SCAN = (mode) => {
  const parse = (s) => {
    const m = String(s).match(/[\d.]+/g);
    if (!m) return null;
    const [r, g, b, a = 1] = m.map(Number);
    return { r, g, b, a };
  };
  const lum255 = (c) => 0.2126 * c.r + 0.7152 * c.g + 0.0722 * c.b;
  const relLum = (c) => {
    const f = (v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); };
    return 0.2126 * f(c.r) + 0.7152 * f(c.g) + 0.0722 * f(c.b);
  };
  const contrast = (a, b) => {
    const la = relLum(a), lb = relLum(b);
    return (Math.max(la, lb) + 0.05) / (Math.min(la, lb) + 0.05);
  };
  const pageBg = mode === "dark" ? { r: 11, g: 11, b: 30, a: 1 } : { r: 243, g: 236, b: 255, a: 1 };
  // Effective (opaque) background behind an element. Returns null when a gradient
  // background-image is encountered before any opaque color — JS cannot derive a
  // single representative color for a gradient, so contrast there is indeterminate
  // (accent/decorative gradient buttons, bubbles) and we skip rather than guess.
  const effBg = (el) => {
    let e = el;
    while (e && e.nodeType === 1) {
      const cs = getComputedStyle(e);
      if (/gradient/.test(cs.backgroundImage)) return null;
      const c = parse(cs.backgroundColor);
      if (c && c.a >= 0.6) return c;
      e = e.parentElement;
    }
    return pageBg;
  };
  const lightGrad = (img) => {
    if (!img || img === "none") return false;
    const rgbs = img.match(/rgba?\([^)]+\)/g) || [];
    const hexes = img.match(/#[0-9a-fA-F]{6}/g) || [];
    const all = (arr, f) => arr.length > 0 && arr.every(f);
    const rgbL = (s) => { const c = parse(s); return c && c.a > 0.4 && lum255(c) > 185; };
    const hexL = (h) => lum255({ r: parseInt(h.slice(1, 3), 16), g: parseInt(h.slice(3, 5), 16), b: parseInt(h.slice(5, 7), 16) }) > 185;
    return all(rgbs, rgbL) || all(hexes, hexL);
  };
  const pathOf = (el) => {
    let p = el.tagName.toLowerCase();
    if (el.id) p += "#" + el.id;
    if (el.className && el.className.toString) p += "." + el.className.toString().trim().split(/\s+/).slice(0, 2).join(".");
    return p;
  };
  const hasOwnText = (el) => {
    for (const n of el.childNodes) if (n.nodeType === 3 && n.textContent.trim().length > 1) return true;
    return false;
  };
  const fog = [], lowc = [], seen = new Set();
  document.querySelectorAll("body *").forEach((el) => {
    const cs = getComputedStyle(el);
    if (cs.display === "none" || cs.visibility === "hidden" || +cs.opacity === 0) return;
    const r = el.getBoundingClientRect();
    if (r.width < 10 || r.height < 10) return;
    // 1) fog: opaque-light surface or all-light gradient in DARK mode
    if (mode === "dark") {
      const bg = parse(cs.backgroundColor);
      // A foggy surface is a near-NEUTRAL light fill (white/lavender panel). Saturated
      // light fills are brand marks (e.g. Microsoft SSO logo tiles) — not surfaces.
      const neutral = bg && (Math.max(bg.r, bg.g, bg.b) - Math.min(bg.r, bg.g, bg.b)) < 45;
      if (bg && bg.a > 0.3 && lum255(bg) > 185 && neutral) {
        const k = "fog|" + pathOf(el); if (!seen.has(k)) { seen.add(k); fog.push(pathOf(el) + " :: bg=" + cs.backgroundColor); }
      } else if (lightGrad(cs.backgroundImage) && r.width * r.height > 4000) {
        const k = "grad|" + pathOf(el); if (!seen.has(k)) { seen.add(k); fog.push(pathOf(el) + " :: grad"); }
      }
    }
    // 2) contrast: own text vs effective background must be readable
    if (hasOwnText(el)) {
      const col = parse(cs.color);
      const fill = cs.webkitTextFillColor && cs.webkitTextFillColor !== "" ? parse(cs.webkitTextFillColor) : null;
      const ink = fill || col;
      // skip clipped/transparent gradient text
      const clipped = cs.webkitBackgroundClip === "text" || cs.backgroundClip === "text" ||
        (fill && fill.a < 0.3) || (ink && ink.a < 0.3);
      const fs = parseFloat(cs.fontSize) || 0;
      const bg = ink && !clipped && fs >= 8.5 ? effBg(el) : null;
      if (bg) {
        const c = contrast(ink, bg);
        if (c < 2.2) {
          const k = "c|" + pathOf(el); if (!seen.has(k)) { seen.add(k); lowc.push(pathOf(el) + " :: " + c.toFixed(2) + " color=" + cs.color); }
        }
      }
    }
  });
  return { fog, lowc };
};

// ---------------------------------------------------------------------------
// 1. DARK: no opaque-light surface anywhere on any reference page.
// ---------------------------------------------------------------------------
for (const p of PAGES) {
  test(`[${p.name}] dark mode has no opaque-light (foggy) surface`, async ({ page }) => {
    await openPage(page, p, { theme: "dark" });
    await page.evaluate(OPEN_OVERLAYS);
    await page.waitForTimeout(120);
    const { fog } = await page.evaluate(SCAN, "dark");
    expect(fog, `foggy surfaces on ${p.name} (dark):\n` + fog.join("\n")).toEqual([]);
  });
}

// ---------------------------------------------------------------------------
// 2. DARK: no unreadable (dark-on-dark) text.
// ---------------------------------------------------------------------------
for (const p of PAGES) {
  test(`[${p.name}] dark mode text is readable (contrast >= 2.2)`, async ({ page }) => {
    await openPage(page, p, { theme: "dark" });
    await page.evaluate(OPEN_OVERLAYS);
    await page.waitForTimeout(120);
    const { lowc } = await page.evaluate(SCAN, "dark");
    expect(lowc, `low-contrast text on ${p.name} (dark):\n` + lowc.join("\n")).toEqual([]);
  });
}

// ---------------------------------------------------------------------------
// 3. LIGHT: no unreadable (light-on-light) text — light mode stays clean.
// ---------------------------------------------------------------------------
for (const p of PAGES) {
  test(`[${p.name}] light mode text is readable (contrast >= 2.2)`, async ({ page }) => {
    await openPage(page, p, { theme: "light" });
    await page.evaluate(OPEN_OVERLAYS);
    await page.waitForTimeout(120);
    const { lowc } = await page.evaluate(SCAN, "light");
    expect(lowc, `low-contrast text on ${p.name} (light):\n` + lowc.join("\n")).toEqual([]);
  });
}

// ---------------------------------------------------------------------------
// 4. Mobile (390x844) dark: chat + admin have no foggy surface either.
// ---------------------------------------------------------------------------
for (const p of PAGES.filter((p) => p.name === "chat" || p.name === "admin")) {
  test(`[${p.name}] mobile dark has no foggy surface`, async ({ page }) => {
    await openPage(page, p, { theme: "dark", viewport: { width: 390, height: 844 } });
    await page.evaluate(OPEN_OVERLAYS);
    await page.waitForTimeout(120);
    const { fog } = await page.evaluate(SCAN, "dark");
    expect(fog, `foggy surfaces on ${p.name} (mobile dark):\n` + fog.join("\n")).toEqual([]);
  });
}

// ---------------------------------------------------------------------------
// 5. No full-page backdrop-filter blur on the page canvas in either theme.
// ---------------------------------------------------------------------------
for (const p of PAGES) {
  test(`[${p.name}] page canvas has no full-page backdrop blur`, async ({ page }) => {
    await openPage(page, p, { theme: "dark" });
    const blurred = await page.evaluate(() => {
      const bad = [];
      for (const sel of ["body", ".app-layout", ".main-chat", ".app", ".page", ".bg-grid"]) {
        document.querySelectorAll(sel).forEach((el) => {
          const cs = getComputedStyle(el);
          const bf = cs.backdropFilter || cs.webkitBackdropFilter || "";
          if (/blur\(\s*[1-9]/.test(bf)) bad.push(sel + " :: " + bf);
        });
      }
      return bad;
    });
    expect(blurred, `full-page blur on ${p.name}:\n` + blurred.join("\n")).toEqual([]);
  });
}
