# Lattice AI Onboarding

Current release: **8.6.0 — Desktop Capture & Navigation Reliability**.

The first-run goal is a five-minute path from "I opened the app" to "my Brain
has a source, a question, and proof." This page is the product contract behind
that flow; the UI should make these steps obvious without asking a new user to
read the docs first.

## Five-Minute Flow

1. Wake the Brain and confirm the local owner profile.
2. Let Lattice inspect local model/runtime readiness.
3. Pick the recommended Brain voice or skip model loading.
4. Add one source: upload a file, choose and scan a folder, save a note, or
   capture a browser/source event.
5. Ask one grounded question and inspect the answer proof.
6. Open the Knowledge Graph only when the user wants source-level evidence.
7. Back up the Brain once useful memory exists.

## Product Promises

- The user starts in the Brain, not in an admin dashboard.
- Empty states suggest one concrete next action without claiming proof that does
  not exist yet.
- Upload, note, browser, and message ingestion all converge through the unified
  ingestion pipeline when it is available.
- Workspace-scoped content must not leak or overwrite another workspace's graph
  metadata.
- Advanced runtime, workflow, plugin, and admin surfaces are available, but they
  do not block the first conversation.

## Release Gate

8.6.0 treats onboarding as a release gate, not marketing copy. The current
machine-checkable product readiness report requires this five-minute contract,
the Brain Home surface, setup helpers, graph ingestion tests, and exact release
artifact documentation before the release can be called complete.
