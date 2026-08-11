/**
 * Shared page helpers for the v3 visual suite.
 *
 * Not a spec file (playwright's testMatch wants `.spec.`), so it holds no
 * tests — only the four or five gestures every spec repeats: skip the
 * first-run ritual, land on the Brain, and unfold the two menus that hold the
 * controls the tests reach for.
 */
const { expect } = require("@playwright/test");

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

// Automation, briefings, and health panels moved off the first screen — first
// into the "Brain이 정리한 내용" shelf, and in 10.10.0 into the dock's 통계
// drawer. Tests that exercise them open it explicitly.
async function openInsightsShelf(page) {
  await page.getByTestId("brain-dock-stats").click();
  await expect(page.getByTestId("brain-home-drawer")).toBeVisible();
}

// The capture chips (문서 · 이미지 · 파일 · 폴더 · 노트 · 웹) fold behind the
// composer's + control since 10.10.0; tests that use them unfold it first.
async function openAttachMenu(page) {
  await page.getByTestId("brain-attach-toggle").click();
  await expect(page.getByTestId("brain-attach-menu")).toBeVisible();
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

module.exports = {
  GRAPH_SEARCH_PLACEHOLDER,
  trackPageErrors,
  bypassProductFlow,
  openInsightsShelf,
  openAttachMenu,
  openBrain,
  openShellMenu,
};
