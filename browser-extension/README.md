# Lattice AI — browser extension (Manifest V3)

Current release: **12.2.0 — Small Voice**.

A minimal Chrome/Edge (Manifest V3) extension for your **local** Lattice AI
Knowledge Graph. Cloud stays on the host (optional, default local); this
extension still posts only to `127.0.0.1`. It does three things:

1. **Capture** — send the current page into the graph;
2. **Recall** (v9.9.7) — ask your Brain a question and see the *server's own*
   grounding verdict on the answer;
3. **Approvals** (v9.9.7) — show how many agent runs are waiting for approval,
   so a paused run is never invisible from here.

Recall and approvals use the same `/chat` and `/agent/approvals` endpoints
every other surface uses. The extension never computes a grounding verdict
itself: an absent verdict renders as "근거 확인 불가", never as "근거 있음".

## Local-first guarantee

- It posts **only** to `http://127.0.0.1:<port>` on your own machine.
- There is **no** cloud endpoint, telemetry, or external server anywhere in this
  extension. Grep the source: the single `fetch` target is `127.0.0.1`.
- `host_permissions` is restricted to `http://127.0.0.1/*` and
  `http://localhost/*`.

## What it sends

The active tab is captured (scripts/styles/SVG stripped) into:

```json
{
  "url": "https://…",
  "title": "Page title",
  "text": "readable text (≤4 MiB as UTF-8 bytes)",
  "selected_text": "current selection (optional)",
  "captured_at": "ISO-8601"
}
```

…to `POST /api/browser/ingest-current-tab`, which routes it through the unified
ingestion pipeline as `source_type=browser_tab` (see `latticeai/api/browser.py`).

## Install (developer mode)

1. Start Lattice AI locally and sign in (the extension reuses your local session
   cookie via `credentials: include`).
2. Open `chrome://extensions`, enable **Developer mode**.
3. **Load unpacked** → select this `browser-extension/` folder.
4. Click the toolbar icon, confirm the port (default `4825`), press
   **Send this page**.

The popup aborts a local ingestion request if it has not completed within 30
seconds, then restores the Send button so the capture can be retried.

## Backend contract

| Endpoint | Source type | Notes |
|---|---|---|
| `POST /api/browser/read-url` | `web_url` | The runtime fetches a public URL locally and extracts text. |
| `POST /api/browser/ingest-current-tab` | `browser_tab` | Accepts the extension payload above; size-limited and sanitized. |

Both feed the one native ingest door (`lattice-ingest` writing through
`lattice_core::graph_write`), fire `pre_tool`/`post_tool` hooks, and record
provenance (`get_provenance(node_id)`). Covered by
`rust/lattice-ingest/tests/browser_api_replay.rs`.
