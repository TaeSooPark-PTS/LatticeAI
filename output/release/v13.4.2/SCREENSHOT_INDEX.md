# v13.4.2 Release Evidence

Captured from the built React/Vite app served by the release visual API on 2026-11-23T17:41:51.982Z.

## Build Binding

Evidence is only trustworthy while this fingerprint matches
`static/app/asset-manifest.json` **and** the mock API surface
(`tests/visual/mock_server.cjs + tests/visual/mock_server/*.cjs`).
A later `build:assets` or mock-server edit without recapture invalidates the
screenshots even when their mtimes look fresh.

- asset-manifest.sha256: `fa9811877880f087b677428b50134e3a44b470be0af4037d215fe9afc15e919a`
- asset-manifest.mtime: `2026-11-23T17:40:38.940Z`
- asset-manifest.bytes: 4105
- mock-server.sha256: `b1b691f95c8d7fffe3fcb592a6c4ef263da669be86c07a8afb3e10be11c954f3`
- mock-server.mtime: `2026-11-17T11:27:21.484Z`
- mock-server.bytes: 100580
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

- [v13.4.2-living-brain-walkthrough.webm](videos/v13.4.2-living-brain-walkthrough.webm)
- [v13.4.2-living-brain-walkthrough.gif](gifs/v13.4.2-living-brain-walkthrough.gif)
