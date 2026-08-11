const { test, expect } = require("@playwright/test");
const { trackPageErrors, bypassProductFlow } = require("./v3_helpers");

// 연대기 — the screen that shows the Brain its own history.
//
// The release screenshot of this route is only worth publishing if the screen
// is populated, and "populated" here means four separate things that fail
// independently: a curve with a handle on it, a grid with clickable days, a
// day's story split into named groups, and — only when the handle is in the
// past — the rewind panel reading `as-of`. The mock server serves a fixed
// eight-week history so all four are deterministic.

async function openChronicle(page, { mode = "basic" } = {}) {
  await bypassProductFlow(page, { mode });
  await page.goto("/app#/chronicle");
  await expect(page.locator("h1.page-title")).toHaveText("두뇌가 자라온 시간");
}

test("the chronicle renders a populated history, not an empty state", async ({ page }) => {
  const errors = trackPageErrors(page);
  await openChronicle(page);

  // The growth curve drew something: an empty `d` is what a broken series looks
  // like, and it is invisible in a screenshot because the panel still has its
  // frame, its title and its axis labels.
  const area = page.locator(".chronicle-growth-area");
  await expect(area).toHaveAttribute("d", /^M .+ L /);

  // The grid has real days in it.
  const cells = page.getByTestId("chronicle-heatmap-cell");
  expect(await cells.count()).toBeGreaterThan(30);

  // And the day's story is grouped the way a person reads it.
  const story = page.getByTestId("chronicle-day");
  await expect(story).toBeVisible();
  for (const group of ["자료", "새로 생긴 개념", "나눈 대화", "달라진 사실"]) {
    await expect(story.getByRole("heading", { name: new RegExp(group) })).toBeVisible();
  }
  expect(errors).toEqual([]);
});

test("the time handle is a named, keyboard-operable slider", async ({ page }) => {
  const errors = trackPageErrors(page);
  await openChronicle(page);

  const slider = page.getByRole("slider", { name: "시점 고르기" });
  await expect(slider).toBeVisible();
  // It opens on the most recent day, so `aria-valuenow` sits at the maximum.
  const max = await slider.getAttribute("aria-valuemax");
  await expect(slider).toHaveAttribute("aria-valuenow", max);
  // The announced value is the state of the Brain that day, not a bare index.
  await expect(slider).toHaveAttribute("aria-valuetext", /\d{4}-\d{2}-\d{2}/);

  const before = await page.getByTestId("chronicle-scrubber-date").textContent();
  await slider.focus();
  await page.keyboard.press("Home");
  await expect(page.getByTestId("chronicle-scrubber-date")).not.toHaveText(before);
  expect(errors).toEqual([]);
});

test("standing in the past shows the Brain as it was, and a way back to now", async ({ page }) => {
  const errors = trackPageErrors(page);
  await openChronicle(page);

  // No rewind panel while the handle is on the latest day: "the Brain as it was
  // right now" would be a panel saying nothing.
  await expect(page.getByTestId("chronicle-rewind")).toHaveCount(0);

  await page.getByRole("slider", { name: "시점 고르기" }).focus();
  await page.keyboard.press("Home");

  const rewind = page.getByTestId("chronicle-rewind");
  await expect(rewind).toBeVisible();
  await expect(page.getByTestId("chronicle-rewind-entities")).toHaveText("148");
  // The one sentence that keeps the screen from contradicting itself: this
  // count includes documents, the curve's "개념" lane does not.
  await expect(rewind).toContainText("세는 방식이 달라요");
  await expect(rewind.getByRole("button", { name: /Lattice Workspace/ })).toBeVisible();

  await rewind.getByRole("button", { name: "지금으로 돌아오기" }).click();
  await expect(page.getByTestId("chronicle-rewind")).toHaveCount(0);
  expect(errors).toEqual([]);
});

test("a day picked on the grid becomes the day the story tells", async ({ page }) => {
  const errors = trackPageErrors(page);
  await openChronicle(page);

  const cells = page.getByTestId("chronicle-heatmap-cell");
  const target = cells.nth(5);
  const label = await target.getAttribute("aria-label");
  const date = label.slice(0, 10);

  await target.click();
  await expect(page.getByTestId("chronicle-scrubber-date")).toHaveText(date);
  await expect(page.getByTestId("chronicle-day-date")).toHaveText(date);
  await expect(target).toHaveAttribute("aria-pressed", "true");
  expect(errors).toEqual([]);
});

test("the everyday navigation carries 연대기 as its fourth destination", async ({ page }) => {
  const errors = trackPageErrors(page);
  await bypassProductFlow(page, { mode: "basic" });
  await page.goto("/app#/brain");

  const nav = page.getByRole("navigation", { name: "화면 이동" }).first();
  await expect(nav.getByRole("link", { name: "연대기" })).toBeVisible();
  await nav.getByRole("link", { name: "연대기" }).click();
  await expect(page).toHaveURL(/#\/chronicle$/);
  await expect(page.locator("h1.page-title")).toBeVisible();
  await expect(nav.getByRole("link", { name: "연대기" })).toHaveAttribute("aria-current", "page");
  expect(errors).toEqual([]);
});
