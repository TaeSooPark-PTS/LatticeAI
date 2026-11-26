const { test, expect } = require("@playwright/test");
const { WIDTHS, ROUTES, collectViolations } = require("./layout_audit");

for (const language of ["ko", "en"]) {
  for (const theme of ["light", "dark"]) {
    for (const [width, height] of WIDTHS) {
      test(`no label wraps and no control clips at ${width}px in ${language} ${theme}`, async ({ page }) => {
        test.setTimeout(90_000);
        await page.setViewportSize({ width, height });
        await page.addInitScript(([savedLanguage, savedTheme]) => {
          localStorage.setItem("lattice.productFlow.complete", "true");
          localStorage.setItem("lattice.language", savedLanguage);
          localStorage.setItem("lattice.theme", savedTheme);
          localStorage.setItem("lattice.mode", "advanced");
        }, [language, theme]);
        const found = [];
        await page.goto("/app");
        for (const route of ROUTES) {
          await page.goto(`/app#/${route}`);
          const ready = route === "admin/users" ? ".admin-console" : "header.brain-topbar";
          await expect(page.locator(ready)).toBeVisible();
          await expect(page.locator(".brain-shell-loader")).toHaveCount(0);
          await page.waitForTimeout(250);
          for (const violation of await page.evaluate(collectViolations)) found.push(`${route}: ${violation}`);
        }
        expect(found).toEqual([]);
      });
    }
  }
}
