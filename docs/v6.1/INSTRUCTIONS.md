You are the release lead for Lattice AI v6.1.0.

Goal:
Complete v6.1.0 as a Product Hardening & Digital Brain Completion release.

Do not add broad new features. The priority is to make Lattice AI feel like one coherent local-first Digital Brain product.

Fixed product identity:
Lattice AI is a local-first Digital Brain that keeps your knowledge durable across any AI model.
Models are replaceable. The Brain is durable.

Scope:
1. Align README, ARCHITECTURE, FEATURE_STATUS, RELEASE_NOTES, CHANGELOG, DEVELOPMENT, LEGACY_COMPATIBILITY, SECURITY, PRIVACY, package metadata, pyproject metadata, VS Code extension README around the Digital Brain identity.
2. Make the first-run flow clear: create/open local Brain profile -> understand local-first trust boundary -> start Brain Chat -> save first memory -> see memory in Brain Home -> recall it later -> backup/export.
3. Redesign Brain Home as the user's Brain state, not a dashboard, model launcher, graph database, or admin console.
4. Polish Review Center: Pending/Snoozed/All, Unsnooze, Approve/Dismiss/Run now/Snooze semantics, readable provenance, and clear placement in the IA.
5. Decompose app_factory.py further so it only orchestrates config/runtime/app/router assembly. Move responsibilities into focused runtime modules. Preserve import-time no-side-effect behavior and the frozen route snapshot.
6. Reduce legacy root modules into compatibility shims where possible. New code must import package modules directly, not root legacy modules.
7. Strengthen lattice_brain as the independent Brain Core. It must not import latticeai. FastAPI/CLI/tests should depend on BrainCore boundaries where appropriate.
8. Verify local-first trust gates: token presence alone must not start external communication; cloud calls, downloads, Telegram, Brain Network, Docker/Postgres, update checks, and local file reads require explicit consent.
9. Simplify model UX. Basic users see a small recommendation set; Advanced users can inspect registry details. Brain creation and memory should work honestly without a loaded model.
10. Improve UI/UX for first-run, Brain Home, Brain Chat, memory/topic/relationship views, Review Center, backup/export/restore, Settings, and Admin. Fix clipping, empty states, loading/error states, and light/dark responsiveness.
11. Regenerate OpenAPI types where API contracts change.
12. Update screenshots/GIFs and documentation paths to v6.1.0.
13. Bump and synchronize all version metadata to 6.1.0.
14. Run the full validation set before final report.

Validation required:
npm run check:python
node scripts/run_python.mjs -m ruff check .
npm run lint
npm run typecheck
npm run test:unit
npm run test:integration
npm run test:visual
npm run desktop:tauri:check
npm run docs:check-links
npm run build:assets
npm run build:python
npm pack
npm run package:vsix
npm run release:validate

Final report must include:
- Branch name
- Commit hash
- Files changed summary
- Product changes
- UX changes
- Backend/runtime changes
- BrainCore changes
- Legacy cleanup
- Trust/security validation
- Tests run and exact results
- Known remaining gaps
- Whether anything was not built, not pushed, not tagged, or not released

Do not merge to main unless explicitly instructed.
Do not tag.
Do not publish npm/PyPI/VSIX/OpenVSX.
Do not claim 100/100 quality.
