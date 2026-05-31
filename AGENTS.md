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
6. Documentation synchronization
7. UI and feature enhancements

---

## User Working Style

The user prefers autonomous execution.

Default behavior:

- Make reasonable decisions without asking
- Continue through implementation, testing, build, documentation sync, review, commit, and push
- Avoid presenting multiple options when a safe default exists
- Avoid asking for permission for routine engineering work
- Prefer action over discussion
- Prefer implementation over planning
- Prefer completing the task over reporting progress
- Provide final report only

Only interrupt for:

- Secrets
- Credentials
- Package publishing
- Production deployment
- Force push
- Destructive actions
- Irreversible operations
- No safe default exists

---

## Execution Workflow

Default workflow:

1. Analyze
2. Design
3. Implement
4. Test
5. Build
6. Documentation Sync
7. Review
8. Commit
9. Push
10. Final report only

A task is not complete until implementation, tests, build validation, documentation sync when relevant, commit, and push are done.

---

## Architecture Rules

Prefer:

- Dependency injection
- Explicit interfaces
- Small focused modules
- Runtime context objects
- Registry-based dispatch
- Configuration objects
- Composition over global state
- Testable boundaries

Avoid:

- Global mutable state
- Large monolithic files
- Hidden side effects
- Duplicate business logic
- Circular dependencies
- Tight coupling

---

## Refactoring Rules

When refactoring:

- Prefer moving code over rewriting code
- Preserve behavior unless explicitly changing behavior
- Preserve public APIs whenever practical
- Remove dead code when safe
- Remove obsolete compatibility layers when no longer needed
- Leave the codebase cleaner than it was found

---

## Knowledge Graph Rules

Knowledge Graph changes must:

- Preserve legacy compatibility
- Avoid destructive migrations
- Prefer reprojection over mutation
- Keep rollback paths available
- Maintain equivalence tests
- Preserve read compatibility
- Preserve migration safety
- Preserve dual-write guarantees

---

## Documentation Sync Before Commit

Before every non-trivial git commit and push, perform a documentation sync.

This is mandatory for:

- Version bumps
- Release work
- Architecture changes
- API changes
- CLI changes
- UI changes
- VS Code extension changes
- Security / permission / workspace changes
- Packaging / CI / release workflow changes

### Always Check

- README.md
- RELEASE.md
- docs/CHANGELOG.md

### Check When Relevant

- AGENTS.md
- SECURITY.md
- docs/ENTERPRISE.md
- docs/EDITION_STRATEGY.md
- vscode-extension/README.md
- docs/*.md

### Version Reference Rules

Classify version references as:

1. Current release reference
2. Historical changelog / release history
3. Example placeholder

Rules:

- Current release references must match the target version.
- Historical changelog entries must not be rewritten just because they mention old versions.
- Example commands should prefer `X.Y.Z` placeholders instead of hardcoded old versions.
- Do not leave stale `Current release`, `Latest`, or `New in` sections pointing to older versions.
- Do not leave unsafe publish commands using `dist/*`.
- VSIX publish commands must use `dist/ltcai-X.Y.Z.vsix`.

### Required Documentation Checks

Run these checks before commit when documentation or release/version behavior may be affected:

```bash
grep -R "Current release" -n README.md RELEASE.md docs/*.md SECURITY.md vscode-extension/README.md 2>/dev/null || true
grep -R "Latest" -n README.md RELEASE.md docs/*.md SECURITY.md vscode-extension/README.md 2>/dev/null || true
grep -R "New in 1\." -n README.md RELEASE.md docs/*.md vscode-extension/README.md 2>/dev/null || true
grep -R "dist/\*" -n README.md RELEASE.md docs/*.md SECURITY.md vscode-extension/README.md 2>/dev/null || true
grep -R "ovsx publish ltcai-" -n README.md RELEASE.md docs/*.md SECURITY.md vscode-extension/README.md 2>/dev/null || true
grep -R "ltcai-[0-9]\+\.[0-9]\+\.[0-9]\+\.vsix" -n README.md RELEASE.md docs/*.md vscode-extension/README.md 2>/dev/null || true
```

If the release target is known, also check for stale current references to prior versions.

### Documentation Update Policy

Update documentation in the same commit when the code change affects it.

Do not create a separate docs cleanup commit unless explicitly requested.

For release commits:

- README.md must show the target version as current/latest.
- RELEASE.md must describe the target release.
- docs/CHANGELOG.md must include the target release entry.
- GitHub Release notes must align with RELEASE.md / CHANGELOG.md.
- Package publish instructions must use exact target-version artifact filenames.
- Never document `twine upload dist/*`.
- Never document generic `ovsx publish ltcai-X.Y.Z.vsix` without the `dist/` path.
- Prefer `dist/ltcai-X.Y.Z.vsix` for VS Code Marketplace and Open VSX examples.

---

## Testing Requirements

Before completion:

- Run affected tests
- Run unit tests
- Run validation checks
- Run build verification
- Fix failures before completion
- Add tests for important new behavior

---

## Build Requirements

When applicable:

- Run Python validation/build checks
- Run npm validation/build checks
- Run package builds
- Verify artifacts are generated successfully
- Fix build failures before reporting completion

---

## Release Requirements

For release work:

- Bump all required version files consistently
- Generate exact-version artifacts only
- Validate release artifacts before reporting completion
- Use exact artifact paths
- Do not use `dist/*` for upload or release commands
- Do not publish packages unless explicitly requested
- Do not deploy unless explicitly requested

Expected artifact naming:

- `dist/ltcai-X.Y.Z-py3-none-any.whl`
- `dist/ltcai-X.Y.Z.tar.gz`
- `dist/ltcai-X.Y.Z.vsix`
- `ltcai-X.Y.Z.tgz`

Manual package publish commands must use exact target-version filenames.

---

## Git Requirements

When work is complete:

- `git add`
- `git commit`
- `git push`

Use meaningful commit messages.

Do not leave completed work uncommitted.

Do not leave committed work unpushed.

Do not create tags unless explicitly requested.

---

## Forbidden

Do not:

- Publish packages unless explicitly requested
- Deploy services
- Upload release artifacts unless explicitly requested
- Force push
- Remove rollback paths without justification
- Skip tests after major refactors
- Skip builds after major refactors
- Leave stale current-release documentation after release/version work
- Document unsafe `dist/*` publish commands

---

## Final Report

Provide only the final report.

Include:

1. Summary of work completed
2. Architecture changes
3. Files changed
4. Tests executed
5. Build results
6. Documentation sync result
7. Markdown files checked
8. Markdown files updated
9. Stale current-version references fixed
10. Historical version references intentionally preserved
11. Commit hash
12. Push result
13. Remaining technical debt
14. Risks or follow-up items
15. Recommended next refactor