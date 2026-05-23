/**
 * Lattice AI — automated screenshot capture
 * Usage: SESSION_TOKEN=xxx node scripts/take_screenshots.js
 */
let playwright;
try { playwright = require('playwright'); } catch(e) {
  playwright = require('/tmp/node_modules/playwright');
}
const { chromium } = playwright;
const path = require('path');
const fs   = require('fs');

const BASE  = 'http://localhost:4825';
const OUT   = path.join(__dirname, '..', 'docs', 'images');
const TOKEN = process.env.SESSION_TOKEN || '';
fs.mkdirSync(OUT, { recursive: true });

const PAGES = [
  { name: 'chat',  url: `${BASE}/`,      wait: 3500 },
  { name: 'admin', url: `${BASE}/admin`, wait: 3000 },
  { name: 'graph', url: `${BASE}/graph`, wait: 3500 },
];

(async () => {
  const browser = await chromium.launch({ headless: true });
  const ctx = await browser.newContext({
    viewport: { width: 1440, height: 900 },
    deviceScaleFactor: 2,
  });

  // Inject session cookie so we skip the login page
  if (TOKEN) {
    await ctx.addCookies([{
      name: 'session_token',
      value: TOKEN,
      domain: 'localhost',
      path: '/',
      httpOnly: true,
      secure: false,
    }]);
    console.log('🍪 Session cookie injected');
  }

  for (const pg of PAGES) {
    const page = await ctx.newPage();
    console.log(`📸 ${pg.name} → ${pg.url}`);
    try {
      await page.goto(pg.url, { waitUntil: 'networkidle', timeout: 20000 });
      await page.waitForTimeout(pg.wait);

      // Dismiss any open modals
      await page.evaluate(() => {
        document.querySelectorAll('.modal-backdrop, [data-bs-backdrop], dialog[open]')
          .forEach(el => { el.style.display = 'none'; });
      });

      const outPath = path.join(OUT, `screenshot-${pg.name}.png`);
      await page.screenshot({ path: outPath, fullPage: false });
      console.log(`  ✅ → ${outPath}`);
    } catch (e) {
      console.error(`  ❌ ${pg.name}: ${e.message}`);
    } finally {
      await page.close();
    }
  }

  await browser.close();
  console.log('\n✅ Done!');
})();
