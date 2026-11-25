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

async function openInsightsShelf(page) {
  await page.getByTestId("brain-dock-stats").click();
  await expect(page.getByTestId("brain-home-drawer")).toBeVisible();
}

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
