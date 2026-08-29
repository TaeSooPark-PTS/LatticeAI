# Lattice AI v9.2.0 — Model-Agnostic File Generation

> **Status: historical** — point-in-time release note.

Released: 2026-07-20

9.2.0 makes "create a file" work reliably with **any** loaded LLM. Small local
models (gemma/qwen class) previously produced broken HTML files or saved chat
wrappers ("Sure! Here is your page: ```html …") as file bytes; file requests
without an explicit filename fell into the model-driven agent JSON loop that
weak models cannot drive. Every file request now runs through a
model-agnostic pipeline that guarantees a structurally valid file.

## File generation pipeline (`latticeai/core/file_generation.py`)

The pipeline treats the model as an untrusted content source:

1. **Prompt** — extension-aware strict instructions anchored with the exact
   first line the reply must start with (`<!DOCTYPE html>`, `{`, `#!/bin/sh`).
   Small models follow concrete anchors far more reliably than abstract rules.
2. **Extract** — recover the real payload from Markdown fences (largest
   matching-language block, tolerant of unclosed fences), strip
   `<think>`/`<reasoning>` blocks, drop leading/trailing conversational lines
   in English and Korean, and slice known document boundaries
   (`<!DOCTYPE …</html>`, largest parseable JSON value).
3. **Validate** — per-type structural checks: HTML must be a complete
   document, JSON must parse, CSS needs rule blocks, code files must be
   fence-free, and refusal/chat replies are rejected.
4. **Retry** — one corrective attempt that tells the model exactly why the
   previous output was rejected.
5. **Repair** — deterministic fallback that always yields a valid file:
   truncated HTML documents are closed, fragments are embedded in a proper
   scaffold, plain text is escaped into a styled page, and invalid JSON is
   recovered or re-encoded. The chat reply discloses when auto-repair ran.

## Chat routing

- Requests that name a file type but no filename ("html 파일 만들어줘",
  "웹페이지 만들어줘") now infer a target (`generated_page.html`, `styles.css`,
  `data.json`, …) and use the deterministic direct-write path instead of the
  agent loop. Inference is deliberately narrow — it requires a creation verb
  plus an explicit type keyword, so report/document prose requests keep
  flowing to the Knowledge-Graph document generator.
- Explicit type keywords (html, 웹페이지, webpage) count as file words for the
  chat file-action gate, so "html 페이지 만들어줘" creates a real file instead
  of a code block in chat.
- File-generation model calls clamp temperature (≤0.3) and raise the token
  budget (≥4096) so documents complete.
- The direct-write response payload includes `generation` metadata: attempt
  count, per-attempt validation reasons, and whether deterministic repair ran.

## Agent loop hardening

- `extract_action` strips `<think>` blocks before locating the action object
  and tolerates trailing commas in the JSON.
- A malformed action reply no longer aborts the run: up to two corrective
  format reminders are fed back through the corrections channel first.
- The executor prompt pins `write_file` content rules: complete raw file
  content, no fences, extension-valid documents.

## Tests

- New `tests/unit/test_file_generation.py` (22 tests) reproduces the
  small-model failure modes — fenced replies with commentary, `<think>`
  blocks, multiple fences, unfenced chat framing, truncated HTML, refusals,
  invalid JSON, backend errors — and asserts the pipeline yields a valid file
  in every case.
- Full unit suite: 1062 tests passing.

## Compatibility

- No API surface removed. `/chat` file-action responses gain an additive
  `generation` field. Existing explicit-filename and inline-content paths
  behave as before, now with validation and repair behind them.
