const { test, expect } = require("@playwright/test");
const { version: appVersion } = require("../../package.json");
const {
  GRAPH_SEARCH_PLACEHOLDER,
  trackPageErrors,
  bypassProductFlow,
  openInsightsShelf,
  openAttachMenu,
  openBrain,
  openShellMenu,
} = require("./v3_helpers");

// The surfaces a person moves between once the Brain exists: capture, memory,
// conversation, review, the model library, and the two dials that decide what
// leaves this machine.

test("sources visibly enter Brain and memory-grounded automation stays user-triggered", async ({ page }) => {
  const errors = trackPageErrors(page);
  await openBrain(page);
  const livingBrain = page.getByTestId("brain-knowledge-flow").getByTestId("living-brain");
  await openAttachMenu(page);
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

  await openAttachMenu(page);
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

  // The detailed memory visualization stays out of the primary home flow —
  // it lives in the dock's 기억 지도 drawer — then remains fully interactive
  // when the user asks to see it.
  await page.getByTestId("brain-dock-map").click();
  await expect(page.getByTestId("brain-home-drawer")).toBeVisible();
  const topicsChip = page.locator(".ring-label-bottom");
  await expect(topicsChip).toContainText("주제");
  await topicsChip.click();
  const peek = page.locator("#brain-ring-peek");
  await expect(peek).toBeVisible();
  await expect(peek).toContainText("주제");

  // Escape closes the peek and keeps the user on the home surface.
  await page.keyboard.press("Escape");
  await expect(peek).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "Brain에게 물어보세요." })).toBeVisible();

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
  // The map is a subview, not a peer of search: it is absent from the tablist,
  // reached from a named secondary target, and it comes with a way back. Two
  // tabs, not three, is the load-bearing part — a reordered three-tab strip
  // still offers a Cytoscape canvas as a first-class choice to a newcomer.
  await expect(page.getByRole("tab")).toHaveCount(2);
  await expect(page.getByRole("tab", { name: "연결 지도" })).toHaveCount(0);
  await page.getByTestId("open-connections-map").click();
  await expect(page).toHaveURL(/#\/knowledge-graph$/);
  await expect(page.locator("[data-testid='brain-cytoscape']")).toBeVisible();
  await expect(page.getByRole("tab")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "기억 화면으로 돌아가기" })).toBeVisible();

  await page.getByPlaceholder(GRAPH_SEARCH_PLACEHOLDER).fill("workspace");
  await expect(page.locator("body")).toContainText("Workspace Health");

  await page.getByRole("link", { name: "대화", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Brain에게 물어보세요." })).toBeVisible();
  await expect(page.locator("[data-testid='brain-cytoscape']")).toHaveCount(0);
  expect(errors).toEqual([]);
});

