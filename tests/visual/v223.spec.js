// ============================================================================
// Lattice AI — v2.2.3 Frontend Stability & UX Fix suite
//
// Covers the v2.2.3 regressions and quality gates:
//   - login (account.html) input readability in light AND dark (no white-on-white)
//   - recommendation screen scrolls to the bottom (no clipped content)
//   - recommendation model accordions (Gemma 4 / Qwen3-VL / Llama 4) toggle on click
//   - recommendation action buttons are reachable & clickable
//   - onboarding/recommendation dark-mode readability (dark surfaces, light text)
//   - no uncaught page errors on the primary renderable pages
// ============================================================================
const { test, expect } = require("@playwright/test");

function parseRGBA(s) {
  const m = String(s).match(/[\d.]+/g);
  if (!m) return null;
  const [r, g, b, a = 1] = m.map(Number);
  return { r, g, b, a };
}
function luminance(c) {
  return 0.2126 * c.r + 0.7152 * c.g + 0.0722 * c.b; // 0..255
}
// A near-white background with meaningful opacity — the "white-on-white" regression.
function isOpaqueLight(bg) {
  const c = parseRGBA(bg);
  if (!c) return false;
  return c.a > 0.3 && luminance(c) > 180;
}
async function setTheme(page, mode) {
  await page.evaluate((m) => window.setTheme && window.setTheme(m), mode);
  await page.waitForTimeout(120);
}

// ---------------------------------------------------------------------------
// 1. LOGIN input readability — email + password, light AND dark.
// ---------------------------------------------------------------------------
for (const field of [
  { id: "#login-email", name: "email" },
  { id: "#login-pw", name: "password" },
]) {
  test(`[login] ${field.name} input is readable in light & dark`, async ({ page }) => {
    await page.goto("/account");
    await page.waitForTimeout(200);

    // --- LIGHT ---
    await setTheme(page, "light");
    const light = await page.locator(field.id).evaluate((el) => {
      const cs = getComputedStyle(el);
      const fieldCs = getComputedStyle(el.closest(".auth-field") || el.parentElement);
      const ph = getComputedStyle(el, "::placeholder");
      return { text: cs.color, field: fieldCs.backgroundColor, placeholder: ph.color };
    });
    expect(luminance(parseRGBA(light.text)), `${field.name} light text should be dark`).toBeLessThan(110);
    expect(isOpaqueLight(light.field), `${field.name} light field bg is light (expected)`).toBeTruthy();

    // --- DARK --- the regression: white text on a white field.
    await setTheme(page, "dark");
    const dark = await page.locator(field.id).evaluate((el) => {
      const cs = getComputedStyle(el);
      const fieldCs = getComputedStyle(el.closest(".auth-field") || el.parentElement);
      const ph = getComputedStyle(el, "::placeholder");
      return { text: cs.color, field: fieldCs.backgroundColor, placeholder: ph.color };
    });
    expect(luminance(parseRGBA(dark.text)), `${field.name} dark text should be light`).toBeGreaterThan(170);
    // The field background must NOT be an opaque light color in dark mode.
    expect(isOpaqueLight(dark.field), `${field.name} dark field bg must not be opaque-light (white-on-white)`).toBeFalsy();
    // Placeholder must be visible (not equal to a near-white opaque field).
    expect(parseRGBA(dark.placeholder).a, `${field.name} dark placeholder visible`).toBeGreaterThan(0.3);
  });
}

test("[login] subtitle is readable in dark mode", async ({ page }) => {
  await page.goto("/account");
  await setTheme(page, "dark");
  const lum = await page.locator(".subtitle").first().evaluate((el) => {
    const c = getComputedStyle(el).color.match(/[\d.]+/g).map(Number);
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2];
  });
  expect(lum, "dark subtitle should be light text").toBeGreaterThan(150);
});

test("[login] log-in button is clickable and not overlay-blocked", async ({ page }) => {
  await page.goto("/account");
  await page.waitForTimeout(200);
  const btn = page.locator("#login-btn");
  await expect(btn).toBeVisible();
  await expect(btn).toBeEnabled();
  const topIsSelf = await btn.evaluate((el) => {
    const r = el.getBoundingClientRect();
    const top = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
    return el === top || el.contains(top) || (top && top.contains(el));
  });
  expect(topIsSelf, "login button not covered").toBeTruthy();
});

