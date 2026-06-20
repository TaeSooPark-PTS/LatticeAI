const { test, expect } = require("@playwright/test");

const GRAPH_SEARCH_LABEL = /Search knowledge graph|지식 그래프 검색/;

function trackPageErrors(page) {
  const errors = [];
  page.on("pageerror", (error) => errors.push(String(error.message || error)));
  return errors;
}

async function bypassProductFlow(page) {
  await page.addInitScript(() => {
    localStorage.setItem("lattice.productFlow.complete", "true");
    localStorage.setItem("lattice.language", "ko");
  });
}

async function openBrain(page) {
  await bypassProductFlow(page);
  await page.goto("/app");
  await expect(page.locator("main[aria-label='Lattice Brain']")).toBeVisible();
}

test("first-run ritual enters the living Brain", async ({ page }) => {
  const errors = trackPageErrors(page);
  await page.goto("/app");
  await page.getByRole("button", { name: "한국어" }).click();

  await expect(page.locator("body")).toContainText("내 AI 브레인의 주인을 정합니다.");
  await expect(page.locator("body")).toContainText("로컬 우선 AI 브레인입니다.");
  await expect(page.locator("body")).toContainText("외부 전송은 사용자가 선택할 때만 시작됩니다.");
  await expect(page.locator("body")).toContainText("모델은 목소리이고, 자산은 Brain입니다.");

  await page.getByPlaceholder(/You|나/).fill("Codex");
  await page.getByPlaceholder("you@local").fill("codex@local");
  await page.getByPlaceholder("로컬 Brain 비밀번호").fill("Lattice123");
  await page.getByRole("button", { name: "내 Brain 시작하기" }).click();

  await expect(page.locator("body")).toContainText("이 컴퓨터에서 가능한 경험을 확인합니다.");
  await page.getByRole("button", { name: "추천 모델 보기" }).click();

  await expect(page.locator("body")).toContainText("추천대로 시작하세요.");
  await page.getByRole("button", { name: "추천대로 시작하기" }).click();

  await expect(page.locator("body")).toContainText("모델을 설치하고 시작합니다.");
  await page.getByRole("button", { name: "다운로드하고 시작하기" }).click();
  await expect(page.locator("main[aria-label='Lattice Brain']")).toBeVisible();
  await expect(page.locator("body")).toContainText("Lattice Brain");
  await expect(page.locator("body")).toContainText("Deep Graph");
  expect(errors).toEqual([]);
});

test("Brain opens directly on the level 5 Deep Graph", async ({ page }) => {
  const errors = trackPageErrors(page);
  await openBrain(page);

  await expect(page.locator("body")).toContainText("단계 5");
  await expect(page.locator("body")).toContainText("Lattice Brain");
  await expect(page.locator("body")).toContainText("전체 지식 그래프");
  await expect(page.locator("body")).toContainText("Deep Graph");
  await expect(page.locator("[data-testid='emergent-knowledge-graph']")).toBeVisible();
  await expect(page.getByLabel(GRAPH_SEARCH_LABEL)).toBeVisible();
  await expect(page.locator(".graph-node").first()).toBeVisible();
  await expect(page.locator(".memory-fragment")).toHaveCount(0);
  await expect(page.locator(".concept-signal")).toHaveCount(0);
  await expect(page.locator(".relationship-weave")).toHaveCount(0);
  expect(errors).toEqual([]);
});

test("Deep Graph supports graph search without navigating away", async ({ page }) => {
  const errors = trackPageErrors(page);
  await openBrain(page);

  await page.getByLabel(GRAPH_SEARCH_LABEL).fill("workspace");
  await expect(page.locator(".graph-node").first()).toBeVisible();
  await expect(page.locator(".brain-graph-focus")).toContainText("Lattice AI");
  await expect(page.locator("[data-testid='emergent-knowledge-graph']")).toBeVisible();
  expect(errors).toEqual([]);
});

test("conversation keeps the Brain alive while chat streams", async ({ page }) => {
  const errors = trackPageErrors(page);
  await openBrain(page);

  await expect(page.locator("body")).toContainText("전체 지식 그래프");
  await expect(page.locator("body")).toContainText("Deep Graph");
  await expect(page.locator("#brain-model-setup")).toBeVisible();
  await expect(page.locator("#brain-model-setup")).toContainText("Gemma 4 26B");
  await expect(page.getByPlaceholder("Brain에게 말하기...")).toBeVisible();

  await expect(page.locator("section[aria-label='내 Brain 돌보기']")).toBeVisible();
  await expect(page.getByRole("button", { name: /내보내기/ })).toHaveCount(0);
  await page.getByRole("button", { name: /내 Brain 돌보기/ }).click();
  await expect(page.getByRole("button", { name: /내보내기/ })).toBeVisible();
  await expect(page.getByRole("button", { name: /백업/ })).toBeVisible();
  await expect(page.getByRole("button", { name: /복원 미리보기/ })).toBeVisible();

  await page.getByPlaceholder("Brain에게 말하기...").fill("How does hybrid search rank results?");
  await page.getByRole("button", { name: "보내기", exact: true }).click();
  await expect(page.locator("body")).toContainText("Hybrid retrieval");
  await expect(page.locator("body")).toContainText("기억에 저장됨");
  await expect(page.locator("body")).toContainText("출처와 함께 나중에 다시 불러올 수 있습니다.");
  await expect(page.locator("body")).toContainText("Lattice Brain");
  expect(errors).toEqual([]);
});

test("admin console is separated from the user Brain surface", async ({ page }) => {
  const errors = trackPageErrors(page);
  await openBrain(page);

  await expect(page.locator("main[aria-label='Lattice Brain']")).toBeVisible();
  await expect(page.locator("main.admin-console")).toHaveCount(0);

  await page.getByRole("button", { name: "관리자 콘솔" }).click();
  await expect(page).toHaveURL(/#\/admin$/);
  await expect(page.locator("main.admin-console")).toBeVisible();
  await expect(page.locator("body")).toContainText("Admin Console");
  await expect(page.locator("body")).toContainText(/Activity Logs|활동 로그/);
  await expect(page.locator("body")).toContainText(/Security Events|보안 이벤트/);

  await page.getByRole("button", { name: "Brain" }).click();
  await expect(page.locator("main[aria-label='Lattice Brain']")).toBeVisible();
  await expect(page.locator("main.admin-console")).toHaveCount(0);
  expect(errors).toEqual([]);
});

test("mobile Brain surface has no horizontal overflow", async ({ page }) => {
  const errors = trackPageErrors(page);
  await page.setViewportSize({ width: 390, height: 780 });
  await openBrain(page);

  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow).toBeLessThanOrEqual(1);
  await expect(page.locator("body")).toContainText("Deep Graph");
  await expect(page.locator("[data-testid='emergent-knowledge-graph']")).toBeVisible();
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
