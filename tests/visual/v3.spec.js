const { test, expect } = require("@playwright/test");

const GRAPH_SEARCH_PLACEHOLDER = /Search ideas|Search graph labels|검색/;

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

  await expect(page.locator("body")).toContainText("내 지식이 살아나는 Brain을 시작하세요.");
  await expect(page.locator("body")).toContainText("모델은 바꿔도 Brain은 계속 내 것입니다.");
  await expect(page.locator("body")).toContainText("완전 로컬");
  await expect(page.getByRole("button", { name: "Brain 지금 깨우기" })).toBeVisible();
  await page.getByRole("button", { name: "Brain 지금 깨우기" }).click();

  await expect(page.locator("body")).toContainText("이 Brain의 주인을 정합니다.");
  await expect(page.locator("body")).toContainText("로컬 Brain의 소유권");
  await expect(page.locator("body")).toContainText("먼저 안전하게 확인합니다.");
  await expect(page.locator("body")).toContainText("모델은 목소리이고, 자산은 Brain입니다.");

  await page.getByPlaceholder(/You|나/).fill("Codex");
  await page.getByPlaceholder("you@local").fill("codex@local");
  await page.getByPlaceholder("로컬 Brain 비밀번호").fill("Lattice123");
  await page.getByRole("button", { name: "내 Brain 시작하기" }).click();

  await expect(page.locator("body")).toContainText("추천대로 시작하세요.");
  await page.getByRole("button", { name: "추천으로 바로 시작" }).click();

  await expect(page.locator("body")).toContainText("모델을 준비하고 시작합니다.");
  await page.getByRole("button", { name: "준비하고 시작하기" }).click();
  await expect(page.locator("main[aria-label='Lattice Brain']")).toBeVisible();
  await expect(page.locator("body")).toContainText("지금 Brain이 기억을 만들 준비가 됐습니다");
  await expect(page.locator("body")).not.toContainText("전체 지식 그래프");
  expect(errors).toEqual([]);
});

test("Brain home opens the memory graph instead of a duplicate lower Brain", async ({ page }) => {
  const errors = trackPageErrors(page);
  await openBrain(page);

  await expect(page.locator("body")).toContainText("지금 Brain이 기억을 만들 준비가 됐습니다");
  await expect(page.locator(".memory-fragment")).toHaveCount(0);
  await expect(page.locator("[data-testid='brain-cytoscape']")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "기억 보기" })).toHaveCount(0);

  await page.locator(".brain-center-orb .brain-organism").click();
  await expect(page.locator("body")).toContainText("지식 연결망");
  await expect(page.locator("[data-testid='brain-cytoscape']")).toBeVisible();
  await expect(page.getByPlaceholder(GRAPH_SEARCH_PLACEHOLDER)).toBeVisible();
  expect(errors).toEqual([]);
});

test("memory graph supports graph search and returning to the surface", async ({ page }) => {
  const errors = trackPageErrors(page);
  await openBrain(page);
  await page.getByRole("button", { name: "기억 그래프" }).click();
  await expect(page.locator("[data-testid='brain-cytoscape']")).toBeVisible();

  await page.getByPlaceholder(GRAPH_SEARCH_PLACEHOLDER).fill("workspace");
  await expect(page.locator("body")).toContainText("Workspace Health");

  await page.getByRole("button", { name: "대화" }).click();
  await expect(page.locator("body")).toContainText("지금 Brain이 기억을 만들 준비가 됐습니다");
  await expect(page.locator("[data-testid='brain-cytoscape']")).toHaveCount(0);
  expect(errors).toEqual([]);
});

test("conversation keeps the Brain alive while chat streams", async ({ page }) => {
  const errors = trackPageErrors(page);
  await openBrain(page);

  await expect(page.locator("nav[aria-label='Brain workspace navigation']")).toBeVisible();
  await expect(page.locator("section[aria-label='Brain Chat Home']")).toBeVisible();
  await expect(page.locator("body")).toContainText("지금 Brain이 기억을 만들 준비가 됐습니다");
  await expect(page.locator("body")).toContainText("대화 한 줄이나 문서 하나가 기억이 되고");
  await expect(page.locator("section[aria-label='제품 상태와 다음 행동']")).not.toBeVisible();
  await expect(page.locator("[aria-label='Brain 깊이 진행 상태']")).toHaveCount(0);
  await page.getByRole("button", { name: /과거 결정들을 Brain이 구조화해서/ }).click();
  await expect(page.getByPlaceholder("Brain에게 말하기...")).toHaveValue("과거 결정들을 Brain이 구조화해서 나중에 쉽게 찾게 해줘: ");
  await page.getByPlaceholder("Brain에게 말하기...").fill("");

  await page.getByText("자료와 설정").click();
  await expect(page.locator("section[aria-label='제품 상태와 다음 행동']")).toBeVisible();
  await expect(page.getByRole("button", { name: /근거 확인/ })).toBeVisible();
  await expect(page.locator("body")).toContainText("Brain 한눈에 보기");
  await expect(page.locator("body")).toContainText("Brain 준비도");
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

  await page.getByText("자료와 설정").click();
  await page.getByRole("button", { name: "관리자 콘솔", exact: true }).click();
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

test("Review Center loads actionable review evidence", async ({ page }) => {
  const errors = trackPageErrors(page);
  await bypassProductFlow(page);
  await page.goto("/app#/review");

  await expect(page.locator("body")).toContainText("검토함");
  await expect(page.locator("body")).toContainText("Approve 8.1 product readiness evidence");
  await expect(page.locator("body")).toContainText("Review generated screenshots");
  await expect(page.locator("body")).not.toContainText("not valid JSON");
  await expect(page.locator("body")).not.toContainText("Unexpected token");
  expect(errors).toEqual([]);
});

test("mobile Brain surface has no horizontal overflow", async ({ page }) => {
  const errors = trackPageErrors(page);
  await page.setViewportSize({ width: 390, height: 780 });
  await openBrain(page);

  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow).toBeLessThanOrEqual(1);
  await expect(page.locator("body")).toContainText("지금 Brain이 기억을 만들 준비가 됐습니다");
  await expect(page.getByPlaceholder("Brain에게 말하기...")).toBeVisible();
  expect(errors).toEqual([]);
});

test("legacy entry URLs still arrive at the Brain app", async ({ page }) => {
  await bypassProductFlow(page);
  await page.goto("/chat");
  await expect(page).toHaveURL(/\/app#\/chat$/);
  await expect(page.locator("body")).toContainText("지금 Brain이 기억을 만들 준비가 됐습니다");

  await page.goto("/graph");
  await expect(page).toHaveURL(/\/app#\/knowledge-graph$/);
  await expect(page.locator("body")).toContainText("지식 연결망");
});
