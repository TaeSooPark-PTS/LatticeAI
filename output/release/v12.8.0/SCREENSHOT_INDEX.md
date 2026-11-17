# v12.8.0 Release Evidence

Captured from the built React/Vite app served by the release visual API on 2026-11-17T00:00:59.670Z.

## Build Binding

Evidence is only trustworthy while this fingerprint matches
`static/app/asset-manifest.json` **and** the mock API surface
(`tests/visual/mock_server.cjs + tests/visual/mock_server/*.cjs`).
A later `build:assets` or mock-server edit without recapture invalidates the
screenshots even when their mtimes look fresh.

- asset-manifest.sha256: `e48ec3faeb6b0afd2a00f4352bc2bf362b5864873c7b8fc2f5a63d52cc613d0d`
- asset-manifest.mtime: `2026-11-17T00:00:17.561Z`
- asset-manifest.bytes: 3697
- mock-server.sha256: `d8aec290f51392534d4f91ff9ad0b2af16c1e98f2aadad8b34fb6ccbab120e55`
- mock-server.mtime: `2026-11-11T13:16:30.688Z`
- mock-server.bytes: 99796
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

- [v12.8.0-living-brain-walkthrough.webm](videos/v12.8.0-living-brain-walkthrough.webm)
- [v12.8.0-living-brain-walkthrough.gif](gifs/v12.8.0-living-brain-walkthrough.gif)
