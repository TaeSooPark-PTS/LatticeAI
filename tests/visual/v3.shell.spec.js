const { test, expect } = require("@playwright/test");
const { trackPageErrors, bypassProductFlow, openBrain } = require("./v3_helpers");


const PLAIN_MODE_ROUTES = [
  { hash: "#/capture", ready: "h1.page-title" },
  { hash: "#/models", ready: "h1.page-title" },
  { hash: "#/settings", ready: "h1.page-title" },
  { hash: "#/hybrid-search", ready: "h1" },
  { hash: "#/knowledge-graph", ready: "[data-testid='brain-cytoscape']" },
  { hash: "#/review", ready: "h1.page-title" },
  { hash: "#/agents", ready: "h1.page-title" },
  { hash: "#/pipeline", ready: "h1.page-title" },
  { hash: "#/runs", ready: "h1.page-title" },
  { hash: "#/memory", ready: "h1" },
  { hash: "#/chronicle", ready: "h1.page-title" },
];

const ENGINE_VOCABULARY = [
  "파싱", "임베딩", "인덱싱", "벡터", "스키마",
  "awaiting_approval", "retried_ok", "schema_version",
  "graph_schema_version", "tick_seconds", "DSN", "Postgres", "sqlite",
];

test("the reorganised shell opens each screen on its promoted panel", async ({ page }) => {
  const errors = trackPageErrors(page);
  await bypassProductFlow(page, { mode: "basic" });
  await page.goto("/app#/brain");

  await page.getByRole("navigation", { name: "관리 화면 이동" })
    .getByRole("link", { name: "작업", exact: true }).click();
  await expect(page).toHaveURL(/#\/review$/);
  await expect(page.getByRole("tab", { name: "검토함" })).toHaveAttribute("aria-selected", "true");

  await page.goto("/app#/settings");
  await expect(page.locator("h1.page-title")).toBeVisible();
  const groups = page.getByTestId("system-tab-groups");
  await expect(groups.getByRole("tablist")).toHaveCount(3);
  for (const heading of ["나와 작업공간", "내 데이터 보관", "동작 방식과 연결"]) {
    await expect(groups.getByText(heading, { exact: true })).toBeVisible();
  }
  expect(errors).toEqual([]);
});

test("the command palette reaches the same Work screen the shell link opens", async ({ page }) => {
  const errors = trackPageErrors(page);
  await bypassProductFlow(page, { mode: "basic" });

  for (const [entry, expectedHash] of [["작업", "#/review"], ["검토함", "#/act/review"]]) {
    await page.goto("/app#/brain");
    await page.keyboard.press("ControlOrMeta+k");
    const palette = page.getByRole("dialog", { name: /명령|Command/ });
    await expect(palette).toBeVisible();
    await palette.getByRole("option", { name: entry, exact: true }).click();

    await expect(page).toHaveURL(new RegExp(`${expectedHash.replace("/", "\\/")}$`));
    await expect(page.getByRole("tab", { name: "검토함" })).toHaveAttribute("aria-selected", "true");
  }
  expect(errors).toEqual([]);
});

test("every navigation landmark on screen has a name of its own", async ({ page }) => {
  const errors = trackPageErrors(page);
  await bypassProductFlow(page);

  for (const width of [390, 900, 1280]) {
    await page.setViewportSize({ width, height: 800 });
    await page.goto("/app#/brain");
    await expect(page.locator(".brain-topbar")).toBeVisible();
    await page.getByRole("button", { name: "메뉴 열기" }).first().click();
    await expect(page.locator("#brain-more-popover")).toBeVisible();

    const names = await page.evaluate(() => Array.from(document.querySelectorAll("nav"))
      .filter((nav) => getComputedStyle(nav).display !== "none" && nav.getClientRects().length > 0)
      .map((nav) => nav.getAttribute("aria-label")));

    expect(names.every(Boolean), `an unnamed nav at ${width}px`).toBe(true);
    expect(new Set(names).size, `duplicate nav names ${JSON.stringify(names)} at ${width}px`)
      .toBe(names.length);

    await page.keyboard.press("Escape");
  }
  expect(errors).toEqual([]);
});

test("management links appear in exactly one place at every width", async ({ page }) => {
  const errors = trackPageErrors(page);
  await bypassProductFlow(page);

  for (const width of [390, 760, 761, 768, 800, 900, 959, 960, 1024, 1119, 1120, 1440]) {
    await page.setViewportSize({ width, height: 800 });
    await page.goto("/app#/brain");
    await expect(page.locator(".brain-topbar")).toBeVisible();

    const shell = await page.evaluate(() => {
      const topbar = document.querySelector(".brain-topbar");
      const menuButton = document.querySelector(".brain-more-button");
      const quickNav = document.querySelector(".brain-utility-quick-nav");
      const viewportWidth = document.documentElement.clientWidth;
      return {
        topbarOverflow: topbar.scrollWidth - topbar.clientWidth,
        menuButtonOverhang: menuButton
          ? Math.round(menuButton.getBoundingClientRect().right - viewportWidth)
          : 0,
        documentOverflow: document.documentElement.scrollWidth - viewportWidth,
        topbarCopyVisible: Boolean(quickNav) && getComputedStyle(quickNav).display !== "none",
      };
    });

    expect(shell.topbarOverflow, `topbar overflows at ${width}px`).toBeLessThanOrEqual(1);
    expect(shell.menuButtonOverhang, `menu button clipped at ${width}px`).toBeLessThanOrEqual(1);
    expect(shell.documentOverflow, `page scrolls sideways at ${width}px`).toBeLessThanOrEqual(1);

    await page.getByRole("button", { name: "메뉴 열기" }).first().click();
    await expect(page.locator("#brain-more-popover")).toBeVisible();
    await expect
      .poll(
        () => page.evaluate(() => {
          const panel = document.querySelector("#brain-more-popover");
          return Boolean(panel && panel.contains(document.activeElement));
        }),
        { message: `menu opened without taking focus at ${width}px` },
      )
      .toBe(true);

    const menuCopyVisible = await page.evaluate(() => {
      const section = document.querySelector("#brain-more-popover .brain-more-section");
      return Boolean(section) && getComputedStyle(section).display !== "none";
    });

    const copies = [shell.topbarCopyVisible, menuCopyVisible].filter(Boolean);
    expect(copies, `management links appear ${copies.length}× at ${width}px`).toHaveLength(1);

    await page.keyboard.press("Escape");
    await expect(page.locator("#brain-more-popover")).toHaveCount(0);
  }
  expect(errors).toEqual([]);
});

test("screens the README publishes render, and speak plainly", async ({ page }) => {
  const errors = trackPageErrors(page);
  await bypassProductFlow(page, { mode: "basic" });

  for (const { hash, ready } of PLAIN_MODE_ROUTES) {
    await page.goto(`/app${hash}`);
    await expect(page.locator(ready).first()).toBeVisible();
    await expect(page.getByTestId("service-unavailable-banner")).toHaveCount(0);

    const text = await page.locator("main, .brain-shell-content").first().innerText();
    for (const word of ENGINE_VOCABULARY) {
      expect(text, `${hash} still shows "${word}" to a plain-mode reader`).not.toContain(word);
    }
  }
  expect(errors).toEqual([]);
});

test("the material-to-memory steps are readable without a glossary", async ({ page }) => {
  const errors = trackPageErrors(page);
  await bypassProductFlow(page, { mode: "basic" });
  await page.goto("/app#/pipeline");

  const journey = page.getByRole("list", { name: "자료가 기억이 되는 3단계" });
  await expect(journey).toBeVisible();
  await expect(journey).toContainText("내용 읽기");
  await expect(journey).toContainText("뜻 파악하기");
  await expect(journey).toContainText("기억에 연결하기");
  expect(errors).toEqual([]);
});

test("the boundary panel shows which memories a question would send", async ({ page }) => {
  const errors = trackPageErrors(page);
  await openBrain(page);
  await page.goto("/app#/system");
  await page.getByRole("tab", { name: "환경설정" }).click();

  await expect(page.getByTestId("network-boundary-panel")).toBeVisible();
  await page.getByTestId("network-boundary-probe").fill("릴리스 어떻게 하지");
  await page.getByTestId("network-boundary-preview").click();

  const result = page.getByTestId("network-boundary-preview-result");
  await expect(result).toContainText("릴리스 절차 정리");
  await expect(result).toContainText("배포 전 확인 목록");
  await expect(result).toContainText("아무것도 나가지 않습니다");

  expect(errors).toEqual([]);
});
