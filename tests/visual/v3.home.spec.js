const { test, expect } = require("@playwright/test");
const {
  GRAPH_SEARCH_PLACEHOLDER,
  trackPageErrors,
  openAttachMenu,
  openBrain,
} = require("./v3_helpers");

// First run and the Brain home itself: the ritual that creates the Brain, the
// one-viewport home contract, and the suggestion deck that lives on it.

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
  await expect(page.getByRole("heading", { name: "Brain에게 물어보세요." })).toBeVisible();
  await expect(page.locator("body")).not.toContainText("전체 지식 그래프");
  expect(errors).toEqual([]);
});

test("the Brain home is one screen: Brain, composer, add material, quiet settings", async ({ page }) => {
  const errors = trackPageErrors(page);
  await page.setViewportSize({ width: 1280, height: 800 });
  await openBrain(page);

  await expect(page.getByRole("heading", { name: "Brain에게 물어보세요." })).toBeVisible();
  const stage = page.getByTestId("brain-home-stage");
  const livingBrain = page.getByTestId("brain-knowledge-flow").getByTestId("living-brain");
  await expect(stage).toBeVisible();
  await expect(page.getByTestId("brain-knowledge-flow")).toBeVisible();
  await expect(livingBrain).toBeVisible();
  // Nothing graph-shaped on the home any more: the knowledge graph opens by
  // clicking the Brain itself (asserted at the end of this test).
  await expect(page.locator(".brain-flow-node")).toHaveCount(0);
  await expect(page.locator(".brain-flow-edges line")).toHaveCount(0);
  // Capture folds behind the composer's +: closed on arrival, one click away.
  await expect(page.getByTestId("brain-attach-toggle")).toBeVisible();
  await expect(page.getByTestId("brain-ingestion-dock")).toHaveCount(0);
  await openAttachMenu(page);
  await expect(page.getByTestId("brain-ingestion-dock")).toBeVisible();
  await expect(page.getByRole("button", { name: "폴더", exact: true })).toBeVisible();
  // Autonomy and appearance are decided here, not buried in settings.
  await expect(page.getByTestId("quick-mode-strict")).toBeVisible();
  await expect(page.getByTestId("topbar-theme-toggle")).toBeVisible();

  // One station, not five stacked blocks. The greeting, the composer (capture
  // riding inside its + menu) and the autonomy dial used to be siblings of
  // equal weight down the column; a first-time reader had no cue which was the
  // thing to do. They are one surface now, so every one must resolve inside it.
  const station = page.getByTestId("brain-home-station");
  await expect(station).toBeVisible();
  await expect(station.getByTestId("brain-knowledge-flow")).toBeVisible();
  await expect(station.locator(".brain-composer")).toBeVisible();
  await expect(station.locator(".brain-composer").getByTestId("brain-ingestion-dock")).toBeVisible();
  await expect(station.getByTestId("brain-quick-controls")).toBeVisible();
  // The station's floor holds autonomy alone — capture left it for the +.
  const toolbar = station.locator(".brain-station-toolbar");
  await expect(toolbar.getByTestId("brain-quick-controls")).toBeVisible();
  await expect(toolbar.getByTestId("brain-ingestion-dock")).toHaveCount(0);
  await expect(stage.locator("> .brain-home-station")).toHaveCount(1);

  // The dock is a labeled continuity bar in the reading path, four drawers
  // closed. 11.2.0 added 기능 (the opt-in switchboard) as a fourth rail item
  // and *not* as a card: the canvas keeps exactly the station and the deck.
  for (const dockButton of ["brain-dock-conversations", "brain-dock-stats", "brain-dock-map", "brain-dock-features"]) {
    await expect(page.getByTestId(dockButton)).toBeVisible();
  }
  await expect(page.locator(".brain-home-dock-rail > button")).toHaveCount(4);
  await expect(page.getByTestId("brain-home-drawer")).toHaveCount(0);
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
  for (const critical of [livingBrain, page.getByTestId("brain-ingestion-dock"), composer]) {
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
      page.getByTestId("brain-attach-toggle"),
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
  await openAttachMenu(page);
  await page.locator(".brain-composer").getByRole("button", { name: "노트", exact: true }).click();
  const popover = page.locator(".brain-composer .brain-ingestion-dock-popover");
  await expect(popover).toBeVisible();
  await expect(popover).toBeInViewport({ ratio: 0.99 });
  expect(await station.evaluate((el) => el.scrollTop)).toBe(0);
  const heroTopAfter = await page.getByTestId("brain-knowledge-flow").evaluate((el) => el.getBoundingClientRect().top);
  expect(Math.abs(heroTopAfter - heroTopBefore)).toBeLessThanOrEqual(1);
  await page.locator(".brain-composer").getByRole("button", { name: "노트", exact: true }).click();
  await page.keyboard.press("Escape");

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
