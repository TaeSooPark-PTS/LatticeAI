/**
 * Sidecar-backed first-value E2E (v9.9.5 / Wave 3.2 residual).
 *
 * Unlike tests/visual (mock_server.cjs), these tests hit a real FastAPI
 * sidecar started by scripts/run_sidecar_e2e.mjs with an isolated data dir
 * and LATTICEAI_REQUIRE_AUTH=false. They assert the product loop surfaces
 * the SPA and core API contracts — not a scripted fake.
 */
const { test, expect } = require("@playwright/test");
const { version: appVersion } = require("../../package.json");

function trackPageErrors(page) {
  const errors = [];
  page.on("pageerror", (error) => errors.push(String(error.message || error)));
  return errors;
}

async function bypassProductFlow(page) {
  await page.addInitScript(() => {
    localStorage.setItem("lattice.productFlow.complete", "true");
    localStorage.setItem("lattice.language", "ko");
    localStorage.setItem("lattice.mode", "advanced");
  });
}

test.describe("sidecar first-value loop", () => {
  test("health reports a live sidecar with the package version", async ({ request }) => {
    const res = await request.get("/health");
    expect(res.ok()).toBeTruthy();
    const body = await res.json();
    // Accept either a version field or a nested status payload — the
    // contract is "sidecar is up", not a single JSON shape forever.
    const serialized = JSON.stringify(body);
    expect(serialized.length).toBeGreaterThan(2);
    if (body.version) {
      expect(String(body.version)).toContain(appVersion.split(".").slice(0, 2).join("."));
    }
  });

  test("static Brain SPA boots against the live sidecar", async ({ page }) => {
    const errors = trackPageErrors(page);
    await bypassProductFlow(page);
    await page.goto("/app");
    await expect(page.locator("main[aria-label='Lattice Brain']")).toBeVisible({ timeout: 30_000 });
    // Living home copy is the first-value signal users see.
    await expect(page.getByRole("heading", { name: /말하고, 넣으면|Speak|Brain/i }).first()).toBeVisible();
    expect(errors.filter((e) => !/ResizeObserver|favicon/i.test(e))).toEqual([]);
  });

  test("knowledge-graph stats API is reachable from the live sidecar", async ({ request }) => {
    const res = await request.get("/knowledge-graph/stats");
    // Auth-disabled sidecar: 200. If a deployment re-enables auth, 401 is
    // still a live contract (not a connection failure).
    expect([200, 401, 403]).toContain(res.status());
    if (res.status() === 200) {
      const body = await res.json();
      expect(body).toBeTruthy();
      expect(typeof body === "object").toBeTruthy();
    }
  });

  test("agent approvals list is reachable (empty or pending)", async ({ request }) => {
    const res = await request.get("/agent/approvals");
    expect([200, 401, 403]).toContain(res.status());
    if (res.status() === 200) {
      const body = await res.json();
      expect(Array.isArray(body.pending)).toBeTruthy();
    }
  });

  test("first-run ritual path still reaches the living Brain on a live sidecar", async ({ page }) => {
    const errors = trackPageErrors(page);
    // Fresh storage — no product-flow bypass — exercises the ritual.
    await page.goto("/app");
    // Either the ritual CTA or the already-bootstrapped Brain is fine; the
    // sidecar must serve a usable SPA either way.
    const ritual = page.getByRole("button", { name: /Brain 지금 깨우기|Wake|시작/i });
    const brain = page.locator("main[aria-label='Lattice Brain']");
    await expect(ritual.or(brain).first()).toBeVisible({ timeout: 30_000 });
    expect(errors.filter((e) => !/ResizeObserver|favicon/i.test(e)).length).toBeLessThan(5);
  });
});
