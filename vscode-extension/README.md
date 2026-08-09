# Lattice AI — VS Code Extension

**Extension for the local-first Digital Brain that keeps your knowledge durable across any AI model.**

[![VS Code Marketplace](https://img.shields.io/badge/VS%20Code%20Marketplace-Install-blue?logo=visualstudiocode)](https://marketplace.visualstudio.com/items?itemName=parktaesoo.ltcai)
[![Open VSX](https://img.shields.io/open-vsx/v/parktaesoo/ltcai?label=Open%20VSX)](https://open-vsx.org/extension/parktaesoo/ltcai)
[![License](https://img.shields.io/badge/license-MIT-green)](../LICENSE)

Lattice AI connects VS Code, Cursor, and VSCodium to your local-first Digital
Brain. Use it to send files into durable Brain context, work with Brain-backed
chat, run model workflows, and trigger coding actions from the editor.

## Current Release

**11.0.0 — Full Measure** puts the whole Python surface under test: coverage 72.80% → 100.00% with a fail-under-100 CI floor, 5,426 tests, and every platform-locked branch executed on CI through injected fakes. The extension surface is unchanged.
The memory stats moved into a hover badge with a small summary graph, the six
capture chips folded behind one + on the composer, the model banner became a
status pill with the single accented CTA, and past conversations / stats / the
memory map moved onto a dock rail that opens a focus-trapped drawer instead of
stacking cards under the fold. Cards trade borders for soft shadow, and the
accent colour appears exactly twice. Alongside: the frontend test suite now
covers 100% of statements, branches, functions and lines, with vitest
thresholds and a CI coverage gate holding that floor, and the public release
history now starts at 9.0.0.

**This release does not touch the extension**; it ships unchanged apart from its
version. Nothing an editor session depends on moved: no endpoint, token, or
approval path changed, `strict` / `trusted` / `bypass` still travel over the
wire, and governed tool behaviour, the approval prompts, and the audit trail are
exactly as they were. What you will notice is on the app side — a proposal you
raise from the editor now lands in a 검토함 that leads the 작업 screen, and the
approval rows name the action in words (`파일 읽기`) instead of showing the raw
lookup key.

**10.6.1 — First Things** rebuilt the layout of five app screens. One of them
matters to anyone working between the editor and the app: a review item is
**evidence on the left, decision on the right**. A proposal raised from the
editor used to render as one long column that ended in a button row, so a large
diff pushed 승인 / 거절 off the bottom of the screen. 승인하고 적용 and 거절 now
sit in a panel that stays beside the diff, and the app's 작업 → 실행 tab leads
with **승인함** instead of finishing with it.

**10.6.0 — Promoted Panels** rebuilt the layout of the app's main screens. The
review inbox became what the app's **작업** link opens — it used to open the goal
composer — so a proposal raised from the editor is on screen as soon as you
switch over. And **작업 · AI 모델 · 설정** moved out of the app's 더보기 menu into
the top bar on a wide window; on a narrow one they are still in the menu, now
under **관리**.

**10.5.0 — Everyday Words** changed what the app calls things. The autonomy dial
the editor obeys is called **혼자 해도 되는 일** (설정 → 혼자 해도 되는 일), and its
three settings read 먼저 물어보기 / 웬만하면 알아서 / 거의 다 알아서.

**10.4.0 — Named Ground** closed the extension's last three surface-parity gaps,
and all three were rendering gaps rather than contract gaps — the sidecar was
already reporting the data and the extension was discarding it:

- **`Lattice AI: Capture Folder Into Brain`** sends a whole folder through the
  same `/api/ingestion/folder` endpoint the web Capture view uses, including
  the same per-path local-read approval. Previously the editor could only push
  the current file.
- **`Lattice AI: Show Run Artifacts`** renders `artifacts[]` as cards carrying
  the server's own flags, so a deterministically repaired scaffold no longer
  looks identical to clean model output. The extension never upgrades a
  `valid: false` verdict, and an older sidecar that reports only
  `created_files` is labelled "no artifact detail reported" rather than implied
  to be verified.
- **The model picker now explains itself** — it reads the hardware-derived
  recommendation from `GET /setup/scan` and shows the server's reasoning for
  this machine. No scan means no banner; the extension does not invent a
  reason.

10.3.0 added a local-first hybrid path: the Knowledge Graph stays on-device
while cloud LLMs become an opt-in worker. The default network boundary is
`local_only` — cloud use requires an explicit acknowledgement, only minimal
related nodes leave the machine, and streamed answers expand the local Brain
with provenance under Review Queue gates.

9.9.8 added a `strict` / `trusted` / `bypass` permission mode. Editor actions
run through the same governed tool path, so the mode set in the Lattice AI app
(설정 → 혼자 해도 되는 일) applies here too; the dial itself lives in the app
rather than in editor settings, so there is one place to raise autonomy and one
audit trail for it.

The extension follows the main app positioning: Lattice AI is a local-first
Digital Brain, not just a model launcher or editor chat panel. The 9.9.6 line closes the editor's
surface-parity gaps: recall answers carry the same grounding verdict the web
app badges (`Lattice AI: Ask Your Brain`, `Ask About Current File`), staged
change proposals can be reviewed and applied in place
(`Lattice AI: Review Center`), and `Lattice AI: Run Agent Task` reports a
run's steps, files, and plain-language outcome. 9.9.7 adds the last two
editor gaps: `Lattice AI: Run Agent Task (Live Steps)` streams the same
`agent_step` frames the web timeline renders, and
`Lattice AI: Build From This Evidence` turns the sources your last recall
actually cited into one-click follow-ups. It connects editor actions to the same
durable Brain context, explicit consent gates, replaceable model workflow, and
separated Admin surface used by the desktop app. The v9.9.0 line hardens
trust: change proposals record the original content hash and refuse to apply
over a file you edited in the meantime (atomic apply, exactly-once approval);
a verifier that can't be parsed ends as needs-review instead of a fabricated
success; every mutating tool is inventory-governed with a fail-closed CI gate;
and device analysis no longer fabricates a "ready" model card on probe failure
— while preserving the 9.8.0 honest knowledge pipeline, 9.7.0 hybrid graph
retrieval, model-agnostic file generation, fail-closed boundaries, typed
runtime and model state, exact release artifacts, and the visible VS Code sync
status and runtime architecture contract.

## Quick Start

Install and start the Lattice AI workspace:

```bash
pip install ltcai
LTCAI
```

For Apple Silicon local model support:

```bash
pip install "ltcai[local]"
```

Then install the extension:

- [VS Code Marketplace: parktaesoo.ltcai](https://marketplace.visualstudio.com/items?itemName=parktaesoo.ltcai)
- [Open VSX: parktaesoo.ltcai](https://open-vsx.org/extension/parktaesoo/ltcai)

The extension auto-connects to `http://localhost:4825`.

## What It Adds

| Area | Description |
| --- | --- |
| Chat panel | Work with local or cloud models using workspace and graph context |
| Current file actions | Ask, edit, explain, refactor, and generate tests from the active file |
| Send To Lattice | Send the current file or selection into workspace workflows |
| Sync status | See whether the editor is connected, indexing, synced, or offline |
| Model workflows | Load, switch, and use model choices managed by Lattice AI |
| Knowledge Graph | Use graph-backed context from files, notes, screenshots, and conversations |
| Multi-agent workflow | Start planner/executor/reviewer style coding workflows |
| Local-first workspace | Keep personal work local while supporting organization workspace patterns |
| Native v4 app | Use `/app` as the primary product shell |

## Commands

| Command | Shortcut | Description |
| --- | --- | --- |
| Lattice AI: Open Chat | `Cmd+Shift+A` | Open the workspace chat panel |
| Lattice AI: Edit Selection | `Cmd+Shift+E` | Rewrite selected code |
| Lattice AI: Load Model | `Cmd+Shift+M` | Pick a local or cloud model |
| Lattice AI: Explain Selection | Right-click menu | Explain selected code |
| Lattice AI: Refactor Selection | Command palette | Refactor selected code |
| Lattice AI: Generate Tests | Command palette | Generate focused tests |
| Lattice AI: Send To Lattice | Command palette | Record file context in Lattice AI |
| Lattice AI: Ask About Current File | Command palette | Ask with current file context |
| Lattice AI: Show Sync Status | Command palette / status bar | Inspect the editor-to-app bridge state |
| Lattice AI: Generate Terminal Command | Right-click menu | Generate a shell command |
| Lattice AI: Save to Knowledge Garden | Right-click menu | Save a snippet or note |

## Model Workflow

Lattice AI supports local and cloud model choices:

- MLX-VLM on Apple Silicon for current multimodal local models, with MLX-LM
  retained as a text fallback only for standard Gemma 4 metadata.
- LM Studio, vLLM, llama.cpp, and Ollama-compatible local paths.
- OpenAI, OpenRouter, Groq, Together AI, and OpenAI-compatible endpoints.
- Model cards disclose maker country, maker company, run mode, internet usage,
  model name, HF verification status, download/load strategy, and hardware fit.

## Settings

| Setting | Default | Description |
| --- | --- | --- |
| `ltcai.serverUrl` | `http://localhost:4825` | Lattice AI workspace URL |
| `ltcai.autoLoadModel` | `false` | Load the default model on extension activation |
| `ltcai.defaultModel` | empty | Optional default model id |

Remote or tunnel server example:

```json
{
  "ltcai.serverUrl": "https://your-server.example"
}
```

## Links

- [Project README](https://github.com/TaeSooPark-PTS/LatticeAI)
- [PyPI](https://pypi.org/project/ltcai/)
- [npm](https://www.npmjs.com/package/ltcai)
- [GitHub Releases](https://github.com/TaeSooPark-PTS/LatticeAI/releases)
- [Changelog](https://github.com/TaeSooPark-PTS/LatticeAI/blob/main/docs/CHANGELOG.md)
- [Security Policy](https://github.com/TaeSooPark-PTS/LatticeAI/blob/main/SECURITY.md)

## License

MIT — [TaeSoo Park](https://github.com/TaeSooPark-PTS)
