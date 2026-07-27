const { test, expect } = require("@playwright/test");
const { version: appVersion } = require("../../package.json");

const GRAPH_SEARCH_PLACEHOLDER = /Search ideas|Search graph labels|검색/;

function trackPageErrors(page) {
  const errors = [];
  page.on("pageerror", (error) => errors.push(String(error.message || error)));
  return errors;
}

async function bypassProductFlow(page, { mode = "advanced" } = {}) {
  await page.addInitScript(({ workspaceMode }) => {
    localStorage.setItem("lattice.productFlow.complete", "true");
    localStorage.setItem("lattice.language", "ko");
    localStorage.setItem("lattice.mode", workspaceMode);
  }, { workspaceMode: mode });
}

// Automation, briefings, and health panels moved off the first screen into the
// "Brain이 정리한 내용" shelf. Tests that exercise them open it explicitly.
async function openInsightsShelf(page) {
  await page.getByTestId("brain-insights-shelf").locator("> summary").click();
}

async function openBrain(page, options) {
  await bypassProductFlow(page, options);
  await page.goto("/app");
  await expect(page.locator("main[aria-label='Lattice Brain']")).toBeVisible();
}

async function openShellMenu(page) {
  await page.getByRole("button", { name: "메뉴 열기" }).click();
  await expect(page.locator("#brain-more-popover")).toBeVisible();
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
  await expect(page.getByRole("heading", { name: "말하고, 넣으면, Brain이 연결합니다." })).toBeVisible();
  await expect(page.locator("body")).not.toContainText("전체 지식 그래프");
  expect(errors).toEqual([]);
});

test("the Brain home is one screen: Brain, composer, add material, quiet settings", async ({ page }) => {
  const errors = trackPageErrors(page);
  await page.setViewportSize({ width: 1280, height: 800 });
  await openBrain(page);

  await expect(page.getByRole("heading", { name: "말하고, 넣으면, Brain이 연결합니다." })).toBeVisible();
  const stage = page.getByTestId("brain-home-stage");
  const livingBrain = page.getByTestId("brain-knowledge-flow").getByTestId("living-brain");
  const ingestionDock = page.getByTestId("brain-ingestion-dock");
  await expect(stage).toBeVisible();
  await expect(page.getByTestId("brain-knowledge-flow")).toBeVisible();
  await expect(livingBrain).toBeVisible();
  await expect(ingestionDock).toBeVisible();
  // Nothing graph-shaped on the home any more: the knowledge graph opens by
  // clicking the Brain itself (asserted at the end of this test).
  await expect(page.locator(".brain-flow-node")).toHaveCount(0);
  await expect(page.locator(".brain-flow-edges line")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "폴더", exact: true })).toBeVisible();
  // Autonomy and appearance are decided here, not buried in settings.
  await expect(page.getByTestId("quick-mode-strict")).toBeVisible();
  await expect(page.getByTestId("topbar-theme-toggle")).toBeVisible();
  // The onboarding tracks that used to compete with the composer are gone.
  await expect(page.getByTestId("first-value-loop")).toHaveCount(0);
  await expect(page.getByTestId("brain-first-five")).toHaveCount(0);
  await expect(page.locator(".memory-fragment")).toHaveCount(0);
  await expect(page.locator("[data-testid='brain-cytoscape']")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "기억 보기" })).toHaveCount(0);

  const viewportFit = await page.evaluate(() => {
    const root = document.documentElement;
    const stageElement = document.querySelector("[data-testid='brain-home-stage']");
    const rect = stageElement?.getBoundingClientRect();
    const bodyMotion = getComputedStyle(document.querySelector(".brain-body-motion"));
    const vitalRing = getComputedStyle(document.querySelector(".brain-vital-ring.is-primary"));
    return {
      verticalOverflow: root.scrollHeight - root.clientHeight,
      horizontalOverflow: root.scrollWidth - root.clientWidth,
      stageBottom: rect?.bottom ?? Number.POSITIVE_INFINITY,
      bodyAnimation: bodyMotion.animationName,
      ringAnimation: vitalRing.animationName,
    };
  });
  expect(viewportFit.verticalOverflow).toBeLessThanOrEqual(1);
  expect(viewportFit.horizontalOverflow).toBeLessThanOrEqual(1);
  expect(viewportFit.stageBottom).toBeLessThanOrEqual(801);
  expect(viewportFit.bodyAnimation).not.toBe("none");
  expect(viewportFit.ringAnimation).not.toBe("none");

  const composer = page.getByPlaceholder("질문하거나, 자료를 붙여 넣거나, 할 일을 적어보세요");
  for (const critical of [livingBrain, ingestionDock, composer]) {
    await expect(critical).toBeInViewport({ ratio: 0.95 });
  }
  await composer.fill("새 기억을 연결해서 다음 행동을 찾아줘");
  await expect(livingBrain).toHaveAttribute("data-state", "listening");
  await composer.fill("");
  await expect(livingBrain).toHaveAttribute("data-state", "idle");

  await livingBrain.click();
  await expect(page.locator("body")).toContainText("기억 사이의 연결을 살펴보세요");
  await expect(page.locator("[data-testid='brain-cytoscape']")).toBeVisible();
  await expect(page.getByPlaceholder(GRAPH_SEARCH_PLACEHOLDER)).toBeVisible();
  expect(errors).toEqual([]);
});

