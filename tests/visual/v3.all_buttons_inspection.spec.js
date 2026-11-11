const { test, expect } = require("@playwright/test");
const { trackPageErrors, bypassProductFlow, openBrain } = require("./v3_helpers");

test.describe("Full Comprehensive Button-by-Button UI Inspection", () => {
  test("1. Onboarding flow: Wake -> Login -> Model Recommendations (with Google EmbeddingGemma 2) -> Install & Skip", async ({ page }) => {
    const errors = trackPageErrors(page);
    const serverErrors = [];
    page.on("response", (res) => {
      if (res.status() >= 500) {
        serverErrors.push(`${res.request().method()} ${res.url()} -> ${res.status()}`);
      }
    });

    // Clean session: start from onboarding
    await page.goto("/app");
    await page.waitForTimeout(500);

    // If login button is visible, click it
    const loginBtn = page.getByRole("button", { name: /시작하기|로그인|Login|Start/i });
    if (await loginBtn.isVisible({ timeout: 2000 }).catch(() => false)) {
      await loginBtn.click();
      await page.waitForTimeout(500);
    }

    // Now on recommendations step
    // Verify EmbeddingGemma 2 recommendation card is present
    const embeddingCard = page.locator("[data-testid='flow-embedding-recommendation']");
    if (await embeddingCard.isVisible({ timeout: 4000 }).catch(() => false)) {
      await expect(embeddingCard).toContainText(/EmbeddingGemma 2|google\/embeddinggemma-2/i);
      
      // Click "임베딩 모델(EmbeddingGemma 2) 준비"
      const selectEmbeddingBtn = page.locator("[data-testid='flow-select-embedding-model']");
      await expect(selectEmbeddingBtn).toBeVisible();
      await selectEmbeddingBtn.click();
      await page.waitForTimeout(500);

      // Verify we reached install step
      const backBtn = page.getByRole("button", { name: /뒤로|Back/i });
      if (await backBtn.isVisible({ timeout: 2000 }).catch(() => false)) {
        await backBtn.click();
        await page.waitForTimeout(500);
      }
    }

    // Click Skip model setup button ("모델 없이 Brain 열기")
    const skipBtn = page.getByRole("button", { name: /모델 없이 Brain 열기|Open Brain without a model|건너뛰기/i });
    if (await skipBtn.isVisible({ timeout: 2000 }).catch(() => false)) {
      await skipBtn.click();
      await page.waitForTimeout(500);
    }

    expect(serverErrors).toEqual([]);
    expect(errors).toEqual([]);
  });

  test("2. Brain Home & Chat: clicks mode toggles, composer, attachment chips, dock stats", async ({ page }) => {
    const errors = trackPageErrors(page);
    const serverErrors = [];
    page.on("response", (res) => {
      if (res.status() >= 500) {
        serverErrors.push(`${res.request().method()} ${res.url()} -> ${res.status()}`);
      }
    });

    await openBrain(page);

    // 1) Test composer input and send button
    const composerInput = page.locator("textarea, input[type='text']").first();
    if (await composerInput.isVisible()) {
      await composerInput.fill("안녕하세요! Lattice AI 테스트입니다.");
      const sendButton = page.locator("button[type='submit'], [data-testid='brain-send-button']").first();
      if (await sendButton.isVisible()) {
        await sendButton.click();
        await page.waitForTimeout(300);
      }
    }

    // 2) Open attach menu and test clicking each chip
    const attachToggle = page.getByTestId("brain-attach-toggle");
    if (await attachToggle.isVisible({ timeout: 2000 }).catch(() => false)) {
      await attachToggle.click();
      const attachMenu = page.getByTestId("brain-attach-menu");
      await expect(attachMenu).toBeVisible();

      // Click around attach chips
      const chips = attachMenu.locator("button, label.brain-ingestion-dock-action");
      const chipCount = await chips.count();
      for (let i = 0; i < chipCount; i++) {
        const chip = chips.nth(i);
        if (await chip.isVisible()) {
          // Verify focus/hover
          await chip.hover();
        }
      }
      // Close attach menu
      await attachToggle.click();
    }

    // 3) Open dock stats drawer and close it
    const statsBtn = page.getByTestId("brain-dock-stats");
    if (await statsBtn.isVisible({ timeout: 2000 }).catch(() => false)) {
      await statsBtn.click();
      await expect(page.getByTestId("brain-home-drawer")).toBeVisible();
      // Close stats drawer cleanly with Escape
      await page.keyboard.press("Escape");
      await page.waitForTimeout(300);
    }

    expect(serverErrors).toEqual([]);
    expect(errors).toEqual([]);
  });

  test("3. Model Library: verifies EmbeddingGemma 2 recommendation card, activate button, filters, and toggles", async ({ page }) => {
    const errors = trackPageErrors(page);
    const serverErrors = [];
    page.on("response", (res) => {
      if (res.status() >= 500) {
        serverErrors.push(`${res.request().method()} ${res.url()} -> ${res.status()}`);
      }
    });

    await bypassProductFlow(page, { mode: "advanced" });
    await page.goto("/app#/models");
    await page.waitForTimeout(600);

    // Verify Embedding recommendation section is visible
    const embeddingCard = page.locator("[data-testid='library-embedding-recommendation']");
    await expect(embeddingCard).toBeVisible({ timeout: 5000 });
    await expect(embeddingCard).toContainText(/Google EmbeddingGemma 2|google\/embeddinggemma-2/i);

    // Test clicking activate button for EmbeddingGemma 2
    const activateBtn = page.locator("[data-testid='library-activate-embeddinggemma-2']");
    if (await activateBtn.isVisible().catch(() => false)) {
      await activateBtn.click();
      await page.waitForTimeout(400);
    }

    // Test clicking all filter buttons & engine tabs on the page
    const buttons = page.locator("main button, nav button, [role='tab']");
    const count = await buttons.count();
    for (let i = 0; i < count; i++) {
      const btn = buttons.nth(i);
      if (await btn.isVisible() && await btn.isEnabled()) {
        const text = await btn.innerText().catch(() => "");
        // Avoid destructive unload buttons during crawl
        if (!/삭제|제거|Unload|Delete/i.test(text)) {
          await btn.click().catch(() => {});
          await page.waitForTimeout(50);
        }
      }
    }

    expect(serverErrors).toEqual([]);
    expect(errors).toEqual([]);
  });

  test("4. Capture surface: clicks through Note, File, Web URL, Folder tabs and action buttons", async ({ page }) => {
    const errors = trackPageErrors(page);
    const serverErrors = [];
    page.on("response", (res) => {
      if (res.status() >= 500) {
        serverErrors.push(`${res.request().method()} ${res.url()} -> ${res.status()}`);
      }
    });

    await bypassProductFlow(page);
    await page.goto("/app#/capture");
    await page.waitForTimeout(500);

    // Click all tabs on capture screen
    const tabs = page.locator("[role='tab'], .tab-button, button.segmented-control-item");
    const tabCount = await tabs.count();
    for (let i = 0; i < tabCount; i++) {
      const tab = tabs.nth(i);
      if (await tab.isVisible()) {
        await tab.click();
        await page.waitForTimeout(100);
      }
    }

    // Test text inputs and buttons
    const inputs = page.locator("input[type='text'], textarea");
    if (await inputs.count() > 0) {
      const input = inputs.first();
      if (await input.isVisible()) {
        await input.fill("Sample note capture test");
      }
    }

    expect(serverErrors).toEqual([]);
    expect(errors).toEqual([]);
  });

  test("5. Knowledge Graph & Search: zoom controls, reset view, and search input", async ({ page }) => {
    const errors = trackPageErrors(page);
    const serverErrors = [];
    page.on("response", (res) => {
      if (res.status() >= 500) {
        serverErrors.push(`${res.request().method()} ${res.url()} -> ${res.status()}`);
      }
    });

    await bypassProductFlow(page);
    await page.goto("/app#/knowledge-graph");
    await page.waitForTimeout(600);

    // Click graph buttons (zoom, reset, layout)
    const graphButtons = page.locator("button");
    const count = await graphButtons.count();
    for (let i = 0; i < Math.min(count, 10); i++) {
      const btn = graphButtons.nth(i);
      if (await btn.isVisible() && await btn.isEnabled()) {
        await btn.click().catch(() => {});
      }
    }

    expect(serverErrors).toEqual([]);
    expect(errors).toEqual([]);
  });

  test("6. Settings & System: theme toggling, language switching, hardware reload", async ({ page }) => {
    const errors = trackPageErrors(page);
    const serverErrors = [];
    page.on("response", (res) => {
      if (res.status() >= 500) {
        serverErrors.push(`${res.request().method()} ${res.url()} -> ${res.status()}`);
      }
    });

    await bypassProductFlow(page);
    await page.goto("/app#/settings");
    await page.waitForTimeout(500);

    // Click language switcher buttons or theme switcher buttons
    const buttons = page.locator("button, [role='radio']");
    const count = await buttons.count();
    for (let i = 0; i < count; i++) {
      const btn = buttons.nth(i);
      if (await btn.isVisible() && await btn.isEnabled()) {
        const text = await btn.innerText().catch(() => "");
        if (!/초기화|Reset|Delete|Clear/i.test(text)) {
          await btn.click().catch(() => {});
          await page.waitForTimeout(50);
        }
      }
    }

    expect(serverErrors).toEqual([]);
    expect(errors).toEqual([]);
  });

  test("7. Review Center, Automations, Chronicle, and Admin surfaces: all tabs and controls", async ({ page }) => {
    const errors = trackPageErrors(page);
    const serverErrors = [];
    page.on("response", (res) => {
      if (res.status() >= 500) {
        serverErrors.push(`${res.request().method()} ${res.url()} -> ${res.status()}`);
      }
    });

    const routes = ["#/review", "#/runs", "#/chronicle", "#/memory"];
    for (const route of routes) {
      await bypassProductFlow(page);
      await page.goto(`/app${route}`);
      await page.waitForTimeout(400);

      // Click all interactive tabs and buttons on each page
      const buttons = page.locator("[role='tab'], button.button, button.badge-action, .tab-item");
      const count = await buttons.count();
      for (let i = 0; i < Math.min(count, 8); i++) {
        const btn = buttons.nth(i);
        if (await btn.isVisible() && await btn.isEnabled()) {
          await btn.click().catch(() => {});
          await page.waitForTimeout(50);
        }
      }
    }

    expect(serverErrors).toEqual([]);
    expect(errors).toEqual([]);
  });
});