test("conversation keeps the Brain alive while chat streams", async ({ page }) => {
  const errors = trackPageErrors(page);
  await openBrain(page);

  await expect(page.getByRole("button", { name: "메뉴 열기" })).toBeVisible();
  await expect(page.getByRole("link", { name: "자료", exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: "기억", exact: true })).toBeVisible();

  // The three management destinations were lifted out of the menu into the
  // topbar, so they must be reachable without opening anything. Both landmarks
  // carry the same three links, which is why each side is scoped by name.
  const utilityNav = page.getByRole("navigation", { name: "관리 화면 이동" });
  await expect(utilityNav.getByRole("link", { name: "작업", exact: true })).toBeVisible();
  await expect(utilityNav.getByRole("link", { name: "AI 모델" })).toBeVisible();
  await expect(utilityNav.getByRole("link", { name: "설정" })).toBeVisible();

  // On desktop viewports, management links are directly visible in topbar utility nav.
  // The popover menu deduplicates these links on desktop viewports.
  await openShellMenu(page);
  const shellMenu = page.getByRole("dialog", { name: "더보기" });
  await expect(shellMenu).toBeVisible();
  await page.getByRole("button", { name: "메뉴 닫기" }).last().click();
  await expect(page.locator("section[aria-label='Brain Chat Home']")).toBeVisible();
  await expect(page.getByRole("heading", { name: "Brain에게 물어보세요." })).toBeVisible();
  // The memory-map hint moved off the reading line into the stats badge's
  // popover: hover (or click) is the gesture that asks for it.
  await expect(page.locator("body")).not.toContainText("Brain 그림을 누르면 기억 지도를 볼 수 있어요.");
  await page.locator(".brain-hero-stats-badge").click();
  await expect(page.getByTestId("brain-hero-stats-popover")).toBeVisible();
  await expect(page.getByTestId("brain-hero-stats-popover")).toContainText("Brain 그림을 누르면 기억 지도를 볼 수 있어요.");
  await page.keyboard.press("Escape");
  await expect(page.getByTestId("brain-hero-stats-popover")).toHaveCount(0);
  await expect(page.locator("section[aria-label='제품 상태와 다음 행동']")).toHaveCount(0);
  await expect(page.locator("[aria-label='Brain 깊이 진행 상태']")).toHaveCount(0);
  const composer = page.getByPlaceholder("질문하거나, 자료를 붙여 넣거나, 할 일을 적어보세요");
  await expect(page.getByRole("button", { name: /초점 정리/ })).toBeVisible();

  await openInsightsShelf(page);
  await expect(page.locator(".brain-home-drawer-body")).toBeVisible();
  await expect(page.locator("section[aria-label='제품 상태와 다음 행동']")).toHaveCount(0);
  await expect(page.locator("body")).toContainText("Brain 한눈에 보기");
  await expect(page.locator("body")).toContainText("Brain 준비도");
  await expect(page.locator("section[aria-label='내 Brain 돌보기']")).toBeVisible();
  await expect(page.getByRole("button", { name: /내보내기/ })).toHaveCount(0);
  await page.getByRole("button", { name: /내 Brain 돌보기/ }).click();
  await expect(page.getByRole("button", { name: /내보내기/ })).toBeVisible();
  await expect(page.getByRole("button", { name: /백업/ })).toBeVisible();
  await expect(page.getByRole("button", { name: /복원 미리보기/ })).toBeVisible();
  // The drawer is a modal surface; leave it before speaking to the composer.
  await page.getByRole("button", { name: "Brain 보조 패널 닫기" }).click();
  await expect(page.getByTestId("brain-home-drawer")).toHaveCount(0);

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
  // Korean UI now titles this screen 관리자 설정 — the English string was the one
  // untranslated heading left in the product. Matched either way, like the
  // assertions below it, so the test pins the screen and not the locale.
  await expect(page.locator("body")).toContainText(/Admin Console|관리자 설정/);
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

test("capture leads with one place to add material, not four equal tabs", async ({ page }) => {
  const errors = trackPageErrors(page);
  await bypassProductFlow(page, { mode: "basic" });
  await page.goto("/app#/capture");

  // Adding something is one action with three shapes, inside one station. It
  // used to be three page-level tabs ranked equal with the indexer's status
  // page, so "connect a folder" — the most valuable thing here — was two tabs
  // deep and looked like a different screen.
  const station = page.locator(".capture-station");
  await expect(station).toBeVisible();
  await expect(page.getByRole("tab")).toHaveCount(0);
  for (const name of ["파일 올리기", "폴더 연결하기", "웹페이지 저장하기"]) {
    await expect(station.getByRole("button", { name })).toBeVisible();
  }
  await expect(station.getByTestId("capture-method-files")).toHaveAttribute("aria-pressed", "true");

  // Progress and the material already added drop below it, side by side, and
  // are on screen without choosing a tab first.
  await expect(page.getByRole("list", { name: "자료가 기억이 되는 3단계" })).toBeVisible();
  await expect(page.locator("body")).toContainText("업로드된 문서");

  // Each way in still has its own deep link and its own controls.
  await station.getByTestId("capture-method-local").click();
  await expect(page).toHaveURL(/#\/my-computer$/);
  await expect(station.getByRole("button", { name: "폴더 선택" })).toBeVisible();
  await station.getByTestId("capture-method-browser").click();
  await expect(page).toHaveURL(/#\/capture-browser$/);
  await expect(station.getByRole("button", { name: "스캔하고 저장" })).toBeVisible();
  expect(errors).toEqual([]);
});

test("the model library answers which model is running before anything else", async ({ page }) => {
  const errors = trackPageErrors(page);
  await bypassProductFlow(page, { mode: "basic" });
  await page.goto("/app#/models");

  // The question that brings people here was answered by a stat cell partway
  // down a catalogue. It is the first block on the page now — above the tabs,
  // so it holds whichever tab is open.
  const active = page.getByTestId("library-active-model");
  await expect(active).toBeVisible();
  await expect(active).toContainText("지금 작동 중인 모델");

  const order = await page.evaluate(() => {
    const card = document.querySelector("[data-testid='library-active-model']");
    const tablist = document.querySelector("[role='tablist']");
    if (!card) return null;
    // No tablist in plain mode (only one tab survives); the card must still be
    // the first thing under the page heading.
    if (!tablist) return { cardBeforeTabs: true };
    return { cardBeforeTabs: card.compareDocumentPosition(tablist) & Node.DOCUMENT_POSITION_FOLLOWING ? true : false };
  });
  expect(order).not.toBeNull();
  expect(order.cardBeforeTabs).toBe(true);

  // A model name, never the registry coordinate it is stored under.
  await expect(active).not.toContainText("mlx-community/");
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

  // The bottom bar is sized from its item count. Trimming the primary nav used
  // to leave a hard-coded fifth column empty, bunching the tabs to one side.
  const bottomBar = await page.evaluate(() => {
    const nav = document.querySelector(".brain-mobile-nav");
    if (!nav) return null;
    const items = Array.from(nav.children).map((child) => child.getBoundingClientRect());
    return { navWidth: nav.getBoundingClientRect().width, right: items[items.length - 1].right, count: items.length };
  });
  // A leftover column would strand the last tab a full column short of the
  // edge; the real gap here is just the bar's own padding. Five since 11.3.0:
  // the four everyday destinations (대화 · 자료 · 기억 · 연대기) plus 더보기.
  expect(bottomBar.count).toBe(5);
  expect(bottomBar.navWidth - bottomBar.right).toBeLessThan(bottomBar.navWidth / bottomBar.count / 2);

  await expect(page.getByRole("heading", { name: "Brain에게 물어보세요." })).toBeVisible();
  await expect(page.getByTestId("brain-knowledge-flow").getByTestId("living-brain")).toBeVisible();
  await expect(page.getByTestId("brain-attach-toggle")).toBeVisible();
  await expect(page.getByPlaceholder("질문하거나, 자료를 붙여 넣거나, 할 일을 적어보세요")).toBeVisible();

  await openInsightsShelf(page);
  await expect(page.getByRole("button", { name: /검토 작업으로 저장/ })).toBeVisible();
  await page.getByRole("button", { name: "Brain 보조 패널 닫기" }).first().click();
  await expect(page.getByRole("button", { name: /검토 작업으로 저장/ })).toBeHidden();

  await page.getByTestId("brain-dock-conversations").click();
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
  await expect(page.getByRole("heading", { name: "Brain에게 물어보세요." })).toBeVisible();

  await page.goto("/graph");
  await expect(page).toHaveURL(/\/app#\/knowledge-graph$/);
  await expect(page.locator("body")).toContainText("기억 사이의 연결을 살펴보세요");
});

test("past conversations resume with markdown rendering and delete inline", async ({ page }) => {
  const errors = trackPageErrors(page);
  await openBrain(page);

  await page.getByTestId("brain-dock-conversations").click();
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
  await page.getByTestId("brain-dock-conversations").click();
  await expect(page.locator("section[aria-label='지난 대화 목록']")).toBeVisible();

  await page.getByRole("button", { name: "대화 삭제: Reindex the workspace" }).click();
  await page.getByRole("button", { name: "한 번 더 누르면 삭제" }).click();
  await expect(page.locator("body")).toContainText("대화를 삭제했습니다.");
  expect(errors).toEqual([]);
});

// The network boundary contracts shipped in 10.1.0 with no way to reach them
// 11.2.0: every opt-in feature used to be an environment variable and a
// restart. The switchboard is the reachable half — a dock drawer, not a card,
// because the home canvas is a one-viewport contract. What this asserts is the
// part a screenshot cannot: that the panel renders *the server's* catalog,
// including the honesty that catalog carries (where a value came from, which
// option is not installed here, which switch sends knowledge off the machine).
test("the 기능 drawer turns opt-in features on without leaving home", async ({ page }) => {
  const errors = trackPageErrors(page);
  await openBrain(page);

  await page.getByTestId("brain-dock-features").click();
  const drawer = page.getByTestId("brain-home-drawer");
  await expect(drawer).toBeVisible();
  await expect(drawer).toHaveAttribute("aria-modal", "true");
  const panel = page.locator("section.brain-features-panel");
  await expect(panel).toBeVisible();

  // Rendered from /api/features: labels, order, and kinds are all the server's.
  await expect(page.getByTestId("feature-row-allow_multimodal")).toContainText("사진·녹음도 기억하기");
  await expect(page.locator(".brain-feature-row")).toHaveCount(9);
  const multimodal = page.getByTestId("feature-switch-allow_multimodal");
  await expect(multimodal).toHaveAttribute("role", "switch");
  await expect(multimodal).toHaveAttribute("aria-checked", "true");

  // The three honesty affordances, each on the row it belongs to.
  await expect(page.getByTestId("feature-row-vault_watch")).toContainText("설정으로 켜짐");
  await expect(page.getByTestId("feature-row-brain_network")).toContainText("이 컴퓨터 밖으로");
  await expect(page.getByTestId("feature-row-video_ingest")).toHaveClass(/is-child/);
  const hnsw = page.getByTestId("feature-choice-vector_backend-hnsw");
  await expect(hnsw).toBeDisabled();
  await expect(hnsw).toContainText("설치 필요");

  // Jade marks state and nothing else: only the switches that are on carry it.
  const litTracks = await page.evaluate(() => Array.from(
    document.querySelectorAll(".brain-feature-switch"),
  ).map((node) => ({
    on: node.getAttribute("aria-checked") === "true",
    background: getComputedStyle(node.querySelector(".brain-feature-switch-track")).backgroundColor,
  })));
  const onColours = new Set(litTracks.filter((row) => row.on).map((row) => row.background));
  const offColours = new Set(litTracks.filter((row) => !row.on).map((row) => row.background));
  expect(onColours.size).toBe(1);
  expect([...onColours].some((colour) => offColours.has(colour))).toBe(false);

  // A switch moves under the finger and the panel says so out loud.
  await page.getByTestId("feature-switch-brain_network").click();
  await expect(page.getByTestId("feature-switch-brain_network")).toHaveAttribute("aria-checked", "true");
  await expect(panel.locator(".brain-features-notice")).toHaveText("바뀌었습니다.");

  // Focus-trapped modal: Escape returns to the home it never left.
  await page.keyboard.press("Escape");
  await expect(page.getByTestId("brain-home-drawer")).toHaveCount(0);
  await expect(page.getByTestId("brain-home-station")).toBeVisible();
  expect(errors).toEqual([]);
});

test("the 기능 drawer stays usable on a phone", async ({ page }) => {
  const errors = trackPageErrors(page);
  await page.setViewportSize({ width: 390, height: 780 });
  await openBrain(page, { mode: "basic" });

  await page.getByTestId("brain-dock-features").click();
  await expect(page.getByTestId("brain-home-drawer")).toBeVisible();
  await expect(page.getByTestId("feature-switch-allow_multimodal")).toBeVisible();

  // No sideways scroll anywhere, and the switch is a real touch target rather
  // than a decoration squeezed off the edge by the sentence next to it.
  const overflow = await page.evaluate(() => ({
    page: document.documentElement.scrollWidth - document.documentElement.clientWidth,
    drawer: (() => {
      const body = document.querySelector(".brain-home-drawer-body");
      return body.scrollWidth - body.clientWidth;
    })(),
  }));
  expect(overflow.page).toBeLessThanOrEqual(1);
  expect(overflow.drawer).toBeLessThanOrEqual(1);

  const box = await page.getByTestId("feature-switch-allow_multimodal").boundingBox();
  expect(box.width).toBeGreaterThan(44);
  expect(box.height).toBeGreaterThanOrEqual(24);
  // The choice pills wrap under their sentence instead of crushing it.
  const pills = await page.getByTestId("feature-choices-vector_backend").boundingBox();
  const row = await page.getByTestId("feature-row-vector_backend").boundingBox();
  expect(pills.x + pills.width).toBeLessThanOrEqual(row.x + row.width + 1);
  expect(errors).toEqual([]);
});

// from the app — the dial existed only for whoever called the API by hand.
// This asserts the control is actually on the settings screen and that it
// refuses to send a cloud switch the server would reject.
test("the network boundary dial is reachable and gates cloud behind an acknowledgement", async ({ page }) => {
  const errors = trackPageErrors(page);
  await openBrain(page);
  await page.goto("/app#/system");
  // System opens on the account tab; the dials live under 환경설정.
  await page.getByRole("tab", { name: "환경설정" }).click();

  const panel = page.getByTestId("network-boundary-panel");
  await expect(panel).toBeVisible();
  await expect(page.getByTestId("network-boundary-active")).toHaveText("로컬만");

  // Both server-served modes render; nothing is hardcoded in the client.
  await expect(page.getByTestId("network-boundary-option-local_only")).toBeVisible();
  await expect(page.getByTestId("network-boundary-option-cloud_allowed")).toBeVisible();

  // While local_only is in force there is nothing to configure about cloud
  // write-back, so the policy switches stay out of the way.
  await expect(page.getByTestId("network-boundary-policy")).toHaveCount(0);

  // Choosing cloud must not be one click: the server refuses without an ack,
  // so the button stays disabled until the box is ticked.
  await page.getByTestId("network-boundary-option-cloud_allowed").click();
  await expect(page.getByTestId("network-boundary-apply")).toBeDisabled();
  await page.getByTestId("network-boundary-ack").check();
  await expect(page.getByTestId("network-boundary-apply")).toBeEnabled();

  expect(errors).toEqual([]);
});
