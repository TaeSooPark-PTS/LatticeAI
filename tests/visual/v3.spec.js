// Lattice AI v3 — frontend shell visual + behavioural checks.
// Runs against tests/visual/mock_server.cjs which serves /app and mocks the
// future API surfaces (/api/index/status, /api/graph, /api/search/hybrid).
const { test, expect } = require("@playwright/test");

const ROUTES = [
  "home", "chat", "knowledge-graph", "hybrid-search", "files", "pipeline",
  "agents", "models", "my-computer", "settings",
  "admin/users", "admin/permissions", "admin/audit", "admin/security",
  "admin/policies", "admin/private-vpc",
];

// Uncaught JS exceptions fail a view; network 401s (best-effort identity probe)
// are expected under the mock and are ignored.
function trackPageErrors(page) {
  const errors = [];
  page.on("pageerror", (e) => errors.push(String(e.message || e)));
  return errors;
}

test("shell boots with rail, brand, topbar and mode switcher", async ({ page }) => {
  const errors = trackPageErrors(page);
  await page.goto("/app");
  await page.waitForSelector(".lt3-navitem");
  await expect(page.locator(".lt3-rail__word")).toContainText("Lattice AI");
  await expect(page.locator("#lt3-mode")).toBeVisible();
  // Basic mode shows the core 7 primary items (no admin group).
  expect(await page.locator(".lt3-navitem").count()).toBe(7);
  expect(errors).toEqual([]);
});

test("home leads with the retrieval lattice (3 pillars)", async ({ page }) => {
  await page.goto("/app#/home");
  await page.waitForSelector(".lt3-pillar");
  await expect(page.locator(".lt3-pillar")).toHaveCount(3);
  await expect(page.locator(".lt3-pillars")).toContainText("Knowledge Graph");
  await expect(page.locator(".lt3-pillars")).toContainText("Vector Index");
  await expect(page.locator(".lt3-pillars")).toContainText("Hybrid Search");
});

test("every primary + admin view renders without JS errors", async ({ page }) => {
  for (const route of ROUTES) {
    const errors = trackPageErrors(page);
    await page.goto(`/app#/${route}`);
    // Each view renders either a standard view header or the flush chat surface.
    await page.waitForSelector(".lt3-vhead, .lt3-chat", { timeout: 8000 });
    expect(errors, `route ${route} threw`).toEqual([]);
  }
});

test("admin mode reveals the six administration areas", async ({ page }) => {
  await page.goto("/app#/home");
  await page.waitForSelector(".lt3-navitem");
  await page.locator('#lt3-mode button[data-mode="admin"]').click();
  await page.waitForSelector('.lt3-navgroup__label:has-text("Administration")');
  const adminItems = page.locator(".lt3-navgroup", { has: page.locator('.lt3-navgroup__label:has-text("Administration")') }).locator(".lt3-navitem");
  await expect(adminItems).toHaveCount(6);
});

test("theme toggle flips light/dark", async ({ page }) => {
  await page.goto("/app");
  await page.waitForSelector("#lt3-theme");
  await page.evaluate(() => { try { localStorage.setItem("lt-theme", "light"); } catch (e) {} });
  await page.reload();
  await page.waitForSelector("#lt3-theme");
  await page.locator("#lt3-theme").click();
  await expect(page.locator("html")).toHaveAttribute("data-lt-theme", "dark");
});

test("command palette opens and filters", async ({ page }) => {
  await page.goto("/app");
  await page.waitForSelector(".lt3-cmd-trigger");
  await page.locator(".lt3-cmd-trigger").click();
  await page.waitForSelector("#lt3-palette");
  await page.locator("#lt3-palette input").fill("hybrid");
  await expect(page.locator(".lt3-palette__item").first()).toContainText("Hybrid");
});

test("knowledge graph renders an SVG mesh of entities", async ({ page }) => {
  await page.goto("/app#/knowledge-graph");
  await page.waitForSelector(".lt3-graph-canvas svg .lt3-gnode");
  expect(await page.locator(".lt3-gnode").count()).toBeGreaterThan(0);
  await expect(page.locator(".lt3-entity").first()).toBeVisible();
});

test("hybrid search returns fused results", async ({ page }) => {
  await page.goto("/app#/hybrid-search");
  await page.waitForSelector(".lt3-search input");
  await page.locator(".lt3-search input").fill("retrieval");
  await page.locator(".lt3-search input").press("Enter");
  await page.waitForSelector(".lt3-result", { timeout: 8000 });
  expect(await page.locator(".lt3-result").count()).toBeGreaterThan(0);
});

test("chat is a native v3 view (no redirect) with conversations, context and streaming", async ({ page }) => {
  await page.goto("/app#/chat");
  await page.waitForSelector(".lt3-chat");
  // Must NOT redirect to the legacy /chat page.
  expect(page.url()).toContain("/app#/chat");
  expect(await page.locator(".lt3-chat__main").count()).toBe(1);
  // Conversation rail + the four retrieval-context sections.
  await expect(page.locator(".lt3-convo").first()).toBeVisible();
  await expect(page.locator(".lt3-ctx-sec__title")).toHaveCount(4);
  // Sending a message streams an assistant reply.
  await page.locator(".lt3-composer textarea").fill("How does hybrid search rank results?");
  await page.locator(".lt3-composer textarea").press("Enter");
  await page.waitForFunction(() => {
    const b = document.querySelectorAll(".lt3-msg--ai .lt3-msg__bubble");
    return b.length && b[b.length - 1].textContent.trim().length > 0;
  }, { timeout: 8000 });
  expect(await page.locator(".lt3-msg--user").count()).toBeGreaterThan(0);
});

test("mobile: no horizontal overflow and the nav drawer toggles", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 780 });
  for (const route of ["home", "knowledge-graph", "hybrid-search", "settings", "admin/users"]) {
    await page.goto(`/app#/${route}`);
    await page.waitForSelector(".lt3-vhead, .lt3-chat");
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    expect(overflow, `route ${route} overflows`).toBeLessThanOrEqual(1);
  }
  await page.goto("/app#/home");
  await page.waitForSelector(".lt3-topbar__menu");
  // Rail starts off-canvas, opens on the hamburger, closes via scrim.
  expect(await page.locator(".lt3-rail").evaluate((el) => el.getBoundingClientRect().left)).toBeLessThan(-10);
  await page.locator(".lt3-topbar__menu").click();
  await page.waitForTimeout(350);
  expect(await page.locator(".lt3-rail").evaluate((el) => el.getBoundingClientRect().left)).toBeGreaterThanOrEqual(-1);
});

test("dark-mode home screenshot is non-blank", async ({ page }) => {
  await page.evaluate(() => {}).catch(() => {});
  await page.goto("/app");
  await page.evaluate(() => { try { localStorage.setItem("lt-theme", "dark"); } catch (e) {} });
  await page.goto("/app#/home");
  await page.waitForSelector(".lt3-pillar");
  const shot = await page.screenshot();
  expect(shot.length).toBeGreaterThan(20_000);
});