// ---------------------------------------------------------------------------
// 2. RECOMMENDATION screen — scroll + accordions + reachable actions.
// ---------------------------------------------------------------------------
test("recommendation body scrolls to the bottom (no clipped content)", async ({ page }) => {
  await page.setViewportSize({ width: 1024, height: 700 });
  await page.goto("/onboarding-fixture");
  await page.waitForTimeout(200);
  const body = page.locator("#onboarding-body");
  const metrics = await body.evaluate((el) => ({
    overflowY: getComputedStyle(el).overflowY,
    scrollH: el.scrollHeight,
    clientH: el.clientHeight,
  }));
  expect(["auto", "scroll"]).toContain(metrics.overflowY);
  // content must exceed the viewport (otherwise the test isn't exercising scroll)
  expect(metrics.scrollH, "content taller than body").toBeGreaterThan(metrics.clientH);
  // scroll to the bottom and confirm the last family is reachable
  const reachedBottom = await body.evaluate((el) => {
    el.scrollTop = el.scrollHeight;
    return el.scrollTop > 0 && Math.abs(el.scrollHeight - el.clientHeight - el.scrollTop) < 4;
  });
  expect(reachedBottom, "body scrolls fully to bottom").toBeTruthy();
});

test("recommendation action buttons are reachable and clickable", async ({ page }) => {
  await page.setViewportSize({ width: 1024, height: 700 });
  await page.goto("/onboarding-fixture");
  await page.waitForTimeout(200);
  for (const id of ["#custom-btn", "#recommended-btn"]) {
    const btn = page.locator(id);
    await expect(btn, `${id} visible`).toBeVisible();
    const ok = await btn.evaluate((el) => {
      const r = el.getBoundingClientRect();
      const top = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
      return el === top || el.contains(top);
    });
    expect(ok, `${id} reachable / not covered`).toBeTruthy();
  }
});

for (const fam of [
  { id: "#fam-gemma", name: "Gemma 4" },
  { id: "#fam-qwen", name: "Qwen3-VL" },
  { id: "#fam-llama", name: "Llama 4" },
]) {
  test(`recommendation accordion toggles: ${fam.name}`, async ({ page }) => {
    await page.setViewportSize({ width: 1024, height: 700 });
    await page.goto("/onboarding-fixture");
    await page.waitForTimeout(150);
    const det = page.locator(fam.id);
    await det.scrollIntoViewIfNeeded();
    expect(await det.evaluate((el) => el.open), `${fam.name} starts closed`).toBeFalsy();
    await det.locator("summary").click();
    expect(await det.evaluate((el) => el.open), `${fam.name} opens on click`).toBeTruthy();
    await det.locator("summary").click();
    expect(await det.evaluate((el) => el.open), `${fam.name} closes on click`).toBeFalsy();
  });
}

test("recommendation screen is readable in dark mode", async ({ page }) => {
  await page.setViewportSize({ width: 1024, height: 700 });
  await page.goto("/onboarding-fixture");
  await setTheme(page, "dark");
  const r = await page.evaluate(() => {
    const card = document.querySelector(".recommendation-card");
    const cs = getComputedStyle(card);
    const txt = getComputedStyle(card.querySelector("p")).color.match(/[\d.]+/g).map(Number);
    return { bg: cs.backgroundColor, textLum: 0.2126 * txt[0] + 0.7152 * txt[1] + 0.0722 * txt[2] };
  });
  expect(isOpaqueLight(r.bg), "recommendation card bg must not be opaque-light in dark").toBeFalsy();
  expect(r.textLum, "recommendation card text should be light in dark").toBeGreaterThan(120);
});

// ---------------------------------------------------------------------------
// 3. No uncaught page errors on primary renderable pages.
//    (External CDN 404s in the offline mock are console noise, not app errors —
//     we only fail on real uncaught JS exceptions.)
// ---------------------------------------------------------------------------
for (const [name, url] of [
  ["account", "/account"],
  ["workspace", "/workspace"],
  ["graph", "/graph?node=entity:lattice"],
  ["admin", "/admin"],
  ["onboarding-fixture", "/onboarding-fixture"],
]) {
  test(`[${name}] has no uncaught page errors`, async ({ page }) => {
    const errors = [];
    page.on("pageerror", (e) => errors.push(e.message));
    await page.goto(url);
    await page.waitForTimeout(400);
    expect(errors, `${name} page errors: ${errors.join("; ")}`).toEqual([]);
  });
}
