# Lattice AI v7.9.0

## Agent Runtime Boundary Hardening

7.9.0 sharpens the AgentRuntime architecture without changing user-facing
execution semantics. The product runtime facade remains
`lattice_brain.runtime.agent_runtime.AgentRuntime`, while the older single-agent
PLAN / EXECUTE / VERIFY loop now has the explicit `SingleAgentRuntime` name.

### Changed

- Added `SingleAgentRuntime` for the single-agent state machine.
- Preserved `latticeai.core.agent.AgentRuntime` as a compatibility alias.
- Updated tool-dispatch wiring to construct `SingleAgentRuntime` directly.
- Moved git rollback for single-agent edits behind an injected `rollback_file`
  port owned by `ToolDispatchService`.
- Added a shared `runtime-boundary/v1` descriptor for both runtime surfaces.
- Added `RuntimeBoundaryProtocol` for common runtime inspection without forcing
  product and single-agent execution methods to match.
- Updated product and architecture readiness gates to 7.9.0.

### Expected artifacts

- `dist/ltcai-7.9.0-py3-none-any.whl`
- `dist/ltcai-7.9.0.tar.gz`
- `dist/ltcai-7.9.0.vsix`
- `ltcai-7.9.0.tgz`
- `src-tauri/target/release/bundle/dmg/Lattice AI_7.9.0_aarch64.dmg`
