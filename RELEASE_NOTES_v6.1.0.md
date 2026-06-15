# Lattice AI v6.1.0 Release Notes

Product Hardening / Digital Brain Completion.

v6.1.0 tightens the v6 Digital Brain experience and release readiness without
adding a broad new surface. The release keeps Lattice AI focused on a
local-first Brain that survives model changes, while hardening first-run,
review, packaging, and runtime seams.

## Highlights

- First-run can create or open the local Brain even when model setup is
  deferred.
- Brain Home now explains the first memory loop: save useful context, see it
  return in Brain state, then protect it with backup/export.
- Review Center cards clarify that Run now previews or regenerates work without
  approving the review item.
- `lattice_brain` has an AST import guard proving it does not import
  `latticeai` or `ltcai`.
- Model download consent has unit coverage so token presence alone does not
  start an external download path.
- Pure CLI runtime helpers live in `latticeai.cli.runtime` while the root
  `ltcai_cli.py` entrypoint remains compatible.
- `latticeai.cli` is included in the Python wheel package list.
- Tool dispatch authorization now has an injectable service boundary around the
  shared ToolRegistry.
- Production chat AgentRuntime construction moved into app-factory assembly and
  is injected through `AppContext`.
- Release evidence is refreshed under `output/release/v6.1.0/`.

## Validation

- PR #5 checks passing at final release-prep verification.
- `npm run check:python` passed.
- `npm run build:python` built the exact 6.1.0 Python artifacts locally.
- Focused runtime, import-guard, CLI runtime, ToolRegistry, app-factory, and
  route-order tests passed during hardening.
- `npm run docs:check-links` passed.

## Expected Artifacts

- `dist/ltcai-6.1.0-py3-none-any.whl`
- `dist/ltcai-6.1.0.tar.gz`
- `dist/ltcai-6.1.0.vsix`
- `ltcai-6.1.0.tgz`
- `src-tauri/target/release/bundle/dmg/Lattice AI_6.1.0_aarch64.dmg`

Package publishing, deployment, tag creation, and merge to `main` remain
owner-run only.