test("compact desktop and low-height Brain homes keep primary controls on screen", async ({ page }) => {
  const errors = trackPageErrors(page);
  for (const viewport of [
    { width: 800, height: 800 },
    { width: 1366, height: 650 },
  ]) {
    await page.setViewportSize(viewport);
    await openBrain(page);

    const overflow = await page.evaluate(() => ({
      horizontal: document.documentElement.scrollWidth - document.documentElement.clientWidth,
      vertical: document.documentElement.scrollHeight - document.documentElement.clientHeight,
    }));
    expect(overflow.horizontal).toBeLessThanOrEqual(1);
    expect(overflow.vertical).toBeLessThanOrEqual(1);

    for (const critical of [
      page.getByTestId("brain-knowledge-flow").getByTestId("living-brain"),
      page.getByTestId("brain-ingestion-dock"),
      page.getByPlaceholder("질문하거나, 자료를 붙여 넣거나, 할 일을 적어보세요"),
    ]) {
      await expect(critical).toBeVisible();
      await expect(critical).toBeInViewport({ ratio: 0.9 });
    }
  }
  expect(errors).toEqual([]);
});

test("sources visibly enter Brain and memory-grounded automation stays user-triggered", async ({ page }) => {
  const errors = trackPageErrors(page);
  await openBrain(page);
  const livingBrain = page.getByTestId("brain-knowledge-flow").getByTestId("living-brain");
  const fileInput = page.locator(".brain-ingestion-dock input[type='file']");

  const idleBorder = await fileInput.evaluate((element) => {
    const action = element.closest("label.brain-ingestion-dock-action");
    return action ? getComputedStyle(action).borderColor : "";
  });
  await fileInput.focus();
  const focusedFileAction = await fileInput.evaluate((element) => {
    const action = element.closest("label.brain-ingestion-dock-action");
    return {
      focusWithin: action?.matches(":focus-within") ?? false,
      border: action ? getComputedStyle(action).borderColor : "",
    };
  });
  expect(focusedFileAction.focusWithin).toBe(true);
  expect(focusedFileAction.border).not.toBe(idleBorder);

  await fileInput.setInputFiles({
    name: "project-memory.md",
    mimeType: "text/markdown",
    buffer: Buffer.from("Project Atlas decision: keep all retrieval local."),
  });

  await expect(livingBrain).toHaveAttribute("data-state", "synthesizing");
  await expect(page.locator(".brain-ingestion-dock-action").filter({ hasText: "파일" })).toHaveClass(/is-emerged|is-complete/);

  await openInsightsShelf(page);
  await page.getByRole("button", { name: /검토 작업으로 저장/ }).click();
  await expect(page.locator(".brain-automation-activity")).toContainText("완료");
  expect(errors).toEqual([]);
});

