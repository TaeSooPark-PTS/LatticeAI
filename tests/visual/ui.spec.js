const { test, expect } = require("@playwright/test");

test("workspace page renders health dashboard", async ({ page }) => {
  await page.goto("/workspace");
  await expect(page.locator("#workspace-health-grid")).toContainText("Indexed Files");
  await expect(page.locator("#workspace-health-grid")).toContainText("v1.7.0");
  await expect(page.locator("#workspace-health-status")).toContainText("ready");
  const shot = await page.screenshot();
  expect(shot.length).toBeGreaterThan(20_000);
});

test("graph page renders an interactive nonblank canvas", async ({ page }) => {
  await page.goto("/graph?node=entity:lattice");
  await expect(page.locator("#node-count")).toContainText("5");
  await page.waitForTimeout(500);
  const paintedPixels = await page.locator("#graph").evaluate((canvas) => {
    const ctx = canvas.getContext("2d");
    const { width, height } = canvas;
    const data = ctx.getImageData(0, 0, width, height).data;
    let painted = 0;
    for (let i = 3; i < data.length; i += 80) {
      if (data[i] > 0) painted += 1;
    }
    return painted;
  });
  expect(paintedPixels).toBeGreaterThan(100);
});

test("skills marketplace shows progress-ready metadata", async ({ page }) => {
  await page.goto("/workspace#skills");
  await expect(page.locator("#skill-list")).toContainText("visual_regression");
  await expect(page.locator("#skill-list")).toContainText("validation:");
  await expect(page.locator("#skill-updates-count")).toBeAttached();
  const shot = await page.screenshot();
  expect(shot.length).toBeGreaterThan(20_000);
});

test("organization workspace page renders member management", async ({ page }) => {
  await page.goto("/workspace#organization");
  await expect(page.locator("#workspace-list")).toContainText("Design Org");
  await expect(page.locator("#edition-pill")).toContainText("community");
});

test("enterprise admin page renders policy and SIEM surfaces", async ({ page }) => {
  await page.goto("/admin#enterprise");
  await expect(page.locator("#enterprise-capability-status")).toContainText("siem export");
  await expect(page.locator("#enterprise-admin-policies")).toContainText("admin_policy_packs");
  await expect(page.locator("#enterprise-siem-preview")).toContainText("ltcai.siem.v1");
  const shot = await page.screenshot();
  expect(shot.length).toBeGreaterThan(20_000);
});
