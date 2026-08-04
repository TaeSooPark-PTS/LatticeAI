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
  // The eyebrow "로컬 Brain의 소유권" is gone: the rebuilt login screen says the
  // same thing as a sentence instead of a label above one. Assert the sentence
  // — the claim under test is that this screen states the memory is local and
  // owned here, not that a particular label survived.
  await expect(page.locator("body")).toContainText("이 컴퓨터 안에서만 사는 기억을 만듭니다.");
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

  // One station, not five stacked blocks. The greeting, the composer, capture
  // and the autonomy dial used to be siblings of equal weight down the column;
  // a first-time reader had no cue which was the thing to do. They are one
  // bordered surface now, so every one of them must resolve inside it.
  const station = page.getByTestId("brain-home-station");
  await expect(station).toBeVisible();
  await expect(station.getByTestId("brain-knowledge-flow")).toBeVisible();
  await expect(station.locator(".brain-composer")).toBeVisible();
  await expect(station.getByTestId("brain-ingestion-dock")).toBeVisible();
  await expect(station.getByTestId("brain-quick-controls")).toBeVisible();
  // Capture and autonomy answer the same question, so they share one toolbar
  // rather than sitting in two separate strips.
  const toolbar = station.locator(".brain-station-toolbar");
  await expect(toolbar.getByTestId("brain-ingestion-dock")).toBeVisible();
  await expect(toolbar.getByTestId("brain-quick-controls")).toBeVisible();
  await expect(stage.locator("> .brain-home-station")).toHaveCount(1);
  // 10.6.2: the suggestions left the station for a deck of their own below it,
  // so the station is the first move and nothing else.
  const deck = page.getByTestId("brain-secondary-deck");
  await expect(deck).toBeVisible();
  await expect(stage.locator("> [data-testid='brain-secondary-deck']")).toHaveCount(1);
  await expect(station.locator(".brain-home-prompt-strip")).toHaveCount(0);
  // A named <section> is a `region`; an aria-label on the plain div inside it
  // would be discarded, and the deck would reach a screen reader unnamed.
  await expect(page.getByRole("region", { name: "Brain 추천 질문" })).toBeVisible();
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

/**
 * 10.8.0 shipped a welcome step that measured 770px against a 747px viewport,
 * so the very first screen of the product opened with a scrollbar and its
 * closing line — the one that says what the person is agreeing to — sat under
 * the fold. Two ordinary laptop heights, and the invitation has to fit in both.
 */
