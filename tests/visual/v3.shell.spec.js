const { test, expect } = require("@playwright/test");
const { trackPageErrors, bypassProductFlow, openBrain } = require("./v3_helpers");

// The shell itself: which screen a link opens, what the palette reaches, how
// the navigation landmarks name themselves, and whether the published screens
// speak plainly.

// `scripts/capture_release_evidence.mjs` publishes the first six of these as
// README screenshots and a GIF, and it captures them in the app's real default
// mode (`basic`) rather than in `advanced`. The rest are one click away from a
// captured frame. Two things have to hold: every screen must actually render,
// and none of them may put the engine's own vocabulary in front of a reader
// who never asked for it.
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

// Words that name the machine's job rather than the reader's. `#/runs` showed
// `awaiting_approval` and `retried_ok` on its status badges in this very mode
// before this pass, which is what makes the list load-bearing rather than
// decorative — the advanced surfaces still show every one of them on purpose.
const ENGINE_VOCABULARY = [
  "파싱", "임베딩", "인덱싱", "벡터", "스키마",
  "awaiting_approval", "retried_ok", "schema_version",
  "graph_schema_version", "tick_seconds", "DSN", "Postgres", "sqlite",
];

// Reordering a tab array changes the strip, not where the screen opens. These
// two surfaces were re-prioritised, so each is checked at the point a person
// actually arrives: by following the link, not by loading the tab directly.
test("the reorganised shell opens each screen on its promoted panel", async ({ page }) => {
  const errors = trackPageErrors(page);
  await bypassProductFlow(page, { mode: "basic" });
  await page.goto("/app#/brain");

  await page.getByRole("navigation", { name: "관리 화면 이동" })
    .getByRole("link", { name: "작업", exact: true }).click();
  await expect(page).toHaveURL(/#\/review$/);
  // What is waiting on a decision, not the goal composer that used to open here.
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

// The shell link and the palette entry carry the same word, "작업". They were
// pointed at the review inbox in two separate lists — and the palette read
// neither of them, so Cmd+K still opened the goal composer. Worse, "검토함"
// emitted `#/act/review`, a shape parseHash had no branch for, so the one
// destination this layout promotes rendered the Brain home instead.
test("the command palette reaches the same Work screen the shell link opens", async ({ page }) => {
  const errors = trackPageErrors(page);
  await bypassProductFlow(page, { mode: "basic" });

  for (const [entry, expectedHash] of [["작업", "#/review"], ["검토함", "#/act/review"]]) {
    await page.goto("/app#/brain");
    // ControlOrMeta, not Meta: the handler takes either, but CI runs Linux and
    // this suite should open the palette the way that machine's users do.
    await page.keyboard.press("ControlOrMeta+k");
    const palette = page.getByRole("dialog", { name: /명령|Command/ });
    await expect(palette).toBeVisible();
    await palette.getByRole("option", { name: entry, exact: true }).click();

    await expect(page).toHaveURL(new RegExp(`${expectedHash.replace("/", "\\/")}$`));
    // Landing on the Brain home instead is the failure this guards: it renders
    // without error, so only naming the panel catches it.
    await expect(page.getByRole("tab", { name: "검토함" })).toHaveAttribute("aria-selected", "true");
  }
  expect(errors).toEqual([]);
});

// Landmarks are how a screen-reader user skips straight to navigation. Three
// <nav>s answered to "화면 이동" — primary, bottom bar, and the one inside the
// menu — so that list read as three indistinguishable entries. The menu's copy
// holds the management links, so it shares its name with its topbar twin, which
// CSS guarantees is never on screen at the same time.
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

// The management links are one list rendered twice: in the topbar, and in the
// menu. Whoever owns "visible" for one copy has to own it for the other, or the
// widths between the two thresholds show both — and the topbar, ~110px wider
// with the quick links in it, overruns its own box and clips the menu button
// off the right edge. Sweeping the seam is the only way to see that band; the
// suite's fixed 390/800/1280/1440 viewports all sit clear of it.
test("management links appear in exactly one place at every width", async ({ page }) => {
  const errors = trackPageErrors(page);
  await bypassProductFlow(page);

  // 959/960 was the seam while the primary nav carried three links; 11.3.0
  // moved it to 1120 for the fourth. Both are swept: the old pair proves the
  // band that used to show two copies now shows one, the new pair proves the
  // moved threshold still hands over cleanly.
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
        // A clipped control is still in the DOM and still passes a visibility
        // check; what makes it unreachable is hanging past the viewport with
        // nowhere to scroll to.
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
    // Opening a dialog has to move focus into it. The hidden copy of the links
    // stays in the DOM, and focus() on a display:none element is a silent
    // no-op — which would leave focus on the trigger. The shell moves focus in
    // a requestAnimationFrame, so poll rather than read once.
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

    // Same hash next iteration means no remount, so the menu would still be
    // open and the button would read "메뉴 닫기". Closing it here keeps the loop
    // honest and checks Escape works at this width while we are in it.
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
    // Renders at all — a blank or errored frame is what a screenshot hides.
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

  // Three named steps, each saying what it does to your file — this tab used
  // to be two raw API payloads and was hidden from plain mode entirely.
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

  // Named memories, not a count or a reassurance.
  const result = page.getByTestId("network-boundary-preview-result");
  await expect(result).toContainText("릴리스 절차 정리");
  await expect(result).toContainText("배포 전 확인 목록");
  // Previewing under local_only must not read as "this was just sent".
  await expect(result).toContainText("아무것도 나가지 않습니다");

  expect(errors).toEqual([]);
});