test("an empty web capture is not presented as new Brain knowledge", async ({ page }) => {
  const errors = trackPageErrors(page);
  await page.route("**/api/browser/read-url", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({
      status: "empty",
      source_type: "web_url",
      detail: "No readable text was extracted from the page.",
    }),
  }));
  await openBrain(page);

  await page.getByRole("button", { name: "웹", exact: true }).click();
  const webInput = page.getByPlaceholder("https://...");
  await webInput.fill("https://blank.example");
  await webInput.press("Enter");

  await expect(page.locator("body")).toContainText("웹 페이지를 넣지 못했습니다");
  await expect(page.locator(".brain-ingestion-dock-action").filter({ hasText: "웹" })).toHaveClass(/is-failed/);
  expect(errors).toEqual([]);
});

test("ok:false core responses render an unavailable error instead of a quiet empty Brain", async ({ page }) => {
  const errors = trackPageErrors(page);
  await page.route("**/api/memory/manager", (route) => route.fulfill({
    status: 503,
    contentType: "application/json",
    body: JSON.stringify({ detail: "memory service offline" }),
  }));

  await openBrain(page);

  const banner = page.getByTestId("service-unavailable-banner");
  await expect(banner).toBeVisible();
  await expect(banner).toContainText("로컬 Lattice 서비스를 사용할 수 없습니다");
  await expect(banner).toContainText("memory service offline");
  await expect(banner).toContainText("빈 Brain으로 표시하지 않았습니다");
  expect(errors).toEqual([]);
});

test("memory rings peek previews a layer without leaving home", async ({ page }) => {
  const errors = trackPageErrors(page);
  await openBrain(page);

  // The detailed memory visualization stays out of the primary home flow,
  // then remains fully interactive when the user asks to see it.
  // The shelf's OWN toggle — it now contains nested collapsibles (the
  // knowledge garden), so a descendant-wide `summary` match is ambiguous.
  await page.getByTestId("brain-insights-shelf").locator("> summary").click();
  const topicsChip = page.locator(".ring-label-bottom");
  await expect(topicsChip).toContainText("주제");
  await topicsChip.click();
  const peek = page.locator("#brain-ring-peek");
  await expect(peek).toBeVisible();
  await expect(peek).toContainText("주제");

  // Escape closes the peek and keeps the user on the home surface.
  await page.keyboard.press("Escape");
  await expect(peek).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "말하고, 넣으면, Brain이 연결합니다." })).toBeVisible();

  // "Open this layer" from the graph ring hands off to the memory graph view.
  await page.locator(".ring-label-right").click();
  await page.getByRole("button", { name: "이 층 자세히 보기" }).click();
  await expect(page.locator("[data-testid='brain-cytoscape']")).toBeVisible();
  expect(errors).toEqual([]);
});

