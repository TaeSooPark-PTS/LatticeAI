# Lattice AI v8.3.0 Release Notes

## Summary

8.3.0 is the Orchestrated Brain Readiness release. It reduces hidden
compatibility debt, makes AgentRuntime and workflow boundaries more inspectable,
routes graph ingest through the unified ingestion pipeline, strengthens
workspace-safe graph behavior, and documents the onboarding and plugin/community
path.

## Highlights

- Managed legacy shim inventory with owners, replacements, reasons, and removal
  phases.
- AgentRuntime lifecycle cleanup and compatibility for legacy run event reads.
- WorkflowEngine boundary/config inspection and centralized legacy step
  projection.
- `/knowledge-graph/ingest` support for `IngestionPipeline` provenance and hook
  lifecycle.
- Workspace-isolated text/web/note graph identities for duplicate content.
- Upload client failure handling and upload-to-KG pipeline coverage.
- 8.3.0 onboarding, community/plugin, release, changelog, and security docs.

## Upgrade Note

The workspace-scoped graph identity fix is intentionally additive. Existing
legacy-global graph nodes are not rewritten in place; if the same text/web/note
content is re-ingested with a workspace id, Lattice AI may create a separate
workspace-scoped node. Re-index existing sources after upgrading when you want a
graph to converge on workspace-scoped provenance.

## Artifacts

Expected local release artifacts use exact 8.3.0 filenames:

- `dist/ltcai-8.3.0-py3-none-any.whl`
- `dist/ltcai-8.3.0.tar.gz`
- `dist/ltcai-8.3.0.vsix`
- `ltcai-8.3.0.tgz`
- `src-tauri/target/release/bundle/dmg/Lattice AI_8.3.0_aarch64.dmg`

Package registry publishing remains an owner-run manual step.
