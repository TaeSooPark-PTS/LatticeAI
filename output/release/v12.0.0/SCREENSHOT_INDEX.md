# v12.0.0 Release Evidence

Captured from the built React/Vite app served by the release visual API on 2026-08-18T07:34:56.650Z.

## Build Binding

Evidence is only trustworthy while this fingerprint matches
`static/app/asset-manifest.json` **and** the mock API surface
(`tests/visual/mock_server.cjs + tests/visual/mock_server/*.cjs`).
A later `build:assets` or mock-server edit without recapture invalidates the
screenshots even when their mtimes look fresh.

- asset-manifest.sha256: `57f02e48562677ae349ee657a502e6c8a8e98c4ad4b8c25b71f87ba1d3de8a43`
- asset-manifest.mtime: `2026-08-18T07:29:08.704Z`
- asset-manifest.bytes: 3679
- mock-server.sha256: `00454e913af71fa7b0c4f8d122040b546bec5479fb0b6e64531336b525c68f1b`
- mock-server.mtime: `2026-08-10T23:25:01.151Z`
- mock-server.bytes: 98621
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

- [v12.0.0-living-brain-walkthrough.webm](videos/v12.0.0-living-brain-walkthrough.webm)
- [v12.0.0-living-brain-walkthrough.gif](gifs/v12.0.0-living-brain-walkthrough.gif)
