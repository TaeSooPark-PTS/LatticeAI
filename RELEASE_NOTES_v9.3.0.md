# Lattice AI v9.3.0 — Proactive Brain Intelligence

Released: 2026-07-20

9.3.0 turns the Brain from a passive knowledge store into an **active steward
of its own knowledge**. Until now the Brain accepted what you gave it and
answered what you asked; it never looked at itself. This release wires the
previously dormant quality layer (`lattice_brain.quality` — dedupe, merge,
conflict and temporal-contradiction detection, edge quality) into the product
and upgrades the core recall path with semantic evidence.

## Brain Intelligence (`/api/brain/*`)

- **Health diagnosis** — `GET /api/brain/health` scores the Brain across four
  dimensions: freshness (how much knowledge went stale), connectivity (how
  much knowledge is disconnected from everything else), search readiness
  (vector-index coverage), and consistency (duplicate/contradiction
  pressure). It returns an overall grade plus concrete recommended care
  actions. Every number is read from the live stores — a missing store
  degrades that dimension to `unavailable`, it never guesses.
- **Proactive insights** — `GET /api/brain/insights` digests recent knowledge
  growth, trending types, stale knowledge, orphan nodes, and suggested
  questions grounded in real node titles.
- **Contradiction surfacing** — `GET /api/brain/contradictions` detects
  preference/negation conflicts and temporal contradictions across workspace
  memories and reports explicit CONTRADICTS edges from the graph, each with
  evidence snippets.
- **Consent-first consolidation** — `POST /api/brain/consolidate` detects
  duplicate memories and duplicate edges. It is a dry run by default;
  `apply=true` prunes only exact duplicate workspace memories through the
  audited MemoryService path. Graph content is never mutated.

## Hybrid recall

`POST /api/memory/recall` now blends vector similarity into the lexical
ranking behind a `hybrid-evidence/v2` quality gate:

- Semantic hits surface knowledge phrased differently from the query — the
  main blind spot of lexical recall (e.g. a Korean note found by an English
  query).
- Vector matches are workspace-scoped through `filter_scoped_nodes` **before**
  they can influence results — the global vector index cannot leak across
  workspace boundaries.
- Each result reports `evidence_kinds` (lexical/semantic) so confidence stays
  explainable.
- Any vector-tier failure degrades recall honestly back to
  `lexical-evidence/v1` with the error surfaced, never a broken recall.

## Brain surface

A new **"Brain intelligence check"** panel sits beside Brain care: overall
score and plain-language grade, per-dimension scores, activity/attention
chips (recent, stale, disconnected, conflicting), recommended care actions,
and duplicate-cleanup preview/apply. Fully ko/en localized.

## Verification

- New `tests/unit/test_brain_intelligence.py` (14 tests): health scoring,
  workspace-scoped graph reads, insights, contradiction pairs, consent-first
  consolidation, and hybrid recall blend/merge/scoping/degradation.
- Full sweep on this release: **1076 unit**, **13 integration**, **14
  frontend vitest**, and **18 playwright visual** tests passing; lint,
  typecheck, brain-quality-eval, and product-readiness gates green; all four
  new endpoints exercised against a live-boot app.

## Compatibility

- Purely additive API surface (`/api/brain/health|insights|contradictions|
  consolidate`, plus additive `vector_score`/`evidence_kinds` fields and the
  `hybrid-evidence/v2` gate label in recall responses). No endpoints removed
  or changed shape otherwise.
