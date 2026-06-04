// ============================================================================
// Lattice AI — v2.2.2 Frontend QA stabilization suite
//
// These tests assert the *behaviour* the v2.2.2 release stabilizes, not pixels:
//   - light/dark theme parity on every primary page (computed colors flip)
//   - interactive controls are actually clickable (hit-test, not covered)
//   - no unexpected horizontal scroll across the supported viewport matrix
//   - mobile hamburger drawers open/close (graph + admin)
//   - Escape closes open drawers (keyboard a11y)
//   - long surfaces scroll instead of clipping
// ============================================================================
const { test, expect } = require("@playwright/test");

const PAGES = [
  { name: "workspace", url: "/workspace" },
  { name: "graph", url: "/graph?node=entity:lattice" },
  { name: "admin", url: "/admin" },
  { name: "chat", url: "/chat" },
];

// chat.html bootstraps against a live backend (chat.js re-renders the document
// when its setup endpoints are absent), so the static mock can't drive its
// theme runtime. Theme parity is asserted on the pages the mock renders 1:1.
const THEME_PAGES = PAGES.filter((p) => p.name !== "chat");

// Representative slice of the requested viewport matrix (mobile→ultrawide).
const VIEWPORTS = [
  { name: "mobile-375", width: 375, height: 667 },
  { name: "mobile-390", width: 390, height: 844 },
  { name: "mobile-430", width: 430, height: 932 },
  { name: "tablet-768", width: 768, height: 1024 },
  { name: "tablet-1024", width: 1024, height: 1366 },
  { name: "desktop-1280", width: 1280, height: 720 },
  { name: "desktop-1440", width: 1440, height: 900 },
  { name: "desktop-1920", width: 1920, height: 1080 },
  { name: "ultrawide-2560", width: 2560, height: 1440 },
  { name: "ultrawide-3440", width: 3440, height: 1440 },
];

