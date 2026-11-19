[![English](https://img.shields.io/badge/English-Active-2563eb?style=flat-square)](./privacy.md) [![한국어](https://img.shields.io/badge/한국어-Korean-475569?style=flat-square)](./ko/privacy-details.md)

# Privacy and Data Policy

## Summary

**By default, Lattice AI collects no telemetry and transmits no user data
externally.** External features explicitly chosen by the user—such as cloud
models, model downloads, web page reading, Telegram, or Brain Network—communicate
only with their configured targets.

All data is stored exclusively on the user's local machine.

## Data Stored Locally

| Data | Storage Location | Description |
|------|------------------|-------------|
| User accounts | `~/.ltcai/users.json` | Name, scrypt-hashed password, role |
| Session tokens | `~/.ltcai/sessions.json` | SHA-256 token hash, expiry/refresh timestamps (no plaintext bearer stored) |
| Chat history | `~/.ltcai/chat_history.json` | User-AI conversation content |
| Knowledge Graph | `~/.ltcai/knowledge_graph.sqlite` | Chat/document/web/tab nodes, edges, and provenance |
| Uploaded / ingested files | `~/.ltcai/knowledge_graph_blobs/` | Original PDF/DOCX/web text blobs |
| Ingestion provenance | `~/.ltcai/knowledge_graph.sqlite` (`ingestion_provenance`) | Source, timestamp, and processing metadata per node — never sent externally |
| Graph exports / backups | `~/.ltcai/workspace_exports/` | Local export/backup files created by the user (no cloud dependency) |
| Knowledge Garden | `~/.ltcai-brain/` | P-Reinforce classification store |
| Configuration | `~/.ltcai/config.json` | Model settings; API keys stored via OS keyring |

## Data Not Collected

- Usage statistics, session length, or click events
- Error reports (written only to local `server.log`)
- Device identifiers or IP addresses
- Prompt or response content

## When Using Cloud Models

If you configure an API key for OpenAI, Groq, Together, OpenRouter, or another
provider and select a cloud model, prompts and responses travel through that
provider's servers and are governed by their privacy policies:

- OpenAI: https://openai.com/privacy
- Groq: https://groq.com/privacy-policy
- Together AI: https://www.together.ai/privacy
- OpenRouter: https://openrouter.ai/privacy

When using local Apple Silicon MLX models, prompts never leave your machine.

## Telegram and Permission Notifications

The Telegram bridge only processes messages and callback queries from chat IDs
explicitly registered in `LATTICEAI_TELEGRAM_ALLOWED_CHAT_IDS` when enabled by
the user. Local Lattice API calls require a dedicated
`LATTICEAI_SERVER_SESSION_TOKEN` and never read bearer tokens out of the session
store file.

Permission request notifications (e.g., Discord) include only a short hint
rather than the full approval token. Operators may optionally configure
`LATTICEAI_PERMISSION_UI_URL` to link to the review page, but the approval token
itself is never embedded in the message or URL.

## Web and Browser Ingestion (v3.6.0)

- **URL reader** (`/api/browser/read-url`): The **local runtime directly**
  fetches a URL explicitly requested by the user, extracts text, and indexes it
  into the graph. Lattice AI never crawls autonomously and never forwards
  fetched pages externally. Private/local addresses and DNS rebinding are
  blocked, every redirect is re-validated, and only textual responses up to
  4 MB are streamed.
- **Browser tab capture** (`/api/browser/ingest-current-tab`) and the Manifest
  V3 extension: The extension transmits **exclusively to `127.0.0.1` (local)**.
  There is no cloud endpoint (verifiable via the single `fetch` target in
  `browser-extension/`).

## Graph Export and Backup (v3.6.0)

Knowledge Graph export/import and backup/restore operate **entirely locally**
and require no cloud service. Moving or sharing exported files is the user's
responsibility.

## API Key Security

- API keys are stored in the OS keyring (macOS Keychain, Windows Credential
  Manager, Linux Secret Service).
- Keys are never written to disk in plaintext unless
  `LATTICEAI_ALLOW_PLAINTEXT_API_KEYS=true` is explicitly set.
- API key, token, and password patterns are automatically masked before saving
  chat history.

## Deleting Data

```bash
# Delete chat history only
rm ~/.ltcai/chat_history.json

# Delete Knowledge Graph
rm ~/.ltcai/knowledge_graph.sqlite
rm -rf ~/.ltcai/knowledge_graph_blobs/

# Delete all data
rm -rf ~/.ltcai/
rm -rf ~/.ltcai-brain/
```

In the web UI: use `/clear` to clear the active conversation, or manage per-user
data from the admin panel.

## Public Deployments

If you deploy Lattice AI in an environment accessible to external users, clearly
disclose to your users:

1. What data is stored on the server
2. Which cloud AI providers are used
3. How long chat contents are retained and how users can delete them

Lattice AI itself is not responsible for the data-handling practices of public
deployment operators.

## Contact

Privacy inquiries: rnlgnquvk@gmail.com
