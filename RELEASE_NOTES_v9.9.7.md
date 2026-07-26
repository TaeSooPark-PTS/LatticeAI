# Lattice AI 9.9.7 — No Gaps Left

**Release date:** 2026-07-27

9.9.6 closed the loudest surface-parity gaps and wrote the rest down honestly:
five `✖` entries in `docs/SURFACE_PARITY.md`, plus a set of "design boundary"
notes explaining what each surface deliberately did not do. 9.9.7 takes that
list at face value and closes **every** entry — including the boundaries.

The parity matrix now contains no `✖` at all. Every remaining `—` is a design
boundary that states *why*, because a "—" without a reason is a gap wearing a
better hat.

## Highlights

### 1. VS Code: live step timeline + evidence → action

- **`POST /agent` now streams.** `stream: true` emits the same named
  `agent_step` frames the web app already received through the chat route, and
  the stream's terminal payload is identical to the JSON response — a client
  that ignores named events sees the historical shape.
  `tests/unit/test_agent_stream_parity.py` pins that equivalence.
- **`Lattice AI: Run Agent Task (Live Steps)`** writes each frame to the output
  channel as it happens instead of reporting only after the run.
- **`Lattice AI: Build From This Evidence`** remembers what the last recall
  actually cited and turns it into the same one-click follow-ups the web card
  offers. File-producing actions run through the agent so a real artifact
  lands; chat actions open in the panel.

### 2. Telegram: grounding badge + Review Center

- Answers now carry the server's own grounding verdict. An absent verdict
  renders as `❔ 근거 확인 불가` — never promoted to 근거 있음.
- **`/review`** lists staged change proposals from the same `/api/proposals`
  surface with inline approve/reject. A 409 reports "the file changed since
  staging, nothing was written" rather than retrying behind your back.

### 3. The browser extension is no longer capture-only

It was documented as a deliberate boundary; it is now a real surface:

- **Ask your Brain** from the popup, with the same grounding badge. The
  extension never computes a verdict locally — no verdict means "근거 확인 불가".
- **Pending approvals** are visible, so a paused run is not invisible from the
  browser. The approval *decision* still happens in the web app, the editor, or
  the bot, because it needs a signed short-TTL token — that boundary is now
  written down with its reason.
- Still local-only: the single `fetch` target is `127.0.0.1`, asserted by test.

### 4. Knowledge garden — four beds

`GET /api/brain/garden` answers the four questions a gardener actually asks,
from one workspace-scoped read: **최근 들어온 것 / 서로 어긋나는 것 / 오래된 것 /
자주 쓰는 것**. "Frequent" is real graph degree, not a guess, and retrieval
plumbing (Chunk nodes) is never presented as a plant. An unavailable graph
yields empty beds instead of invented ones.

### 5. A profile for small local models

`latticeai/core/agent_profiles.py` stops running one loop for every model:

- **standard** — today's behaviour, unchanged, for models that can hold a
  tool-call contract (and for every model the size heuristic does not
  recognize — the conservative default).
- **compact** — for ≤4B local models: a shorter transcript window, an earlier
  escalation to naming the valid tools, and a **direct-path fallback**. When
  JSON tool calls keep failing, the loop stops asking for JSON entirely and
  executes the plan's own file steps, requesting only file content in plain
  text. Weak models are bad at tool protocols and fine at writing a file.

A failed or staged write is reported as *not written* — the fallback never
fabricates evidence.

### 6. Folder memory state

`GET /knowledge-graph/local/health` answers "is this folder actually in my
Brain?": indexing coverage per folder, what failed, and the stored reason it
failed. An unscanned folder reports *unknown*, never "0% indexed". Vector
freshness rides along once, explicitly labelled **global**, because the vector
index is not per-folder and claiming otherwise would invent a number.

### 7. Two skills that pay off on install

- **`meeting_notes`** — pasted meeting notes → 결정사항 / 할 일 / 미해결 질문,
  saved as one markdown note that the Brain then remembers.
- **`weekly_review`** — this week's actual Brain records → a weekly review with
  a source on every line. An empty week is written as empty, not padded.

Both are governed (`risk=write`, `rollback=snapshot`), ship evals, and are
guarded by a contract test that refuses a skill whose `action` is not a
registered tool.

### 8. Voice memo capture

`POST /api/capture/voice` takes a short memo into the Brain through the
unified ingestion pipeline. Transcription is an **injected, optional local
port**:

- with a local transcriber, the memo becomes a searchable note;
- without one, the memo is still stored and the response says
  `transcription: "unavailable"` with `searchable: false`. A missing
  transcriber never produces an invented transcript, and never silently drops
  the memo.

No cloud speech API, and no model is installed behind your back — an absent
transcriber is a reported state.

## Honest limitations

- **Voice transcription ships with no bundled transcriber.** `POST
  /api/capture/voice` stores memos today and transcribes only when a local
  transcriber is wired in. `GET /api/capture/voice/status` tells you which of
  the two you have before you record anything.
- **Approval decisions stay off the browser extension** — they need a signed,
  short-TTL, single-use token bound to the pausing user. The extension shows
  that runs are waiting; you approve in the web app, the editor, or the bot.
- **The garden, folder-health, and voice surfaces are web/desktop only** by
  design; each is recorded as a boundary with its reason in
  `docs/SURFACE_PARITY.md`.
- **The compact profile is selected from the model id.** A model whose id names
  no size keeps the standard loop — the conservative choice, but it means an
  unlabelled small model needs `LATTICEAI_AGENT_PROFILE=compact`.
- **`meeting_notes` / `weekly_review` are recipes over `write_file`**, not new
  runtimes: their quality tracks the loaded model's, and their evals check the
  write contract rather than prose quality.

## Verification

- unit: 1714 tests, including new suites for `/agent` SSE parity, agent
  profiles and the direct-path fallback, the garden overview, folder memory
  health, shipped-skill contracts, and voice capture
- frontend: 141 vitest tests (garden panel, folder health card, plus the
  9.9.6 suites)
- VS Code: 13 surface tests · browser extension: 13 tests
- `agent_eval` 23/23, brain quality eval, product readiness gate
- lint / ruff / typecheck / bundle budget / OpenAPI drift / i18n / docs gates
