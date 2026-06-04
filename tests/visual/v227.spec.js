// ============================================================================
// Lattice AI — v2.2.7 visual system stabilization suite
//
// These checks lock the rendered issues found during browser QA:
//   - chat composer stays dark/crisp in dark mode, with no inner textarea box
//   - graph canvas stage is not a washed-out light surface in dark mode
//   - Workspace OS inputs/list cards use dark tokens in dark mode
//   - mobile chat composer keeps the same visual language without clipping
// ============================================================================
const { test, expect } = require("@playwright/test");

function json(body) {
  return { status: 200, contentType: "application/json", body: JSON.stringify(body) };
}

function luminance(rgb, base = { r: 11, g: 11, b: 30 }) {
  const values = String(rgb).match(/[\d.]+/g);
  if (!values) return 255;
  const [rawR, rawG, rawB, rawA = 1] = values.map(Number);
  const alpha = Math.max(0, Math.min(1, rawA));
  const r = rawR * alpha + base.r * (1 - alpha);
  const g = rawG * alpha + base.g * (1 - alpha);
  const b = rawB * alpha + base.b * (1 - alpha);
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

async function setTheme(page, mode = "dark") {
  await page.addInitScript((theme) => {
    localStorage.setItem("lt-theme", theme);
    localStorage.setItem("lattice-theme", theme);
    localStorage.setItem("ltcai_onboarding_complete", "true");
    localStorage.setItem("ltcai_onboarding_complete_admin@example.com", "true");
    document.documentElement.setAttribute("data-lt-theme", theme);
  }, mode);
  await page.evaluate((theme) => {
    localStorage.setItem("lt-theme", theme);
    localStorage.setItem("lattice-theme", theme);
    localStorage.setItem("ltcai_onboarding_complete", "true");
    localStorage.setItem("ltcai_onboarding_complete_admin@example.com", "true");
    document.documentElement.setAttribute("data-lt-theme", theme);
    window.setTheme && window.setTheme(theme);
  }, mode).catch(() => {});
  await page.waitForTimeout(120);
}

async function openLiveChat(page, { viewport } = {}) {
  if (viewport) await page.setViewportSize(viewport);
  await page.route("**/account/profile", (route) => route.fulfill(json({
    email: "admin@example.com",
    name: "Admin",
    nickname: "Admin",
    is_admin: true,
  })));
  await page.route("**/health", (route) => route.fulfill(json({
    status: "ok",
    current_model: null,
    loaded_models: [],
    device: "visual",
  })));
  await page.route("**/local/sysinfo", (route) => route.fulfill(json({
    ram_pct: 24,
    cpu_pct: 9,
    gpu_mem_pct: 0,
  })));
  await page.route("**/runtime_features", (route) => route.fulfill(json({
    telegram_enabled: false,
    graph_enabled: true,
  })));
  await page.route("**/history/conversations", (route) => route.fulfill(json({ conversations: [] })));
  await page.route("**/engines", (route) => route.fulfill(json({ engines: [] })));
  await setTheme(page, "dark");
  await page.goto("/chat");
  await page.waitForFunction(() => typeof window.showChat === "function");
  await setTheme(page, "dark");
  await page.evaluate(() => {
    window.setOnboardingComplete && window.setOnboardingComplete();
    document.querySelectorAll(
      ".admin-overlay,.model-overlay,.onboarding-overlay,#setup-overlay,.workspace-modal-overlay,.mode-modal-overlay,.acct-modal-overlay,.mcp-modal-overlay,.advanced-settings-overlay,.perm-overlay",
    ).forEach((el) => {
      el.classList.remove("open");
      el.style.display = "none";
      el.setAttribute("aria-hidden", "true");
    });
    document.body.classList.remove("modal-open");
    window.showChat();
  });
}

test("[chat] dark composer has no white haze or legacy inner textarea box", async ({ page }) => {
  await openLiveChat(page);

  const composer = await page.locator(".input-box").evaluate((el) => {
    const cs = getComputedStyle(el);
    return {
      background: cs.backgroundColor,
      image: cs.backgroundImage,
      border: cs.borderColor,
      boxShadow: cs.boxShadow,
    };
  });
  expect(composer.image).not.toContain("243, 237, 255");
  expect(composer.image).not.toContain("255, 255, 255");
  expect(composer.boxShadow).toContain("rgba(0, 0, 0");

  const textarea = await page.locator("#user-input").evaluate((el) => {
    const cs = getComputedStyle(el);
    return {
      background: cs.backgroundColor,
      borderTopWidth: cs.borderTopWidth,
      boxShadow: cs.boxShadow,
      outline: cs.outlineStyle,
    };
  });
  expect(textarea.background).toBe("rgba(0, 0, 0, 0)");
  expect(textarea.borderTopWidth).toBe("0px");
  expect(textarea.boxShadow).toBe("none");
  expect(textarea.outline).toBe("none");
});

test("[chat] mobile composer remains crisp and inside the viewport", async ({ page }) => {
  await openLiveChat(page, { viewport: { width: 390, height: 844 } });

  const geometry = await page.locator(".input-box").evaluate((el) => {
    const rect = el.getBoundingClientRect();
    return {
      left: rect.left,
      right: rect.right,
      bottom: rect.bottom,
      width: rect.width,
      viewportWidth: window.innerWidth,
      viewportHeight: window.innerHeight,
      image: getComputedStyle(el).backgroundImage,
    };
  });
  expect(geometry.left).toBeGreaterThanOrEqual(10);
  expect(geometry.right).toBeLessThanOrEqual(geometry.viewportWidth - 10);
  expect(geometry.bottom).toBeLessThanOrEqual(geometry.viewportHeight - 8);
  expect(geometry.image).not.toContain("255, 255, 255");
});

test("[graph] dark canvas stage is an intentional dark work surface", async ({ page }) => {
  await setTheme(page, "dark");
  await page.goto("/graph");
  await setTheme(page, "dark");
  const stage = await page.locator(".stage").evaluate((el) => {
    const cs = getComputedStyle(el);
    return { image: cs.backgroundImage, border: cs.borderColor };
  });
  expect(stage.image).toContain("rgb(11, 11, 30)");
  expect(stage.image).not.toContain("rgb(255, 255, 255)");
  expect(stage.image).not.toContain("#fff");
});

test("[workspace] dark inputs and relationship cards do not revert to white", async ({ page }) => {
  await setTheme(page, "dark");
  await page.goto("/workspace");
  await setTheme(page, "dark");

  const surfaces = await page.evaluate(() => {
    const selectors = ["#entity-search", "#org-name", ".list-item", ".workspace-band", ".workspace-panel"];
    return selectors.map((selector) => {
      const el = document.querySelector(selector);
      const cs = getComputedStyle(el);
      return { selector, background: cs.backgroundColor, color: cs.color };
    });
  });

  for (const surface of surfaces) {
    expect(luminance(surface.background), `${surface.selector} should not be a light surface`).toBeLessThan(120);
  }
});
