# Lattice AI v6.0.0 Architecture Review

## Current Direction

The v6.0.0 branch keeps Lattice AI centered on a local-first Digital Brain:
knowledge and provenance are durable, while models and automation runs are
replaceable execution layers.

## Review Center Boundary

Implemented boundaries:

- `latticeai/services/review_queue.py` owns Review Center policy:
  transitions, read-time `effective_status`, `run_now` back-linking, and
  unsnooze semantics.
- `latticeai/api/review_queue.py` is a thin FastAPI router with explicit
  response models.
- `WorkspaceOSStore` owns workspace-scoped persistence.
- `TriggerService` and `RunExecutor` enqueue review items only through the
  opt-in `review_queue: true` path.

This keeps Review Center behavior additive and avoids changing legacy workflow
execution unless a workflow opts in.

## App Factory

Status:

- `app_factory.py` still performs broad assembly work.
- The Review Queue wiring is explicit but contributes to factory size.
- A full decomposition into runtime modules is not complete in this branch
  checkpoint.

Recommended next steps:

- Extract review/router assembly into a small runtime composition module.
- Continue the existing runtime split pattern rather than introducing a new
  global registry.
- Preserve lazy imports and current app bootstrap semantics.

## Brain Core Boundary

Expected rule:

- `lattice_brain` must not import `latticeai`.

Verification command:

```bash
rg "from latticeai|import latticeai" lattice_brain
```

Current finding:

- AST import scan and `tests/unit/test_lattice_brain_isolation.py` pass.
- `rg` reports `latticeai` strings in `lattice_brain/runtime/__init__.py`, but
  those are architecture-map docstring references, not executable imports.

## Compatibility Risks

- Review queue persistence adds state but does not require destructive
  migration.
- `unsnooze` only mutates explicitly through an API action.
- Snooze expiry remains read-time only, so no scheduler mutation or hidden
  migration is introduced.

## Architecture Score Evidence

Positive evidence:

- Review Center policy is testable independently from the API.
- Frontend Review Center is feature-owned instead of embedded inside `Act.tsx`.
- OpenAPI-generated schemas now drive ReviewItem frontend typing.

Remaining gaps:

- `app_factory.py` decomposition remains incomplete.
- Strict generated client methods still sit behind the local `apiJson` wrapper.
