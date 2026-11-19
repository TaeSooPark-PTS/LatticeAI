[![English](https://img.shields.io/badge/English-Active-2563eb?style=flat-square)](./SURFACE_PARITY.md) [![한국어](https://img.shields.io/badge/한국어-Korean-475569?style=flat-square)](./ko/SURFACE_PARITY.md)

# Lattice AI — Surface Parity Matrix

> **Status: reference**
> Introduced: v9.9.4 (2026-07-25, Review Wave 1.4 "Surface Parity Checklist").
> Updated: v9.9.7 — closed **every** remaining `✖` gap in the table.
> Updated: **v10.4.0 — closed every remaining `◐` (partial) entry.** No `◐` remains in the matrix.
> Purpose: **honestly** record how much of the Brain contract each surface provides.
> This is the reference contract preventing "a different Lattice per app" without overstating capabilities.

## Surface Definitions

| Surface | Entry Point | Role |
| --- | --- | --- |
| Web `/app` | SPA served by the local host (`static/app`) | Primary surface — baseline for every capability |
| Desktop | Tauri shell (`src-tauri/`) | Shell wrapping the same SPA — structurally identical to Web |
| VS Code | `vscode-extension/` (`ltcai.*` commands) | Editor companion surface |
| Browser | `browser-extension/` | Capture + recall + pending-approval visibility (v9.9.7) |
| Telegram | Bot adapter | Conversation-only surface |

## Four Core Loop Moments × Surfaces

`✅` Supported · `◐` Partial · `—` Not offered (intentional) · `✖` Gap (backlog)

| Loop Moment | Web `/app` | Desktop | VS Code | Browser | Telegram |
| --- | --- | --- | --- | --- | --- |
| First Capture | ✅ Drag-and-drop, folder & web ingest, quality warnings | ✅ (same as Web) | ✅ `sendToLattice` file/selection + `captureFolder` folder ingest (v10.4.0) | ✅ Page capture (focused purpose) | ✅ Messages + document/photo upload ingest |
| First Recall | ✅ Hybrid search + grounding badge + source→chunk | ✅ (same) | ✅ `askCurrentFile` / `askBrain` — same `/chat` grounding badge | ✅ Popup Q&A + same grounding badge (v9.9.7) | ✅ Answer + grounding badge (v9.9.7) |
| First Artifact | ✅ `artifacts[]` cards, preview, Brain memory chip | ✅ (same) | ✅ `createFile` + `showArtifacts` cards (with repair/validation flags, v10.4.0) | — (intentional: browser never writes local files) | ✅ Files + `artifacts[]` card summary (v10.4.0) |
| First Protection (Approval) | ✅ Approval card + TTL countdown + restart persistence | ✅ (same) | ✅ List / Approve / Reject commands + token cache | ✅ Pending count badge (approve in Web, v9.9.7) | ✅ Inline Done/Cancel + `run_id`/token resume |

## Feature Detail × Surfaces

| Feature | Web `/app` | Desktop | VS Code | Browser | Telegram |
| --- | --- | --- | --- | --- | --- |
| Agent step timeline (v9.9.4) | ✅ Live SSE | ✅ | ✅ `runAgentLive` — same `agent_step` frames from `POST /agent` `stream:true` (v9.9.7) | — (intentional) | — (intentional) |
| Plain-language run explanation (v9.9.6) | ✅ Message footer note | ✅ | ✅ `runAgent` output channel | — | ✅ Post-answer summary message |
| Evidence → Action one-click (v9.9.6) | ✅ Answer evidence card | ✅ | ✅ `ltcai.evidenceActions` (v9.9.7) | — (intentional) | — (intentional) |
| Knowledge Garden 4 beds (v9.9.7) | ✅ Home panel | ✅ | — (intentional) | — (intentional) | — (intentional) |
| Per-folder memory status (v9.9.7) | ✅ Capture card | ✅ | — (intentional) | — (intentional) | — (intentional) |
| Voice memo capture (v9.9.7) | ✅ `POST /api/capture/voice` | ✅ | — (intentional) | — (intentional) | — (intentional) |
| Approval resume (`/agent/resume`) | ✅ | ✅ | ✅ `ltcai.approveAgent` / `rejectAgent` | — | ✅ callback → resume |
| Pending approvals list (`GET /agent/approvals`, v9.9.4) | ✅ | ✅ | ✅ `ltcai.listApprovals` | — | — (bot-local pending map) |
| Watch status signal (v9.9.4) | ✅ Home card | ✅ | — | — | — |
| Review Center (proposal approve/reject) | ✅ | ✅ | ✅ `ltcai.reviewCenter` (honestly reports 409 conflicts) | — (intentional: approve in Web/editor/bot) | ✅ `/review` + inline approve/reject (v9.9.7) |
| Demo corpus First Value Loop | ✅ | ✅ | — | — | — |
| Model load / recommendation | ✅ | ✅ | ✅ `loadModel` + hardware-based recommendation & rationale (v10.4.0) | — (intentional) | — (intentional) |
| Code editing assistant (edit/explain/refactor/tests) | — | — | ✅ | — | — |

## Rules

1. **The API contract is the source of truth.** Every surface consumes the same
   sidecar/host API. Never invent surface-specific private APIs.
2. **`◐` and `✖` exist only when documented.** Whenever a new feature lands in
   Web, update this matrix and mark every other surface explicitly as
   `— (intentional)` or `✖ (gap)`.
3. **`✖` gaps are candidates for Honest Limitations in release notes.** After
   v9.9.7 no `✖` remains in the table, and after v10.4.0 no `◐` remains.
   - Every remaining `—` is an intentional **design boundary**, with its
     rationale stated inline. For example, the browser extension does not write
     local files so it has no artifact generation, and approval *decisions*
     require signed tokens so it shows pending counts while decisions happen in
     Web, the editor, or the bot.
   - When introducing a new boundary, record *why* it is excluded here. An
     unexplained `—` is a gap, not a boundary.
4. Review this table on every release (during the docs step of the release
   checklist).

## VS Code / Telegram Approval Notes (v9.9.5)

- **Tokens are never re-issued by `GET /agent/approvals`** (security). Web
  retains the pause-response token client-side, and VS Code likewise keeps the
  pause-response token in the extension session cache. If it is not in cache,
  the UI prompts the user to paste it or switch to the Web UI.
- **Telegram** handles both `waiting_approval` (legacy `human_in_loop`) and
  `awaiting_approval` (token-based) via inline keyboards, prioritizing
  `run_id` + `token` on resume.

## VS Code Recall & Review Notes (v9.9.6)

- **Recall badge**: `askCurrentFile` and `askBrain` read the exact `grounding`
  verdict from `POST /chat` just like Web. If the verdict is missing, they
  never promote it to "grounded" and instead report `unknown`
  (`vscode-extension/surface.ts::groundingBadge`).
- **Review Center**: `ltcai.reviewCenter` uses the same `/api/proposals`
  surface as Web. Approval applies the exact reviewed diff, and a `409` (file
  changed since staging) honestly tells the user that nothing was written.
- **Step summary**: `ltcai.runAgent` summarizes `steps`, `created_files`, and
  `explanation` in the output channel after completion. (Live SSE timelines
  were added in v9.9.7 via `ltcai.runAgentLive` using the same `agent_step`
  frames — this note captured the v9.9.6 state.)
- Parsing contracts are pinned verbatim against sidecar payload shapes in
  `tests/vscode-extension.test.cjs`.

## v9.9.7 — How the Remaining Gaps Were Closed

- **VS Code live steps**: `POST /agent` gained `stream: true`, emitting the
  **exact same** `agent_step` frames Web received over the chat path, with a
  terminal payload identical to the JSON response
  (`tests/unit/test_agent_stream_parity.py` pins parity between both paths).
- **VS Code evidence → action**: The extension remembers `grounding.cited` from
  recall responses and sends it to `/api/evidence/actions`. File-creating
  actions route through the agent to produce real artifacts; conversational
  actions route to the chat panel.
- **Telegram recall badge / Review Center**: Renders server verdicts verbatim
  and consumes `/api/proposals` directly via `/review`, reporting `409`
  honestly as "nothing was written."
- **Browser extension**: Extended beyond capture-only to support `/chat`
  questions and pending `agent/approvals` counts. **It never computes verdicts
  locally** — missing verdicts display as "unable to verify grounding" and are
  never promoted to "grounded."

## v10.4.0 — How the Last Three `◐` Entries Were Closed

All three gaps were **rendering gaps, not contract gaps**. The sidecar already
returned the required data, and the editor/bot had been discarding it.

- **VS Code folder ingest** (`ltcai.captureFolder`): Uses the **same**
  `POST /api/ingestion/folder` endpoint as Web Capture. Because it reads local
  disk, the approval flow is identical — an initial call without a token
  returns `permission_required`, which the extension displays verbatim (never
  retrying silently). Work is queued as a background job with progress visible
  in Web.
- **VS Code artifact cards** (`ltcai.showArtifacts`): Reads `artifacts[]` from
  the `/agent` response verbatim. Crucially, **repaired scaffolds are
  distinguished from clean model output** — previously a flat path list made
  them look identical. The extension never promotes a server `valid=false`
  verdict to success, and older sidecars returning only `created_files` are
  honestly labeled as having no metadata details.
- **VS Code model recommendation**: Reads `zero_config.recommend` from
  `GET /setup/scan` to show **why this model fits this machine** with concrete
  rationale. When no scan exists, no banner appears — reasons are never
  fabricated.
- **Telegram artifact cards**: Summarizes the same `artifacts[]` flags right
  before sending files, listing up to 8 entries and honestly truncating the
  rest as `… +N`.

Contract tests: `tests/vscode-extension.test.cjs` (19),
`tests/unit/test_telegram_security.py`. Every test asserts exact sidecar
payload shapes and verifies that extensions and bots never recompute server
verdicts.