test("memory opens with search and can reveal the connections map", async ({ page }) => {
  const errors = trackPageErrors(page);
  await openBrain(page);
  await page.getByRole("link", { name: "기억", exact: true }).click();
  await expect(page).toHaveURL(/#\/hybrid-search$/);
  await expect(page.getByRole("heading", { name: "기억에서 찾아보세요." })).toBeVisible();
  await page.getByRole("tab", { name: "연결 지도" }).click();
  await expect(page).toHaveURL(/#\/knowledge-graph$/);
  await expect(page.locator("[data-testid='brain-cytoscape']")).toBeVisible();

  await page.getByPlaceholder(GRAPH_SEARCH_PLACEHOLDER).fill("workspace");
  await expect(page.locator("body")).toContainText("Workspace Health");

  await page.getByRole("link", { name: "대화", exact: true }).click();
  await expect(page.getByRole("heading", { name: "말하고, 넣으면, Brain이 연결합니다." })).toBeVisible();
  await expect(page.locator("[data-testid='brain-cytoscape']")).toHaveCount(0);
  expect(errors).toEqual([]);
});

test("conversation keeps the Brain alive while chat streams", async ({ page }) => {
  const errors = trackPageErrors(page);
  await openBrain(page);

  await expect(page.getByRole("button", { name: "메뉴 열기" })).toBeVisible();
  await expect(page.getByRole("link", { name: "자료", exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: "기억", exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: "작업", exact: true })).toBeVisible();
  await openShellMenu(page);
  await expect(page.getByRole("link", { name: "AI 모델" })).toBeVisible();
  await expect(page.getByRole("link", { name: "설정" })).toBeVisible();
  await page.getByRole("button", { name: "메뉴 닫기" }).last().click();
  await expect(page.locator("section[aria-label='Brain Chat Home']")).toBeVisible();
  await expect(page.getByRole("heading", { name: "말하고, 넣으면, Brain이 연결합니다." })).toBeVisible();
  await expect(page.locator("body")).toContainText("Brain 그림을 누르면 기억 지도를 볼 수 있어요.");
  await expect(page.locator("section[aria-label='제품 상태와 다음 행동']")).toHaveCount(0);
  await expect(page.locator("[aria-label='Brain 깊이 진행 상태']")).toHaveCount(0);
  const composer = page.getByPlaceholder("질문하거나, 자료를 붙여 넣거나, 할 일을 적어보세요");
  await expect(page.getByRole("button", { name: /초점 정리/ })).toBeVisible();

  // The shelf's OWN toggle — it now contains nested collapsibles (the
  // knowledge garden), so a descendant-wide `summary` match is ambiguous.
  await page.getByTestId("brain-insights-shelf").locator("> summary").click();
  await expect(page.locator(".brain-home-insights-content")).toBeVisible();
  await expect(page.locator("section[aria-label='제품 상태와 다음 행동']")).toHaveCount(0);
  await expect(page.locator("body")).toContainText("Brain 한눈에 보기");
  await expect(page.locator("body")).toContainText("Brain 준비도");
  await expect(page.locator("section[aria-label='내 Brain 돌보기']")).toBeVisible();
  await expect(page.getByRole("button", { name: /내보내기/ })).toHaveCount(0);
  await page.getByRole("button", { name: /내 Brain 돌보기/ }).click();
  await expect(page.getByRole("button", { name: /내보내기/ })).toBeVisible();
  await expect(page.getByRole("button", { name: /백업/ })).toBeVisible();
  await expect(page.getByRole("button", { name: /복원 미리보기/ })).toBeVisible();

  await composer.fill("How does hybrid search rank results?");
  await page.getByRole("button", { name: "보내기", exact: true }).click();
  await expect(page.locator("body")).toContainText("Hybrid retrieval");
  await expect(page.locator("body")).toContainText("기억에 저장됨");
  await expect(page.locator("body")).toContainText("출처와 함께 나중에 다시 불러올 수 있습니다.");
  await expect(page.locator(".brain-conversation-trace")).toContainText("내 지식으로 들어왔습니다");
  await expect(page.locator("body")).toContainText("Lattice Brain");
  expect(errors).toEqual([]);
});

test("the Brain pulses without navigating away while an answer is streaming", async ({ page }) => {
  const errors = trackPageErrors(page);
  await page.route("**/chat", async (route) => {
    if (route.request().method() !== "POST") return route.continue();
    await new Promise((resolve) => setTimeout(resolve, 650));
    await route.fulfill({
      status: 200,
      contentType: "text/event-stream; charset=utf-8",
      body: `data: ${JSON.stringify({ chunk: "A grounded answer.", model: "mock-local-model" })}\n\ndata: [DONE]\n\n`,
    });
  });
  await openBrain(page);

  const composer = page.getByPlaceholder("질문하거나, 자료를 붙여 넣거나, 할 일을 적어보세요");
  await composer.fill("기억을 바탕으로 답해줘");
  await page.getByRole("button", { name: "보내기", exact: true }).click();

  const livingBrain = page.locator(".brain-header-presence").getByTestId("living-brain");
  await expect(livingBrain).toHaveAttribute("data-state", "thinking");
  const urlBeforeClick = page.url();
  await livingBrain.click();
  await expect(livingBrain).toHaveClass(/pulse/);
  expect(page.url()).toBe(urlBeforeClick);
  await expect(page.locator("body")).toContainText("A grounded answer.");
  expect(errors).toEqual([]);
});

test("admin console is separated from the user Brain surface", async ({ page }) => {
  const errors = trackPageErrors(page);
  await openBrain(page);

  await expect(page.locator("main[aria-label='Lattice Brain']")).toBeVisible();
  await expect(page.locator("main.admin-console")).toHaveCount(0);

  await openShellMenu(page);
  await page.getByRole("button", { name: "관리자 콘솔 열기" }).click();
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
  await expect(page.locator("body")).toContainText(`Approve ${appVersion} product readiness evidence`);
  await expect(page.locator("body")).toContainText("Review generated screenshots");
  await expect(page.locator("body")).not.toContainText("not valid JSON");
  await expect(page.locator("body")).not.toContainText("Unexpected token");
  expect(errors).toEqual([]);
});

test("memory automation can be reviewed and explicitly enabled", async ({ page }) => {
  const errors = trackPageErrors(page);
  await openBrain(page);

  await page.getByRole("link", { name: "작업", exact: true }).click();
  await page.getByRole("tab", { name: "레시피" }).click();
  await expect(page.getByText("기억 기반 자동화", { exact: true })).toBeVisible();

  await page.getByRole("button", { name: "검토 가능한 자동화 초안 만들기" }).click();
  await expect(page.getByRole("button", { name: "이 기억 자동화 활성화" })).toBeVisible();
  await page.getByRole("button", { name: "이 기억 자동화 활성화" }).click();
  await expect(page.getByRole("button", { name: "✓ 기억 자동화 작동 중" })).toBeDisabled();
  await expect(page.locator("body")).toContainText("새 기억이 들어오면 결과를 검토함에 초안으로 만듭니다.");
  expect(errors).toEqual([]);
});

test("folder picker imports browser-selected folder files", async ({ page }) => {
  const errors = trackPageErrors(page);
  await bypassProductFlow(page);
  await page.addInitScript(() => {
    window.showDirectoryPicker = async () => ({
      kind: "directory",
      name: "Mock Folder",
      async *values() {
        yield {
          kind: "file",
          name: "folder-note.md",
          getFile: async () => new File(["# Folder note"], "folder-note.md", { type: "text/markdown" }),
        };
      },
    });
  });
  await page.goto("/app#/my-computer");

  await expect(page.locator("body")).toContainText("폴더 연결");
  await page.getByRole("button", { name: "폴더 선택" }).click();
  await expect(page.locator("body")).toContainText("folder-note.md");
  await expect(page.locator("body")).toContainText("Mock Folder 파일을 Brain에 넣었습니다.");
  await expect(page.locator("body")).not.toContainText("폴더 선택 창을 열 수 없습니다");
  expect(errors).toEqual([]);
});

test("mobile Brain surface has no horizontal overflow", async ({ page }) => {
  const errors = trackPageErrors(page);
  await page.setViewportSize({ width: 390, height: 780 });
  await openBrain(page, { mode: "basic" });

  const overflow = await page.evaluate(() => ({
    horizontal: document.documentElement.scrollWidth - document.documentElement.clientWidth,
    vertical: document.documentElement.scrollHeight - document.documentElement.clientHeight,
  }));
  expect(overflow.horizontal).toBeLessThanOrEqual(1);
  expect(overflow.vertical).toBeLessThanOrEqual(1);
  await expect(page.getByRole("heading", { name: "말하고, 넣으면, Brain이 연결합니다." })).toBeVisible();
  await expect(page.getByTestId("brain-knowledge-flow").getByTestId("living-brain")).toBeVisible();
  await expect(page.getByTestId("brain-ingestion-dock")).toBeVisible();
  await expect(page.getByPlaceholder("질문하거나, 자료를 붙여 넣거나, 할 일을 적어보세요")).toBeVisible();

  await openInsightsShelf(page);
  await expect(page.getByRole("button", { name: /검토 작업으로 저장/ })).toBeVisible();
  await page.getByRole("button", { name: "Brain 보조 패널 닫기" }).first().click();
  await expect(page.getByRole("button", { name: /검토 작업으로 저장/ })).toBeHidden();

  await page.getByTestId("brain-history-shelf").locator("summary").click();
  await expect(page.locator("section[aria-label='지난 대화 목록']")).toBeVisible();
  await page.getByRole("button", { name: "Brain 보조 패널 닫기" }).click();
  await expect(page.locator("section[aria-label='지난 대화 목록']")).toBeHidden();
  expect(errors).toEqual([]);
});

test("mobile keeps a single memory-grounded action reachable", async ({ page }) => {
  const errors = trackPageErrors(page);
  await page.setViewportSize({ width: 390, height: 780 });
  await page.route("**/api/memory/brain-brief*", async (route) => {
    const response = await route.fetch();
    const brief = await response.json();
    await route.fulfill({
      response,
      json: { ...brief, proactive_actions: brief.proactive_actions.slice(0, 1) },
    });
  });
  await openBrain(page, { mode: "basic" });

  await expect(page.getByTestId("brain-automation-more")).toHaveCount(0);
  await openInsightsShelf(page);
  const primaryAction = page.getByRole("button", { name: /근거 검토/ });
  await expect(primaryAction).toBeVisible();
  await primaryAction.scrollIntoViewIfNeeded();
  await expect(primaryAction).toBeInViewport({ ratio: 0.95 });
  expect(errors).toEqual([]);
});

test("legacy entry URLs still arrive at the Brain app", async ({ page }) => {
  await bypassProductFlow(page);
  await page.goto("/chat");
  await expect(page).toHaveURL(/\/app#\/chat$/);
  await expect(page.getByRole("heading", { name: "말하고, 넣으면, Brain이 연결합니다." })).toBeVisible();

  await page.goto("/graph");
  await expect(page).toHaveURL(/\/app#\/knowledge-graph$/);
  await expect(page.locator("body")).toContainText("기억 사이의 연결을 살펴보세요");
});

test("past conversations resume with markdown rendering and delete inline", async ({ page }) => {
  const errors = trackPageErrors(page);
  await openBrain(page);

  await page.getByTestId("brain-history-shelf").locator("summary").click();
  await expect(page.locator("section[aria-label='지난 대화 목록']")).toBeVisible();
  await expect(page.locator("body")).toContainText("지난 대화");
  await expect(page.locator("body")).toContainText("How hybrid search ranks");

  await page.getByRole("button", { name: /대화 이어가기: How hybrid search ranks/ }).click();
  await expect(page.locator("body")).toContainText("How does hybrid search rank results?");
  await expect(page.locator(".brain-message.assistant .brain-md strong")).toContainText("reciprocal-rank fusion");
  await expect(page.locator("body")).not.toContainText("**reciprocal-rank fusion**");

  await expect(page.getByRole("button", { name: "응답 전체 복사" })).toBeVisible();
  await page.getByRole("button", { name: "마지막 질문에 다시 답변 받기" }).click();
  await expect(page.locator("body")).toContainText("Hybrid retrieval");

  await page.getByRole("button", { name: "새 대화" }).click();
  await page.getByTestId("brain-history-shelf").locator("summary").click();
  await expect(page.locator("section[aria-label='지난 대화 목록']")).toBeVisible();

  await page.getByRole("button", { name: "대화 삭제: Reindex the workspace" }).click();
  await page.getByRole("button", { name: "한 번 더 누르면 삭제" }).click();
  await expect(page.locator("body")).toContainText("대화를 삭제했습니다.");
  expect(errors).toEqual([]);
});
