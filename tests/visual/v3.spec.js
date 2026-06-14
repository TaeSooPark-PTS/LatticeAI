const { test, expect } = require("@playwright/test");

function trackPageErrors(page) {
  const errors = [];
  page.on("pageerror", (error) => errors.push(String(error.message || error)));
  return errors;
}

async function bypassProductFlow(page) {
  await page.addInitScript(() => {
    localStorage.setItem("lattice.productFlow.complete", "true");
  });
}

async function openBrain(page) {
  await bypassProductFlow(page);
  await page.goto("/app");
  await expect(page.locator("main[aria-label='Lattice Brain']")).toBeVisible();
}

async function travelDeeper(page, times = 1) {
  const brain = page.getByRole("button", { name: "Travel deeper into your Brain" });
  for (let index = 0; index < times; index += 1) {
    await brain.click();
  }
}

test("first-run ritual enters the living Brain", async ({ page }) => {
  const errors = trackPageErrors(page);
  await page.goto("/app");

  await expect(page.locator("body")).toContainText("Welcome to your mind.");
  await expect(page.getByRole("button", { name: "Travel deeper into your Brain" })).toBeVisible();

  await page.getByRole("textbox", { name: "You", exact: true }).fill("Codex");
  await page.getByRole("textbox", { name: "you@local", exact: true }).fill("codex@local");
  await page.getByPlaceholder("Create a strong local password").fill("Lattice123");
  await page.getByRole("button", { name: "Open my Brain" }).click();

  await expect(page.locator("body")).toContainText("Understanding your home.");
  await page.getByRole("button", { name: "See how your Brain can think" }).click();

  await expect(page.locator("body")).toContainText("How shall your mind think today?");
  await page.locator(".ritual-model-card").first().click();

  await expect(page.locator("body")).toContainText("Bring this mind home.");
  await page.getByRole("button", { name: /make this my Brain/ }).click();
  await expect(page.locator("main[aria-label='Lattice Brain']")).toBeVisible();
  await expect(page.locator("body")).toContainText("Living Brain");
  await expect(page.locator("body")).not.toContainText("Knowledge Graph");
  expect(errors).toEqual([]);
});

test("Brain depths reveal memory, knowledge, relationships, then graph", async ({ page }) => {
  const errors = trackPageErrors(page);
  await openBrain(page);

  await expect(page.locator("body")).toContainText("Level 1");
  await expect(page.locator("body")).toContainText("Living Brain");
  await expect(page.locator(".memory-fragment")).toHaveCount(0);
  await expect(page.locator("[data-testid='emergent-knowledge-graph']")).toHaveCount(0);

  await travelDeeper(page);
  await expect(page.locator("body")).toContainText("Memory Layer");
  await expect(page.locator(".memory-fragment").first()).toBeVisible();
  await expect(page.locator("body")).not.toContainText("Knowledge Graph");

  await travelDeeper(page);
  await expect(page.locator("body")).toContainText("Knowledge Layer");
  await expect(page.locator(".concept-signal").first()).toBeVisible();
  await expect(page.locator("body")).not.toContainText("Knowledge Graph");

  await travelDeeper(page);
  await expect(page.locator("body")).toContainText("Relationship Layer");
  await expect(page.locator(".relationship-weave line").first()).toBeAttached();
  await expect(page.locator("body")).not.toContainText("Knowledge Graph");

  await travelDeeper(page);
  await expect(page.locator("body")).toContainText("Knowledge Graph");
  await expect(page.locator("[data-testid='emergent-knowledge-graph']")).toBeVisible();
  await expect(page.getByLabel("Search knowledge graph")).toBeVisible();
  await expect(page.locator(".graph-node").first()).toBeVisible();
  expect(errors).toEqual([]);
});

test("deepest Brain layer supports graph search and returning to the surface", async ({ page }) => {
  const errors = trackPageErrors(page);
  await openBrain(page);
  await travelDeeper(page, 4);

  await page.getByLabel("Search knowledge graph").fill("workspace");
  await expect(page.locator(".graph-node")).toHaveCount(2);
  await expect(page.locator(".brain-graph-focus")).toContainText("Lattice AI");

  await page.getByRole("button", { name: "Surface" }).click();
  await expect(page.locator("body")).toContainText("Living Brain");
  await expect(page.locator("[data-testid='emergent-knowledge-graph']")).toHaveCount(0);
  expect(errors).toEqual([]);
});

test("conversation keeps the Brain alive while chat streams", async ({ page }) => {
  const errors = trackPageErrors(page);
  await openBrain(page);

  await expect(page.getByLabel("Care for my Brain")).toBeVisible();
  await expect(page.getByRole("button", { name: /Export/ })).toBeVisible();
  await expect(page.getByRole("button", { name: /Backup/ })).toBeVisible();
  await expect(page.getByRole("button", { name: /Restore preview/ })).toBeVisible();

  await page.getByPlaceholder("Talk to your Brain...").fill("How does hybrid search rank results?");
  await page.getByRole("button", { name: "Send" }).click();
  await expect(page.locator("body")).toContainText("Hybrid retrieval");
  await expect(page.locator("body")).toContainText("Living Brain");
  expect(errors).toEqual([]);
});

test("mobile Brain surface has no horizontal overflow", async ({ page }) => {
  const errors = trackPageErrors(page);
  await page.setViewportSize({ width: 390, height: 780 });
  await openBrain(page);

  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow).toBeLessThanOrEqual(1);
  await expect(page.getByRole("button", { name: "Travel deeper into your Brain" })).toBeVisible();
  expect(errors).toEqual([]);
});

test("legacy entry URLs still arrive at the Brain app", async ({ page }) => {
  await bypassProductFlow(page);
  await page.goto("/chat");
  await expect(page).toHaveURL(/\/app#\/chat$/);
  await expect(page.locator("body")).toContainText("Lattice Brain");

  await page.goto("/graph");
  await expect(page).toHaveURL(/\/app#\/knowledge-graph$/);
  await expect(page.locator("body")).toContainText("Lattice Brain");
});
