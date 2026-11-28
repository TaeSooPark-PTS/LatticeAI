# v14.7.0 Release Evidence

Captured from the built React/Vite app served by the release visual API on 2026-11-28T01:35:06.325Z.

## Build Binding

Evidence is only trustworthy while this fingerprint matches
`static/app/asset-manifest.json` **and** the mock API surface
(`tests/visual/mock_server.cjs + tests/visual/mock_server/*.cjs`).
A later `build:assets` or mock-server edit without recapture invalidates the
screenshots even when their mtimes look fresh.

- asset-manifest.sha256: `64e529c410c4d4f87b768cb247d2296fab636567db1ff533c6d48ea1a970009c`
- asset-manifest.mtime: `2026-11-28T01:34:18.603Z`
- asset-manifest.bytes: 5160
- mock-server.sha256: `aa624e7bf06984117a1b20440fcdea646f36432b9263899726823b9fdef50b3e`
- mock-server.mtime: `2026-11-27T15:18:50.820Z`
- mock-server.bytes: 96900
- mock-server.files: 11

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

- [v14.7.0-living-brain-walkthrough.webm](videos/v14.7.0-living-brain-walkthrough.webm)
- [v14.7.0-living-brain-walkthrough.gif](gifs/v14.7.0-living-brain-walkthrough.gif)
