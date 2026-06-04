const { test, expect } = require("@playwright/test");

async function openLiveChat(page) {
  const json = (body) => ({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
  await page.route("**/account/profile", (route) => route.fulfill(json({
    email: "admin@example.com",
    name: "Admin",
    nickname: "Admin",
    is_admin: true,
  })));
  await page.route("**/health", (route) => route.fulfill(json({
    status: "ok",
    current_model: "mlx-community/gemma-4-12b-it-4bit",
    loaded_models: ["mlx-community/gemma-4-12b-it-4bit"],
    device: "visual",
  })));
  await page.route("**/local/sysinfo", (route) => route.fulfill(json({
    ram_pct: 22,
    cpu_pct: 8,
    gpu_mem_pct: 5,
  })));
  await page.route("**/runtime_features", (route) => route.fulfill(json({
    telegram_enabled: false,
    graph_enabled: true,
  })));
  await page.route("**/history/conversations", (route) => route.fulfill(json({ conversations: [] })));
  await page.route("**/engines", (route) => route.fulfill(json({ engines: [] })));
  await page.addInitScript(() => {
    localStorage.setItem("ltcai_onboarding_complete", "true");
    localStorage.setItem("ltcai_onboarding_complete_admin@example.com", "true");
  });
  await page.goto("/chat");
  await page.waitForFunction(() => typeof window.showModalLayer === "function");
  await page.evaluate(() => window.setTheme && window.setTheme("dark"));
}

test("[chat] modal manager keeps one blocking overlay active and restores scroll", async ({ page }) => {
  await openLiveChat(page);

  await page.evaluate(() => {
    window.showModalLayer("mode-modal-overlay");
    window.showModalLayer("model-overlay");
  });

  let state = await page.evaluate(() => ({
    mode: getComputedStyle(document.getElementById("mode-modal-overlay")).display,
    model: getComputedStyle(document.getElementById("model-overlay")).display,
    locked: document.body.classList.contains("modal-open"),
    overflow: document.body.style.overflow,
  }));
  expect(state).toEqual({ mode: "none", model: "flex", locked: true, overflow: "hidden" });

  await page.keyboard.press("Escape");
  state = await page.evaluate(() => ({
    model: getComputedStyle(document.getElementById("model-overlay")).display,
    locked: document.body.classList.contains("modal-open"),
    overflow: document.body.style.overflow,
  }));
  expect(state.model).toBe("none");
  expect(state.locked).toBeFalsy();
  expect(state.overflow).toBe("");
});

test("[chat] permission dialog temporarily replaces and restores the previous modal", async ({ page }) => {
  await openLiveChat(page);

  await page.evaluate(() => {
    window.showModalLayer("local-browser-overlay");
    window.__permissionResult = window.requestPermission("/tmp/report.md", "read", "read");
  });
  let state = await page.evaluate(() => ({
    local: getComputedStyle(document.getElementById("local-browser-overlay")).display,
    perm: getComputedStyle(document.getElementById("perm-overlay")).display,
  }));
  expect(state).toEqual({ local: "none", perm: "flex" });

  await page.evaluate(() => window.resolvePermission(false));
  const result = await page.evaluate(() => window.__permissionResult);
  state = await page.evaluate(() => ({
    local: getComputedStyle(document.getElementById("local-browser-overlay")).display,
    perm: getComputedStyle(document.getElementById("perm-overlay")).display,
  }));
  expect(result).toBe(false);
  expect(state).toEqual({ local: "flex", perm: "none" });
});

test("[static] favicon route is available", async ({ request }) => {
  const res = await request.get("/favicon.ico");
  expect(res.status()).toBe(200);
  expect(res.headers()["content-type"]).toContain("image/");
});
