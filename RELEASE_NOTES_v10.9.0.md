# v10.9.0 — Never Blocks (2026-08-05)

Lattice AI runs one event loop on your machine. Through 10.8.0, several things
you can ask it to do were performed *on* that loop: pulling an Ollama model,
installing an engine, sampling CPU and RAM for the System screen. While any of
them ran, the server could not answer anything else — not another chat stream,
not `/health`, not the UI trying to find out what was happening.

Downloading a model has a fifteen-minute timeout. That was, quite literally,
how long the whole product could be frozen by one person clicking one button.

10.9.0 is about that, and about three smaller things a review turned up: a
focus ring nobody could see, an organism that kept thinking after it had
answered, and errors that still arrived in whichever language the endpoint's
author happened to be thinking in.

---

## The server no longer blocks itself

Five call paths did long, blocking work directly inside an `async def`:

| Path | What it was doing on the loop | Worst case |
| --- | --- | --- |
| `POST /engines/pull-model` | `ollama pull`, and a Hugging Face weights download | 15 min |
| `POST /engines/install` | engine installer subprocess | 15 min |
| `POST /engines/prepare-model` | engine install + weight download + local server start | 15 min |
| `POST /mcp/install` | `pip install` / `npm install -g` | 15 min |
| `GET /local/sysinfo` | `top -l 1`, `vm_stat`, `sysctl` | ~2 s, and it is polled |

The last one is the one most people would have met without knowing it:
`/local/sysinfo` is read by the System screen *and* by first-run analysis, so
the two-second freeze landed exactly when an answer was most likely to be
streaming.

Each now runs on a worker thread — the same `asyncio.to_thread` hop the rest of
the codebase already used, which is what made these stand out as omissions
rather than decisions. The streaming variant of model preparation
(`prepare_and_load_model_stream`) had always done this correctly; the
non-streaming one had not.

Two things keep it fixed:

- **A lint gate.** `ruff`'s `ASYNC210/220/221/222/230/251` rules are now in the
  selected set. `ASYNC240` (pathlib) is deliberately left out: those sites are
  single `exists()` / `is_file()` stat calls, where a thread hop costs more
  than the syscall it avoids.
- **A test that measures the loop, not the syntax.**
  `tests/unit/test_event_loop_not_blocked.py` runs a ticker coroutine while the
  handler works and asserts both that the ticker kept running and that the
  blocking body executed on a different thread. Reverting either fix fails it;
  satisfying the linter by hiding `subprocess.run` one call deeper does not.

The Telegram bridge's file uploads got the same treatment — a screenshot was
being read into memory on the loop before it was posted.

## A focus ring you could not see

10.8.0's visual pass added `border-color` to the transition list on the capture
pills under the composer. Focus rings are the one thing that may not ease: the
border now faded from grey to teal over 150ms, so at the instant focus landed —
which is the instant it matters, and the instant anything measuring it looks —
the pill still looked idle. The file's own comment, five rules further down,
says exactly this. The rule that overrode it did not read it.

`border-color` is out of that transition again. The visual suite already had
the assertion; it had been red on `main` since the commit that broke it.

## An organism that kept thinking after it answered

Mid-answer, a retrieval trace makes the Brain pulse "recalling" and parks a
900ms timer to put it back to "thinking". If the answer finished inside that
window, the timer still fired — and the Brain sat visibly thinking about a
question it had already answered, until the next keystroke.

The stream ending is the end of thinking, so the timer is now cancelled with
it. (While there: `stopStreaming` no longer loses its handle when two sends
race into the same tick.)

## One language, further in

10.8.0 built the server-side message catalog and migrated three routers. This
release migrates fourteen more — the everyday path: chat, chat history, chat
intents, memory, the knowledge graph, local files, portability, the review
queue, projects, the network boundary, models, tools, MCP and setup. The catalog
went from 25 messages to 74; every one of the 49 additions replaced a literal at
a raise site and carries both languages, resolved from the request.