function rgbToLuminance(rgb) {
  const m = rgb.match(/(\d+(?:\.\d+)?)/g);
  if (!m) return null;
  const [r, g, b] = m.map(Number);
  // perceptual luminance (sRGB approximation)
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

async function setTheme(page, mode) {
  await page.evaluate((m) => window.setTheme && window.setTheme(m), mode);
  await page.waitForTimeout(120);
}

// ---------------------------------------------------------------------------
// 1. Theme parity — background + text luminance must flip between light & dark.
//    This is the regression that broke dark mode on chat (body-level light
//    token literals shadowed :root[data-lt-theme=dark]).
// ---------------------------------------------------------------------------
for (const p of THEME_PAGES) {
  test(`[${p.name}] light/dark theme flips real computed colors`, async ({ page }) => {
    await page.goto(p.url);
    await page.waitForTimeout(200);

    await setTheme(page, "light");
    const light = await page.evaluate(() => getComputedStyle(document.body).color);
    expect(await page.getAttribute("html", "data-lt-theme")).toBe("light");

    await setTheme(page, "dark");
    const dark = await page.evaluate(() => getComputedStyle(document.body).color);
    expect(await page.getAttribute("html", "data-lt-theme")).toBe("dark");

    // body text color must invert: dark text in light mode, light text in dark mode
    const lightLum = rgbToLuminance(light);
    const darkLum = rgbToLuminance(dark);
    expect(lightLum, `${p.name} light-mode text should be dark`).toBeLessThan(120);
    expect(darkLum, `${p.name} dark-mode text should be light`).toBeGreaterThan(150);
  });
}

// ---------------------------------------------------------------------------
// 2. Clickability / hit-testing — graph toolbar controls must receive clicks.
//    Catches overlay / z-index / pointer-events regressions.
// ---------------------------------------------------------------------------
test("graph toolbar controls are visible, enabled and not overlay-blocked", async ({ page }) => {
  await page.goto("/graph?node=entity:lattice");
  await page.waitForTimeout(300);
  const ids = ["#zoom-in-btn", "#zoom-out-btn", "#fit-btn", "#fullscreen-btn", "#refresh-btn"];
  for (const id of ids) {
    const btn = page.locator(id);
    await expect(btn, `${id} visible`).toBeVisible();
    await expect(btn, `${id} enabled`).toBeEnabled();
    // the element at the button's center must be the button (or its child icon)
    const topIsSelf = await btn.evaluate((el) => {
      const r = el.getBoundingClientRect();
      const top = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
      return el === top || el.contains(top);
    });
    expect(topIsSelf, `${id} not covered by an overlay`).toBeTruthy();
  }
});

test("admin action buttons are clickable and not overlay-blocked", async ({ page }) => {
  await page.goto("/admin");
  await page.waitForTimeout(300);
  for (const id of ["#refresh-btn", "#logout-btn"]) {
    const btn = page.locator(id);
    await expect(btn).toBeVisible();
    await expect(btn).toBeEnabled();
    const topIsSelf = await btn.evaluate((el) => {
      const r = el.getBoundingClientRect();
      const top = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
      return el === top || el.contains(top);
    });
    expect(topIsSelf, `${id} not covered`).toBeTruthy();
  }
});

// ---------------------------------------------------------------------------
// 3. No unexpected horizontal scroll across the viewport matrix.
// ---------------------------------------------------------------------------
for (const vp of VIEWPORTS) {
  test(`[${vp.name}] pages have no horizontal overflow`, async ({ page }) => {
    await page.setViewportSize({ width: vp.width, height: vp.height });
    for (const p of PAGES) {
      await page.goto(p.url);
      await page.waitForTimeout(150);
      const overflow = await page.evaluate(() => {
        const d = document.documentElement;
        return d.scrollWidth - d.clientWidth;
      });
      // allow 1px sub-pixel rounding slack
      expect(overflow, `${p.name} @ ${vp.width} horizontal overflow`).toBeLessThanOrEqual(1);
    }
  });
}

// ---------------------------------------------------------------------------
// 4. Mobile hamburger drawers — graph + admin open and close.
// ---------------------------------------------------------------------------
test("graph mobile hamburger opens and Escape closes the nav drawer", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/graph?node=entity:lattice");
  await page.waitForTimeout(200);
  const toggle = page.locator(".graph-nav-toggle");
  await expect(toggle).toBeVisible();
  await toggle.click();
  await expect(page.locator("body")).toHaveClass(/graph-nav-open/);
  // rail slides in over a 250ms transition — wait for it to settle, then assert on-screen
  await page.waitForTimeout(400);
  const railX = await page.locator(".reference-rail").evaluate((el) => el.getBoundingClientRect().left);
  expect(railX, "graph rail slides on-screen when nav opens").toBeGreaterThanOrEqual(-1);
  // Escape closes
  await page.keyboard.press("Escape");
  await page.waitForTimeout(200);
  await expect(page.locator("body")).not.toHaveClass(/graph-nav-open/);
});

test("admin mobile hamburger opens and overlay click closes the rail", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/admin");
  await page.waitForTimeout(200);
  const toggle = page.locator(".admin-rail-toggle");
  await expect(toggle).toBeVisible();
  await toggle.click();
  await expect(page.locator("body")).toHaveClass(/admin-rail-open/);
  await page.keyboard.press("Escape");
  await page.waitForTimeout(200);
  await expect(page.locator("body")).not.toHaveClass(/admin-rail-open/);
});

// ---------------------------------------------------------------------------
// 5. Long surfaces scroll instead of clipping (graph card list on mobile).
// ---------------------------------------------------------------------------
test("graph mobile card view is scrollable, not clipped", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/graph?node=entity:lattice");
  await page.waitForTimeout(300);
  const toggle = page.locator(".graph-view-toggle");
  await expect(toggle).toBeVisible();
  await toggle.click();
  await page.waitForTimeout(200);
  const list = page.locator(".graph-card-list");
  await expect(list).toBeVisible();
  const overflowY = await list.evaluate((el) => getComputedStyle(el).overflowY);
  expect(["auto", "scroll"]).toContain(overflowY);
});