test("the welcome step fits on a laptop without scrolling", async ({ page }) => {
  const errors = trackPageErrors(page);
  for (const viewport of [
    { width: 1280, height: 747 },
    { width: 1440, height: 800 },
  ]) {
    await page.setViewportSize(viewport);
    await page.goto("/app");
    await page.getByRole("button", { name: "한국어" }).click();
    await expect(page.getByRole("button", { name: "Brain 지금 깨우기" })).toBeVisible();

    const overflow = await page.evaluate(
      () => document.documentElement.scrollHeight - document.documentElement.clientHeight,
    );
    expect(overflow, `welcome overflows at ${viewport.width}x${viewport.height}`).toBeLessThanOrEqual(1);

    // The closing note is the last thing on the page, so if it is in view the
    // whole invitation is.
    await expect(page.locator(".ritual-start-note")).toBeInViewport({ ratio: 0.99 });
    await expect(page.getByRole("button", { name: "Brain 지금 깨우기" })).toBeInViewport({ ratio: 0.99 });
  }
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

/**
 * Three ways the 10.6.2 two-card home broke in CSS rather than in JSX, none of
 * which a render test can see. All three came from the same root cause: the
 * suggestion strip's rules were rewritten at one-class specificity, and this
 * home's stylesheet is not the only unlayered sheet that claims that class.
 */
test("the suggestion deck survives its own stylesheet", async ({ page }) => {
  const errors = trackPageErrors(page);
  await openBrain(page);

  // 1. The station must not clip. The capture popover anchors to the toolbar and
  //    opens below it — outside the station's box — so `overflow: hidden` there
  //    both hid the panel and made the card a scroll container, which scrolled
  //    the greeting and half the composer away when the note field took focus.
  const station = page.getByTestId("brain-home-station");
  expect(await station.evaluate((el) => getComputedStyle(el).overflow)).toBe("visible");

  const heroTopBefore = await page.getByTestId("brain-knowledge-flow").evaluate((el) => el.getBoundingClientRect().top);
  await page.locator(".brain-station-toolbar").getByRole("button", { name: "노트", exact: true }).click();
  const popover = page.locator(".brain-station-toolbar .brain-ingestion-dock-popover");
  await expect(popover).toBeVisible();
  await expect(popover).toBeInViewport({ ratio: 0.99 });
  expect(await station.evaluate((el) => el.scrollTop)).toBe(0);
  const heroTopAfter = await page.getByTestId("brain-knowledge-flow").evaluate((el) => el.getBoundingClientRect().top);
  expect(Math.abs(heroTopAfter - heroTopBefore)).toBeLessThanOrEqual(1);
  await page.locator(".brain-station-toolbar").getByRole("button", { name: "노트", exact: true }).click();

  // 2. The cards fill the deck. graph-home.css centres `.brain-home-prompt-strip`
  //    and loads first; at equal specificity that centring survives and the grid
  //    shrink-wraps to a narrow column in the middle of a wide card.
  const strip = page.locator(".brain-home-prompt-strip");
  expect(await strip.evaluate((el) => getComputedStyle(el).alignItems)).toBe("stretch");
  expect(await strip.evaluate((el) => getComputedStyle(el).overflow)).toBe("visible");
  const fill = await page.evaluate(() => {
    const deck = document.querySelector("[data-testid='brain-secondary-deck']");
    const grid = document.querySelector(".brain-prompt-grid");
    const style = getComputedStyle(deck);
    const inner =
      deck.getBoundingClientRect().width - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight);
    return grid.getBoundingClientRect().width / inner;
  });
  expect(fill).toBeGreaterThan(0.98);

  // 3. A card is a card at desktop width and a chip below 900px. Both values are
  //    set on `.brain-prompt-grid button`, one class deep, and affordance.css
  //    loads last with an app-wide `button { white-space: nowrap }` plus an
  //    opt-back-in list. Naming this selector in that list ties the narrow rule
  //    and wins it, which leaves a wrapping card on the width with no room.
  const card = page.locator(".brain-prompt-grid button").first();
  expect(await card.evaluate((el) => getComputedStyle(el).whiteSpace)).toBe("normal");

  // 4. responsive.css hides `.brain-home-prompt-strip` under 760px — a rule left
  //    over from the layout before this one, and one that only ever lost because
  //    this sheet outranked it. If it wins, the deck renders as an empty card.
  for (const width of [900, 760, 640, 420]) {
    await page.setViewportSize({ width, height: 900 });
    await expect(strip).toBeVisible();
    const cards = await page.locator(".brain-prompt-grid button").count();
    expect(cards).toBeGreaterThan(0);
    const stripHeight = await strip.evaluate((el) => el.getBoundingClientRect().height);
    expect(stripHeight).toBeGreaterThan(20);
    expect(await card.evaluate((el) => getComputedStyle(el).whiteSpace)).toBe("nowrap");
  }
  expect(errors).toEqual([]);
});

/**
 * The other half of the deck. A Brain with nothing to suggest yet — which is
 * every Brain on its first day, the state this screen is designed for — renders
 * starter pills instead of cards. Nothing captures it: the mock always answers
 * with two questions, so the release screenshot and every test above show only
 * the card grid, and the pill row is styled blind. This forces the branch.
 */
test("the deck still reads as a deck when Brain has nothing to suggest", async ({ page }) => {
  const errors = trackPageErrors(page);
  await page.route("**/api/memory/brain-brief*", async (route) => {
    const response = await route.fetch();
    const body = await response.json();
    await route.fulfill({ json: { ...body, suggested_questions: [] } });
  });
  await openBrain(page);

  const deck = page.getByTestId("brain-secondary-deck");
  await expect(deck).toBeVisible();
  await expect(deck.locator(".brain-prompt-grid")).toHaveCount(0);
  const pills = deck.locator(".brain-prompt-pills-row > button.brain-prompt-pill");
  await expect(pills.first()).toBeVisible();

  // The label has to survive the branch — without it the card is three loose
  // pills with nothing saying what they are.
  await expect(deck.getByText("이렇게 시작해 보세요")).toBeVisible();

  // conversation.css gives every `.brain-prompt-pill` a 2.65rem floor, drawn for
  // the pills in a live conversation. Inherited here it would leave this rule's
  // padding doing nothing and the row half again as tall as it reads.
  const heights = await pills.evaluateAll((els) => els.map((el) => el.getBoundingClientRect().height));
  expect(heights.length).toBeGreaterThan(0);
  for (const height of heights) expect(height).toBeLessThan(40);

  // A pill fills the composer rather than sending — the empty state must never
  // ask a question the reader did not choose to ask.
  const label = (await pills.first().textContent()).trim();
  await pills.first().click();
  await expect(page.locator(".brain-home-station textarea")).toHaveValue(label);
  await expect(page.getByTestId("brain-home-station")).toBeVisible();

  expect(errors).toEqual([]);
});

/**
 * The suggestion cards lift 1px on hover. affordance.css owns that gesture and
 * owns the `prefers-reduced-motion` rule that cancels it — but it only cancels
 * what it can outrank, and it is a two-class selector. A layout sheet that
 * declares `transform` on the same hover with one class more takes the lift
 * back and moves the card for a reader who asked the system to hold still.
 */
test("the suggestion cards honour prefers-reduced-motion", async ({ page }) => {
  const errors = trackPageErrors(page);
  await openBrain(page);

  const card = page.locator(".brain-prompt-grid button").first();
  const restingBorder = await card.evaluate((el) => getComputedStyle(el).borderTopColor);

  await card.hover();
  expect(await card.evaluate((el) => getComputedStyle(el).transform)).not.toBe("none");

  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.mouse.move(0, 0);
  await card.hover();
  expect(await card.evaluate((el) => getComputedStyle(el).transform)).toBe("none");
  expect(await card.evaluate((el) => getComputedStyle(el).transitionProperty)).toBe("none");

  // Without the lift, colour is the whole hover. It has to still say something,
  // or a reduced-motion reader gets no answer to the pointer at all.
  expect(await card.evaluate((el) => getComputedStyle(el).borderTopColor)).not.toBe(restingBorder);

  await page.emulateMedia({ reducedMotion: null });
  expect(errors).toEqual([]);
});

/**
 * The greeting banner shrank the Brain from 5.4rem to 3.2rem, and shrinking the
 * organism is only half the job: its halo is an inline `box-shadow` that
 * LivingBrain writes from the Brain's depth, sized for the 220–320px organism it
 * renders standing alone. No stylesheet can outrank an inline style, so the
 * first attempt at scaling it down — a plain `.brain-hero-organism .brain-aura`
 * rule — was dead the moment it was written, and the banner shipped a 60px blur
 * around a 58px body, clipped flat into a smudge with two straight edges.
 *
 * Asserted in geometry rather than on the declaration, so it holds whichever way
 * the glow is scaled next.
 */
test("the Brain's glow is sized to the Brain in the banner, not to the one standing alone", async ({ page }) => {
  const errors = trackPageErrors(page);
  await openBrain(page);

  const glow = await page.evaluate(() => {
    const banner = document.querySelector(".brain-home-station > .brain-hero");
    const organism = document.querySelector(".brain-hero-organism .brain-organism");
    const aura = document.querySelector(".brain-hero-organism .brain-aura");
    if (!banner || !organism || !aura) return null;
    const shadow = getComputedStyle(aura).boxShadow;
    // "rgba(…) 0px 0px 60px 0px" — offset-x, offset-y, blur, spread.
    const lengths = (shadow.match(/-?[\d.]+px/g) || []).map(parseFloat);
    return {
      shadow,
      blur: lengths.length >= 3 ? lengths[2] : 0,
      organism: organism.getBoundingClientRect().width,
      banner: banner.getBoundingClientRect().toJSON(),
      aura: aura.getBoundingClientRect().toJSON(),
    };
  });
  expect(glow).not.toBeNull();

  // A halo cannot be wider than the head it surrounds. The default is 60px on a
  // ~58px organism; anything in that neighbourhood means the host's scaling lever
  // stopped reaching the inline style again.
  expect(glow.blur).toBeLessThan(glow.organism / 3);

  // And the lit box itself has to sit inside the banner, which clips: a glow
  // whose source is already outside the clip edge renders as a cut, not a glow.
  expect(glow.aura.left).toBeGreaterThanOrEqual(glow.banner.left);
  expect(glow.aura.top).toBeGreaterThanOrEqual(glow.banner.top);
  expect(glow.aura.bottom).toBeLessThanOrEqual(glow.banner.bottom);

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
  // edge; the real gap here is just the bar's own padding.
  expect(bottomBar.count).toBe(4);
  expect(bottomBar.navWidth - bottomBar.right).toBeLessThan(bottomBar.navWidth / bottomBar.count / 2);

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

// The network boundary contracts shipped in 10.1.0 with no way to reach them
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

  for (const width of [390, 760, 761, 768, 800, 900, 959, 960, 1024, 1440]) {
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