Three routers were the acute case: `models.py`, `mcp.py` and `tools.py` each
answered *some* errors in Korean and others in English, so a single screen could
show both.

Two gates keep the migrated set migrated: `scripts/check_server_i18n.mjs` (in
`npm run lint`, rejects a literal in either language) and the widened
`MIGRATED_ROUTERS` list in `tests/unit/test_server_messages.py`. A third test
fails if the two lists ever disagree — either gate alone can be satisfied while
the other is not.

Thirty-three routers are still unmigrated, and are honestly not claimed to be:
adding a router to the list is how a migration is declared finished.

## The welcome screen fits

10.8.0 shipped this as a known limitation: the welcome step measured 770px
against a 747px viewport, so the closing line — the one that tells a first-time
visitor what they are agreeing to — sat 8px under the fold, and the product's
first screen opened with a scrollbar.

A tighter `@media (max-height: 820px)` tier gives back the ~25px from the
whitespace between the pieces. Nothing was removed. It now measures 747px in a
747px viewport, with the closing note 33px clear of the bottom, and
`tests/visual/v3.spec.js` pins it at that height and at 800px.

## The streaming chat path has tests

`useBrainChat` was 12% covered — the largest untested surface in the product,
and the one every answer goes through. The reason was mechanical: asserting
anything about a stream needs *frames arriving over time*, which
`mockResolvedValue` cannot express.

`frontend/src/test/fakeChatStream.ts` is that missing harness. A test writes the
frames it wants; the fake replays them into the same handler callbacks the real
reader calls, pausing where asked so the test can inspect mid-stream state.
Eleven cases now cover an answer building up token by token, a recall pulse, the
stop button, a refusal, grounding badges, live `agent_step` frames, the
no-model path, a second send being refused mid-stream, regenerate, and
conversation continuity — 11.92% → 53.15% statement coverage on that file.

---

## Verification

- Python: 2,269 passed, 11 skipped (2,219 in 10.8.0) · `ruff` clean, with the
  new ASYNC rules · `mypy` clean (276 files)
- Frontend: 515 passed (504 in 10.8.0) · `tsc --noEmit` clean · bundle budget,
  i18n literal, i18n namespace, legacy-debt and server-i18n gates green
- Python coverage 72.80% against a 70% floor
- Visual: 33 Playwright specs passed, including the new welcome-fold test.
  The capture-pill focus assertion already existed — it was the one failing on
  `main` before this release.
- Extensions: browser 22 passed, VS Code 19 passed

## Honest limitations

- Thirty-three API routers still raise English literals. They are listed as
  unmigrated rather than quietly claimed; `scripts/check_server_i18n.mjs`
  prints the count on every lint run.
- Frontend statement coverage is 53.9%, not 80% (52.26% in 10.8.0).
  `useBrainChat` moved from 11.92% to 53.15% — the harness now exists, but the
  approval-resume and proactive-action branches are still untested.
  `useBrainIngestion` (12%), `IngestionPanels` (17%) and `Brain.tsx` (37%) are
  the next largest gaps.
- The worker-thread hop makes a long download stop freezing the server; it does
  not make it *cancellable*. Closing the tab mid-pull still leaves the pull
  running to completion.
- The browser extension still resolves its language from the browser, not from
  the web app's setting: the popup is a separate origin and cannot read it.
  Closing this needs a server-side preference, which this release does not add.
- `05-memory-graph.png` differs 3.09% from the 10.8.0 baseline without this
  release claiming a change there. The graph screenshot renders a
  force-directed layout and is not byte-stable between captures.

## Artifacts

- `dist/ltcai-10.9.0-py3-none-any.whl`
- `dist/ltcai-10.9.0.tar.gz`
- `ltcai-10.9.0.tgz`
- `dist/ltcai-10.9.0.vsix`
- `src-tauri/target/release/bundle/dmg/Lattice AI_10.9.0_aarch64.dmg`
