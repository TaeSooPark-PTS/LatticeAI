# LatticeAI Project AGENTS.md

## Project Mission

LatticeAI is a local-first AI workspace platform.

Primary goals:

- Local LLM first
- Knowledge Graph driven workflows
- Agent Runtime architecture
- Tool Registry architecture
- Personal Workspace
- Organization Workspace
- Security-first design
- Maintainable modular architecture

---

## Preferred Refactoring Order

When architectural debt exists, prioritize:

1. AgentRuntime extraction
2. ToolRegistry separation
3. Config centralization
4. Server decomposition
5. Knowledge Graph stabilization
6. UI and feature enhancements

---

## Execution Style

Default workflow:

Analyze
→ Design
→ Implement
→ Test
→ Build
→ Review
→ Commit
→ Push
→ Final report

Provide final report only.

Avoid progress updates.

Avoid approval loops.

Choose reasonable defaults and continue.

---

## Autonomous Execution

Safe changes should be completed without asking.

Examples:

- Refactoring
- Dead code cleanup
- Test additions
- Build validation
- Internal architecture changes
- Dependency injection improvements
- Runtime extraction
- Tool registry improvements
- Config consolidation

Continue until the task is complete.

---

## Stop Conditions

Only stop when:

- Secrets are required
- Credentials are required
- Production deployment is requested
- Package publishing is requested
- Force push is required
- Destructive data deletion is required
- Irreversible operations are required
- No safe default exists

---

## Architecture Rules

Prefer:

- Dependency injection
- Explicit interfaces
- Small modules
- Runtime context objects
- Registry based dispatch
- Configuration objects

Avoid:

- Global mutable state
- Large switch statements
- Massive server files
- Hidden side effects
- Duplicate business logic

---

## Knowledge Graph Rules

Knowledge Graph changes must:

- Preserve legacy compatibility
- Avoid destructive migrations
- Prefer reprojection over mutation
- Keep rollback paths available
- Maintain equivalence tests
- Preserve read compatibility

---

## Testing Requirements

Before completion:

- Run affected tests
- Run unit tests
- Run validation checks
- Run build verification

Fix failures before completion.

---

## Git Requirements

When work is complete:

- git add
- git commit
- git push

Use meaningful commit messages.

Do not create tags unless explicitly requested.

---

## Forbidden

Do not:

- Publish packages
- Deploy services
- Upload release artifacts
- Force push
- Remove rollback paths without justification
- Skip tests after major refactors

---

## Final Report

Provide only the final report.

Include:

1. Architecture changes
2. Files changed
3. Tests executed
4. Build results
5. Commit hash
6. Push result
7. Remaining technical debt
8. Recommended next refactor
