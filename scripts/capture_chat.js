/**
 * Capture chat screenshot with admin session, dismiss onboarding modal
 */
const puppeteer = require('/opt/homebrew/lib/node_modules/puppeteer');
const path = require('path');
const fs = require('fs');
const { execSync } = require('child_process');

const OUT = path.resolve(__dirname, '..', 'docs', 'images');
const TMP = path.resolve(__dirname, '..', 'docs', 'images', 'tmp_frames');

const ADMIN_TOKEN = 'VEvrxiwxuAk9VJ8R7QRoCQ71VMmlVB5modX9_Q6G6W8';
const USER_TOKEN  = '-boZVuN4myaIIS_AeqKcMy4BTD7Ru7l-z9Hm86eu-uU';

function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

async function capturePage(token, url, outName, postLoad) {
  const browser = await puppeteer.launch({
    headless: true,
    args: ['--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage'],
  });
  const page = await browser.newPage();
  await page.setViewport({ width: 1440, height: 900, deviceScaleFactor: 2 });
  await page.setCookie({ name: 'session_token', value: token, domain: 'localhost', path: '/' });
  console.log('Navigating to', url);
  await page.goto(url, { waitUntil: 'networkidle0', timeout: 20000 });
  await sleep(2500);
  if (postLoad) await postLoad(page);
  await sleep(800);
  const out = path.join(OUT, outName);
  await page.screenshot({ path: out, type: 'png' });
  console.log('✅', out);
  await browser.close();
}

(async () => {
  // Chat with admin session — dismiss onboarding modal
  await capturePage(ADMIN_TOKEN, 'http://localhost:4825/chat', 'screenshot-chat.png', async (page) => {
    await page.keyboard.press('Escape');
    await sleep(300);
    // Remove modal overlays via JS
    await page.evaluate(() => {
      var els = document.querySelectorAll('.modal-backdrop, .modal.show, dialog[open], .onboarding-overlay, .overlay, [class*="onboarding"]');
      els.forEach(function(el) { el.style.display = 'none'; });
      var bodyEl = document.body;
      if (bodyEl) {
        bodyEl.classList.remove('modal-open');
        bodyEl.style.overflow = '';
        bodyEl.style.paddingRight = '';
      }
    });
    await sleep(600);
  });

  // Admin — already good, just recapture cleanly
  await capturePage(ADMIN_TOKEN, 'http://localhost:4825/admin', 'screenshot-admin.png', null);

  // Graph with user session
  await capturePage(USER_TOKEN, 'http://localhost:4825/graph', 'screenshot-graph.png', async (page) => {
    await sleep(1500); // extra wait for graph to render
  });

  console.log('\n🎬 Building lattice-ai-demo.gif...');

  const frames = [
    { file: path.join(OUT, 'screenshot-chat.png'),  dur: 4 },
    { file: path.join(OUT, 'screenshot-admin.png'), dur: 3 },
    { file: path.join(OUT, 'screenshot-graph.png'), dur: 3 },
  ];

  const concatLines = frames.flatMap(function(f) { return ["file '" + f.file + "'", "duration " + f.dur]; }).join('\n')
    + "\nfile '" + frames[frames.length - 1].file + "'";

  const concatFile = path.join(TMP, 'concat.txt');
  fs.writeFileSync(concatFile, concatLines);

  const palette = path.join(TMP, 'palette.png');
  const gifOut  = path.join(OUT, 'lattice-ai-demo.gif');

  execSync('ffmpeg -y -f concat -safe 0 -i "' + concatFile + '" -vf "fps=10,scale=960:-1:flags=lanczos,palettegen=max_colors=200:stats_mode=diff" -update 1 "' + palette + '"', { stdio: 'inherit' });
  execSync('ffmpeg -y -f concat -safe 0 -i "' + concatFile + '" -i "' + palette + '" -lavfi "fps=10,scale=960:-1:flags=lanczos[x];[x][1:v]paletteuse=dither=bayer:bayer_scale=5" -loop 0 "' + gifOut + '"', { stdio: 'inherit' });

  var mb = (fs.statSync(gifOut).size / 1024 / 1024).toFixed(1);
  console.log('\n✅ GIF ->', gifOut, '(' + mb + ' MB)');
})().catch(function(e) { console.error('Fatal:', e); process.exit(1); });
