# Project Principles — the Digital Brain constitution

Lattice AI is a local-first **Digital Brain Platform**. Models are temporary;
knowledge is durable. The user owns their knowledge, their memory, their
context, and their experience — on their machine, exportable at any moment,
explainable down to where every fact came from.

## Sovereignty

- Local-first by default; privacy-first by default.
- The user's brain (graph, memories, conversations, notes, provenance) lives
  in local storage they control and can back up, export, and carry away.
- Nothing leaves the machine implicitly: no CDN calls from shipped pages, no
  cloud rendezvous, no telemetry. Knowledge exchange between devices is
  explicit, paired, and signed.
- Models are replaceable implementations. So are agents, retrieval, and the
  UI. The brain is the asset.

## One brain, no silos

- Every data source enters through one ingestion pipeline and lands in the
  Knowledge Graph with provenance. No source bypasses it; none becomes a
  parallel store.
- Memory is part of the brain: episodic (conversations), semantic
  (preferences, decisions), and experience records share the substrate and
  the export.

## Honesty

- **Never fake functionality.** A capability that cannot complete surfaces an
  explicit state (`simulation`, `awaiting_approval`, `unavailable`,
  `skipped` with a reason) — never a fabricated success, score, or record.
- Simulated runs are labeled in their persisted records and never enter the
  brain as experience.
- FEATURE_STATUS.md is the public honesty ledger: claims trace to code, and
  gaps are written down instead of papered over.

## User Agency

- Explain clearly before asking the user to choose.
- Do not hide capability in the name of protection.
- Do not hide source, risk, or limitation details.
- Make the safe path clear, but leave the final decision to the user.
- Consent is explicit: model downloads, folder indexing, computer-use memory,
  and tool approvals are user decisions, not defaults.

## Mode Policy

- Basic mode and advanced mode have the same features.
- Basic mode uses plain language.
- Advanced mode shows deeper execution details.
- Admin mode is the only mode with extra authority.

## Engineering Policy

- Prefer explicit interfaces and dependency injection.
- Keep model catalogs and model workflow policy centralized.
- Avoid hidden global state and silent fallbacks.
- Preserve graph data and migration safety: migrations are backup-first,
  idempotent, and re-entrant; user data directories are never deleted.
- Add tests for product-policy behavior, not just implementation details.
