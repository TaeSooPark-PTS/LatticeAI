# Send to Lattice AI — browser extension (Manifest V3)

A minimal Chrome/Edge (Manifest V3) extension that sends the current page into
your **local** Lattice AI Knowledge Graph.

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
  "text": "readable text (≤4MB)",
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
4. Click the toolbar icon, confirm the port (default `8000`), press
   **Send this page**.

## Backend contract

| Endpoint | Source type | Notes |
|---|---|---|
| `POST /api/browser/read-url` | `web_url` | The runtime fetches a public URL locally and extracts text. |
| `POST /api/browser/ingest-current-tab` | `browser_tab` | Accepts the extension payload above; size-limited and sanitized. |

Both feed `IngestionPipeline.ingest`, fire `pre_tool`/`post_tool` hooks, and
record provenance (`get_provenance(node_id)`). Covered by
`tests/unit/test_browser_ingestion.py`.
