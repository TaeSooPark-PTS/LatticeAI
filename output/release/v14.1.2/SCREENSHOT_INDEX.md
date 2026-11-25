# v14.1.2 Release Evidence

Captured from the built React/Vite app served by the release visual API on 2026-11-25T10:26:20.896Z.

## Build Binding

Evidence is only trustworthy while this fingerprint matches
`static/app/asset-manifest.json` **and** the mock API surface
(`tests/visual/mock_server.cjs + tests/visual/mock_server/*.cjs`).
A later `build:assets` or mock-server edit without recapture invalidates the
screenshots even when their mtimes look fresh.

- asset-manifest.sha256: `7fb1b01d9b12c88272a595ba7c24f0ae71605c4239e9c5a0b8490be66e3050c9`
- asset-manifest.mtime: `2026-11-25T10:26:03.474Z`
- asset-manifest.bytes: 4362
- mock-server.sha256: `59d3c949e8818203ba624e92c1b083cde7d023e9680aa430e0c10dd151056647`
- mock-server.mtime: `2026-11-25T10:07:48.205Z`
- mock-server.bytes: 90433
- mock-server.files: 10

## Screenshots

| File | Flow |
| --- | --- |
| [01-login.png](screenshots/01-login.png) | Login |
| [02-recommended-models.png](screenshots/02-recommended-models.png) | Recommended Models |
| [03-install-load-progress.png](screenshots/03-install-load-progress.png) | Install & Load progress |
| [04-brain-chat-home.png](screenshots/04-brain-chat-home.png) | Brain Chat home |
| [05-memory-graph.png](screenshots/05-memory-graph.png) | Memory Graph |
| [06-capture.png](screenshots/06-capture.png) | Add Sources |
| [07-model-library.png](screenshots/07-model-library.png) | Model Library |
| [08-system.png](screenshots/08-system.png) | System |
| [09-automation-runs.png](screenshots/09-automation-runs.png) | Automation runs, named and status-spoken |
| [10-admin-console.png](screenshots/10-admin-console.png) | Separate Admin Console |
| [11-knowledge-journey.png](screenshots/11-knowledge-journey.png) | Material-to-memory steps |
| [12-review-center.png](screenshots/12-review-center.png) | Automation Review Center |
| [13-chronicle.png](screenshots/13-chronicle.png) | Brain Chronicle — growth, activity, one day's story |

## Motion Evidence

- [v14.1.2-living-brain-walkthrough.webm](videos/v14.1.2-living-brain-walkthrough.webm)
- [v14.1.2-living-brain-walkthrough.gif](gifs/v14.1.2-living-brain-walkthrough.gif)
