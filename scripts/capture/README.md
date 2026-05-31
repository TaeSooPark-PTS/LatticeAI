# Screenshot Capture

Reproducible Playwright capture scripts for release screenshots.

Prerequisites:

```bash
npm ci
npx playwright install chromium
LTCAI
```

Optional environment:

- `LTCAI_CAPTURE_BASE_URL` defaults to `http://localhost:4825`
- `SESSION_TOKEN` or `LTCAI_SESSION_TOKEN` injects an authenticated session cookie
- `LTCAI_CAPTURE_OUT` defaults to `docs/images`
- `LTCAI_CAPTURE_HEADED=1` runs with a visible browser

Commands:

```bash
npm run capture:workspace
npm run capture:graph
npm run capture:skills
npm run capture:enterprise
npm run capture:onboarding
```
