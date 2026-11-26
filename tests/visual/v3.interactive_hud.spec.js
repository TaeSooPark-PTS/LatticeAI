const { test, expect } = require("@playwright/test");
const { trackPageErrors, openBrain } = require("./v3_helpers");

test("fixed-viewport desktop shell, frosted glass HUD, vital pill, command palette, and graph controls all respond cleanly", async ({ page }) => {
  const errors = trackPageErrors(page);
  await page.setViewportSize({ width: 1360, height: 840 });
  await openBrain(page);

  const shellMetrics = await page.evaluate(() => {
    const html = document.documentElement;
    const topbar = document.querySelector(".brain-topbar");
    const topbarStyle = topbar ? getComputedStyle(topbar) : null;
    return {
      htmlOverflowY: getComputedStyle(html).overflowY,
      verticalOverflow: html.scrollHeight - html.clientHeight,
      horizontalOverflow: html.scrollWidth - html.clientWidth,
      backdropFilter: topbarStyle ? (topbarStyle.backdropFilter || topbarStyle.webkitBackdropFilter || "") : "",
    };
  });
  expect(shellMetrics.htmlOverflowY).toBe("hidden");
  expect(shellMetrics.verticalOverflow).toBeLessThanOrEqual(1);
  expect(shellMetrics.horizontalOverflow).toBeLessThanOrEqual(1);
  expect(shellMetrics.backdropFilter).toContain("blur");

  const vitalPill = page.getByTestId("brain-vital-pill");
  await expect(vitalPill).toBeVisible();
  await expect(vitalPill).toHaveAttribute("data-stage", /seed|cortex|neural_web|lattice/);

  const composer = page.getByPlaceholder("질문하거나, 자료를 붙여 넣거나, 할 일을 적어보세요");
  await composer.fill("How does hybrid search rank results?");
  await page.getByRole("button", { name: "보내기", exact: true }).click();
  await expect(page.locator("body")).toContainText("Hybrid retrieval");

  const activeChatOverflow = await page.evaluate(() => ({
    vertical: document.documentElement.scrollHeight - document.documentElement.clientHeight,
    streamOverflowY: getComputedStyle(document.querySelector(".brain-stream")).overflowY,
  }));
  expect(activeChatOverflow.vertical).toBeLessThanOrEqual(1);
  expect(activeChatOverflow.streamOverflowY).toBe("auto");

  await page.keyboard.press("ControlOrMeta+k");
  const palette = page.getByTestId("command-palette");
  await expect(palette).toBeVisible();
  const sortToggle = page.getByTestId("command-sort-toggle");
  await expect(sortToggle).toBeVisible();
  await expect(sortToggle).toHaveAttribute("aria-pressed", "false");
  await sortToggle.click();
  await expect(sortToggle).toHaveAttribute("aria-pressed", "true");
  await page.keyboard.press("Escape");
  await expect(palette).toHaveCount(0);

  await page.getByRole("link", { name: "기억", exact: true }).click();
  await expect(page).toHaveURL(/#\/hybrid-search$/);
  const newestToggle = page.getByTestId("toggle-newest-search");
  if (await newestToggle.isVisible().catch(() => false)) {
    const before = await newestToggle.getAttribute("aria-pressed");
    await newestToggle.click();
    const after = await newestToggle.getAttribute("aria-pressed");
    expect(after).not.toBe(before);
  }

  await vitalPill.click();
  await expect(page).toHaveURL(/#\/knowledge-graph$/);
  await expect(page.getByTestId("brain-graph-stage")).toBeVisible();

  const viewToggle = page.getByTestId("graph-view-toggle");
  await expect(viewToggle).toBeVisible();
  const initialPressed = await viewToggle.getAttribute("aria-pressed");
  await viewToggle.click();
  await expect(viewToggle).toHaveAttribute("aria-pressed", initialPressed === "true" ? "false" : "true");
  await expect(page.getByTestId("brain-cytoscape")).toBeVisible();
  await viewToggle.click();
  await expect(viewToggle).toHaveAttribute("aria-pressed", initialPressed);

  const supersededToggle = page.getByTestId("graph-superseded-toggle");
  await expect(supersededToggle).toBeVisible();
  const supBefore = await supersededToggle.getAttribute("aria-pressed");
  await supersededToggle.click();
  await expect(supersededToggle).toHaveAttribute("aria-pressed", supBefore === "true" ? "false" : "true");

  const firstNodePick = page.locator(".graph-explorer-pick").first();
  await expect(firstNodePick).toBeVisible();
  await firstNodePick.click();
  await expect(page.getByTestId("graph-node-inspector")).toBeVisible();
  await expect(page.getByTestId("graph-inspector-drawer")).toHaveClass(/is-docked-open/);

  const openSourceBtn = page.getByTestId("inspector-open-source");
  await openSourceBtn.click();
  await expect(page.getByTestId("source-chunk-modal")).toBeVisible();
  await expect(page.getByTestId("source-chunk-modal")).toHaveClass(/evidence-drawer/);
  await page.keyboard.press("Escape");
  await expect(page.getByTestId("source-chunk-modal")).toHaveCount(0);

  await page.getByTestId("graph-inspector-close").click();
  await expect(page.getByTestId("graph-node-inspector")).toHaveCount(0);

  for (const label of ["자료", "연대기", "대화"]) {
    await page.getByRole("link", { name: label, exact: true }).click();
    await expect(page.locator("header.brain-topbar")).toBeVisible();
  }

  expect(errors).toEqual([]);
});
