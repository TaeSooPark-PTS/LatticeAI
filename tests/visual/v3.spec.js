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

  await expect(page.locator("body")).toContainText("내 Brain을 시작합니다.");
  await expect(page.locator("body")).toContainText("모델은 바뀔 수 있지만");
  await expect(page.locator("body")).toContainText("모델은 목소리이고, 자산은 Brain입니다.");
  await expect(page.getByRole("button", { name: "Travel deeper into your Brain" })).toBeVisible();

  await page.getByRole("textbox", { name: "You", exact: true }).fill("Codex");
  await page.getByRole("textbox", { name: "you@local", exact: true }).fill("codex@local");
  await page.getByPlaceholder("로컬 Brain 비밀번호").fill("Lattice123");
  await page.getByRole("button", { name: "내 Brain 시작하기" }).click();

  await expect(page.locator("body")).toContainText("이 컴퓨터를 확인합니다.");
  await page.getByRole("button", { name: "추천 모델 보기" }).click();

  await expect(page.locator("body")).toContainText("추천 모델로 시작하세요.");
  await page.getByRole("button", { name: "추천대로 시작하기" }).click();

  await expect(page.locator("body")).toContainText("모델을 설치하고 시작합니다.");
  await page.getByRole("button", { name: "다운로드하고 시작하기" }).click();
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
  await expect(page.getByRole("button", { name: "기억 보기" })).toBeVisible();
  await expect(page.getByRole("button", { name: "주제 보기" })).toBeVisible();

  await page.getByRole("button", { name: "기억 보기" }).click();
  await expect(page.locator("body")).toContainText("Memory Layer");
  await expect(page.locator(".memory-fragment").first()).toBeVisible();
  await expect(page.locator("body")).not.toContainText("Knowledge Graph");

  await page.getByRole("button", { name: "주제 보기" }).click();
  await expect(page.locator("body")).toContainText("Knowledge Layer");
  await expect(page.locator(".concept-signal").first()).toBeVisible();
  await expect(page.locator("body")).not.toContainText("Knowledge Graph");

  await page.getByRole("button", { name: "관계 보기" }).click();
  await expect(page.locator("body")).toContainText("Relationship Layer");
  await expect(page.locator(".relationship-weave line").first()).toBeAttached();
  await expect(page.locator("body")).not.toContainText("Knowledge Graph");

  await page.getByRole("button", { name: "그래프로 보기" }).click();
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

  await expect(page.locator("body")).toContainText("잊으면 안 되는 일부터 말해 주세요.");
  await expect(page.locator("body")).toContainText("Brain 한눈에 보기");
  await page.getByRole("button", { name: /Remember this decision/ }).click();
  await expect(page.getByPlaceholder("Talk to your Brain...")).toHaveValue("Remember this decision: ");
  await page.getByPlaceholder("Talk to your Brain...").fill("");

  await expect(page.locator("section[aria-label='Care for my Brain']")).toBeVisible();
  await expect(page.getByRole("button", { name: /Export/ })).toHaveCount(0);
  await page.getByRole("button", { name: /Care for my Brain/ }).click();
  await expect(page.getByRole("button", { name: /Export/ })).toBeVisible();
  await expect(page.getByRole("button", { name: /Backup/ })).toBeVisible();
  await expect(page.getByRole("button", { name: /Restore preview/ })).toBeVisible();

  await page.getByPlaceholder("Talk to your Brain...").fill("How does hybrid search rank results?");
  await page.getByRole("button", { name: "Send" }).click();
  await expect(page.locator("body")).toContainText("Hybrid retrieval");
  await expect(page.locator("body")).toContainText("기억에 저장됨");
  await expect(page.locator("body")).toContainText("Living Brain");
  expect(errors).toEqual([]);
});

test("admin console is separated from the user Brain surface", async ({ page }) => {
  const errors = trackPageErrors(page);
  await openBrain(page);

  await expect(page.locator("main[aria-label='Lattice Brain']")).toBeVisible();
  await expect(page.locator("main[aria-label='Lattice Admin']")).toHaveCount(0);

  await page.getByRole("button", { name: "Admin" }).click();
  await expect(page).toHaveURL(/#\/admin$/);
  await expect(page.locator("main[aria-label='Lattice Admin']")).toBeVisible();
  await expect(page.locator("body")).toContainText("Admin Console");
  await expect(page.locator("body")).toContainText("Activity Logs");
  await expect(page.locator("body")).toContainText("Security Events");

  await page.getByRole("button", { name: "Brain" }).click();
  await expect(page.locator("main[aria-label='Lattice Brain']")).toBeVisible();
  await expect(page.locator("main[aria-label='Lattice Admin']")).toHaveCount(0);
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
