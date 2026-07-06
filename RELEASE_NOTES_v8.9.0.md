# Lattice AI 8.9.0

Scoped Memory & Tool Policy Hardening.

## Highlights

- Authenticated chat history and Knowledge Graph reads now honor user/workspace
  scope across list, search, traversal, relationship, node, and delete paths.
- Direct HTTP/MCP Tool API calls now run through ToolRegistry policy gates
  before hooks or handlers execute.
- AgentRuntime requires explicit human approval for non-auto-approved plans.
- Local permission approvals hash tokens at rest and block write approvals for
  protected system prefixes.
- Installer/process execution paths now expose redacted command plans, require
  confirmation tokens, and write local process audit events.
- Frontend API base handling, CSS tokens/base rules, workspace clearing, and
  i18n literal checks now have smaller maintainability seams.

## Exact Artifacts

- `dist/ltcai-8.9.0-py3-none-any.whl`
- `dist/ltcai-8.9.0.tar.gz`
- `dist/ltcai-8.9.0.vsix`
- `ltcai-8.9.0.tgz`
- `src-tauri/target/release/bundle/dmg/Lattice AI_8.9.0_aarch64.dmg`

Package registry publishing remains owner-run.
