# Lattice AI Onboarding

Current release: **12.0.0 — Open House**.

The first-run goal is a five-minute path from "I opened the app" to "my Brain
has a source, a question, and proof." This page is the product contract behind
that flow; the UI should make these steps obvious without asking a new user to
read the docs first.

## Five-Minute Flow

1. Wake the Brain and confirm the local owner profile.
2. Let Lattice inspect local model/runtime readiness (`/setup/scan` is a
   real probe). Since 12.0.0 a listed brew/pip/uv item can be installed
   from here, one item at a time and only when the user says so — the
   default is still to show the command rather than run it.
3. Pick the recommended Brain voice (`/models/recommendations` probes RAM
   and the worker catalog) or skip model loading.
4. Add one source: upload a file, choose and scan a folder, save a note, or
   capture a browser/source event.
5. Ask one grounded question and inspect the answer proof.
6. Open the Knowledge Graph only when the user wants source-level evidence.
7. Back up the Brain once useful memory exists.

## Product Promises

- The user starts in the Brain, not in an admin dashboard.
- The first screen asks what the user wants to do, then prioritizes one composer,
  a few starters, and recent conversations over scores or system status.
- Chat, Sources, Memory, and Work stay one visible navigation action away on
  desktop and mobile; models and administration do not compete with them.
- Memory starts with search. The connection map appears only when the user asks
  to inspect relationships.
- Empty states suggest one concrete next action without claiming proof that does
  not exist yet.
- Core-service failures show an unavailable/error state and recovery guidance;
  they are never presented as an empty or healthy Brain. Since 12.0.0 every
  route and every heavy panel sits inside an error boundary with a 다시 시도
  action, so one failing panel costs its own card rather than the screen.
- The first source can be added on any model the machine can hold: the agent
  profile is measured on load, and a model that cannot emit a tool call is
  guided through numbered choices instead of failing the run.
- Re-scanning a folder that has not changed is nearly free, so re-indexing is
  a safe habit rather than a cost. A file deleted from disk keeps its memory
  until the user confirms the 「삭제된 파일 정리」 action.
- Upload, note, browser, and message ingestion all converge through the unified
  ingestion pipeline when it is available.
- Workspace-scoped content must not leak or overwrite another workspace's graph
  metadata.
- Advanced runtime, workflow, plugin, and admin surfaces are available, but they
  do not block the first conversation.

## Release Gate

9.6.0 treats onboarding as a release gate, not marketing copy. The current
machine-checkable product readiness report requires this five-minute contract,
the Brain Home surface, setup helpers, graph ingestion tests, and exact release
artifact documentation before the release can be called complete.
