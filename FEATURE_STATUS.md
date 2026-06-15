# Lattice AI — Feature Status (v6.0.0 target)

**Current release-preparation line:** v6.0.0 Product Reset / Review Center Completion.
Lattice AI is a local-first Digital Brain that keeps your knowledge durable
across any AI model. The primary experience is Brain plus conversation; the
graph remains durable infrastructure and advanced exploration, not the home
product identity. Sections below v5.3.0 are historical release-status records
and should not override the current README, ARCHITECTURE.md, SECURITY.md,
PRIVACY.md, or v6.0.0 release notes.

## v6.0.0 Product Reset / Review Center Completion — current development line

v6.0.0 completes the first Review Center recovery loop and starts the broader
product-quality reset branch. The goal is not to claim 100/100, but to make the
automation review workflow more reversible, typed, and maintainable.

| Area | Status | Evidence |
| --- | --- | --- |
| **Snoozed review visibility** | WORKING | Review Center status filters include Pending, Snoozed, and All. |
| **Unsnooze lifecycle** | WORKING | `ReviewQueueService.unsnooze` and `/automation/reviews/{item_id}/unsnooze` return stored snoozed items to pending and clear `snoozed_until`. |
| **Strict review typing** | WORKING | `frontend/src/api/client.ts` aliases generated OpenAPI `ReviewItem` and `ReviewItemList` schemas and uses generated operation paths for Review Center list/actions. |
| **Frontend ownership split** | WORKING | Review UI lives in `frontend/src/features/review/` instead of inside `Act.tsx`. |
| **v6 quality evidence** | IN REVIEW | `docs/v6/QUALITY_SCORECARD.md` records baseline, target, actual estimate, evidence, and remaining gaps without claiming 100/100. |

## v5.6.0 Brain Automation Review Center — current development line

v5.6.0 adds the user-facing review layer for Brain automation. Automation output
is stored as workspace-scoped review items with source and provenance metadata,
then shown in Act > Runs > Review for explicit user action.

| Area | Status | Evidence |
| --- | --- | --- |
| **Review queue backend** | WORKING | `latticeai/services/review_queue.py`, `latticeai/api/review_queue.py`, and `WorkspaceOSStore` review item persistence expose `/automation/reviews`. |
| **Automation opt-in enqueue** | WORKING | `TriggerService` and `RunExecutor` enqueue review items only for workflows with `review_queue: true`, preserving legacy behavior by default. |
| **Review inbox UI** | WORKING | `frontend/src/pages/Act.tsx` adds Runs/Review tabs, source filters, review cards, provenance details, and guarded actions. |
| **OpenAPI contract** | WORKING | `frontend/openapi.json` and `frontend/src/api/openapi.ts` include the review API contract. |
| **Snooze semantics** | WORKING | Snoozed items are hidden until expiry and surface through read-time `effective_status` without scheduler mutation. |

## v5.5.0 Release Coordination — what changed

v5.5.0 completes the release coordination pass for the current product line:
version metadata, lockfiles, static manifest, release documentation, and exact
artifact names are synchronized while preserving the v5.4.0 Brain Automation
Scheduler behavior.

| Area | Status | Evidence |
| --- | --- | --- |
| **Version synchronization** | WORKING | `scripts/bump_version.py` updates Python, npm, VSIX, Tauri, runtime constants, lockfiles, and static asset metadata to 5.5.0. |
| **Release documentation** | WORKING | README, RELEASE.md, docs/CHANGELOG.md, RELEASE_NOTES.md, and vscode-extension/README.md point current release-preparation references at 5.5.0. |
| **Behavior preservation** | WORKING | v5.4.0 consent-first automation, TriggerService safeguards, and runtime graph cleanup remain the functional baseline. |

## v5.4.0 Brain Automation Scheduler — what changed

The next product direction is a gentle Brain automation layer: Lattice suggests
useful schedules and follow-up workflows, but installs them as reviewable,
disabled drafts until the user explicitly enables a trigger.

| Area | Status | Evidence |
| --- | --- | --- |
| **Brain automation recipes** | IN DEVELOPMENT | `latticeai/services/brain_automation.py` defines Daily Memory Digest, Weekly Project Review, and Follow-up Radar recipes with local-only consent metadata. |
| **Consent-first scheduler boundary** | WORKING | `TriggerService` ignores trigger nodes with explicit `enabled: false`, so recipe drafts do not run silently. |
| **Automate page recipe cards** | WORKING | `frontend/src/pages/Act.tsx` shows recipe cards; "Create reviewable draft" now guards against duplicate recipe drafts (via metadata.recipe_id), prevents double-clicks during install, and shows immediate success feedback + updated button state. |

## v5.3.0 Product Clarity and Runtime Cleanup — what changed

v5.3.0 focuses on making the product easier to understand before adding more
features. It clarifies the public identity, reorganizes README/docs around the
user journey, improves onboarding/model setup copy, documents legacy shims, and
moves app factory config/security/Brain runtime seams into dedicated modules.

| Area | Status | Evidence |
| --- | --- | --- |
| **Product identity** | WORKING | README, package metadata, pyproject, architecture, feature status, release docs, and extension docs use the local-first Digital Brain definition. |
| **User-first README** | WORKING | README starts with what Lattice is, why users need it, what they can do, one-minute flow, screenshots, then install/architecture/release details. |
| **Onboarding copy** | WORKING | First-run copy frames login, environment analysis, recommendation, install/load, and Brain Chat around ownership, local storage, explicit consent, and durable memory. |
| **Model UX simplification** | WORKING | Basic model setup starts with a short recommendation set while Advanced keeps registry, hardware, license, safety, and verification detail visible. |
| **Runtime seam extraction** | WORKING | `latticeai.runtime.config_runtime`, `security_runtime`, and `brain_runtime` hold the first app factory extraction seams while preserving lazy import behavior. |
| **Legacy compatibility map** | WORKING | `docs/LEGACY_COMPATIBILITY.md` explains root compatibility modules, migration direction, and removal checklist. |
| **Development docs** | WORKING | `docs/DEVELOPMENT.md` separates contributor validation/runtime assembly guidance from README. |

## v5.2.0 User-Focused Model Transformation — what changed

v5.2.0 moves model choice from a loose recommendation list to a structured,
verified capability registry. The user-facing catalog stays focused on current
load-ready families, while registry-only candidates remain visible for
verification transparency until load readiness is confirmed.

| Area | Status | Evidence |
| --- | --- | --- |
| **Structured model capability registry** | WORKING | `latticeai/services/model_capability_registry.py` stores hf_repo_id, modality, quantization, hardware RAM notes, strategies, license, safety notes, and verification fields. |
| **HF verification transparency** | WORKING | `scripts/verify_hf_model_registry.py` writes `verification_report.json`; the 5.2.0 verification run confirmed 16/16 HF repos present, 15/16 config/tokenizer hints, Pixtral marked available-but-not-local-load-verified. |
| **User-facing catalog filtering** | WORKING | `latticeai/services/model_catalog.py` keeps raw registry entries for transparency and finalizes `ENGINE_MODEL_CATALOG` to current load-ready families to reduce catalog noise. |
| **Model recommendation metadata** | WORKING | `/models` and `/models/recommendations` expose verification, hardware, modality, load strategy, license, safety notes, and recommended_default. `latticeai/api/models.py`, `latticeai/services/model_recommendation.py`. |
| **Library model UI** | WORKING | `frontend/src/pages/Library.tsx` renders multimodal and HF badges, hardware notes, load strategies, and consent-first setup copy without breaking TypeScript strict checks. |
| **Marketplace template workspace scoping** | WORKING | Template install registry entries are keyed per workspace and `/marketplace/templates/registry` filters through the authorized workspace scope. `latticeai/core/workspace_os.py`, `latticeai/api/marketplace.py`, `tests/unit/test_agent_platform_maturity.py`. |
| **Package/runtime version sync** | WORKING | Historical 5.2.0 package/runtime/static metadata was synchronized for that release. |
| **Artifact exactness** | WORKING | Historical 5.2.0 validation expected exact wheel, sdist, npm tgz, VSIX, and Tauri DMG filenames and warned against `dist/*`. |

## v5.1.0 Product Trust & Clarity Release — what changed

v5.1.0 clarifies why Lattice AI exists and adds trust gates for CSP, local file
auto-read, model downloads, secret redaction, Brain Core independence, and
release documentation.

| Area | Status | Evidence |
| --- | --- | --- |
| **Private Brain positioning** | WORKING | README first screen and `docs/WHY_LATTICE.md` explain the local-first Digital Brain promise. |
| **Local Brain chat and memory views** | WORKING | Brain Chat, recent/older memories, topic views, relationship view, and graph exploration remain the normal user flow. `frontend/src/App.tsx`, `tests/visual/v3.spec.js`. |
| **Korean/English UI foundation** | WORKING | Language selection persists locally and drives first-run, Brain, graph, and key ownership copy. `frontend/src/i18n.ts`, `frontend/src/store/appStore.ts`. |
| **Brain ownership and portability** | WORKING | Export, backup, archive, inspect, verify, restore preview, and confirmed restore remain local-first and model-independent. `lattice_brain/portability.py`, `tests/unit/test_kg_portability.py`, `tests/unit/test_v42_brain_storage.py`. |
| **Tauri and app-shell CSP** | WORKING | Production Tauri CSP is non-null and `/app` responses include CSP headers. `src-tauri/tauri.conf.json`, `latticeai/api/static_routes.py`, `tests/unit/test_v51_trust_gates.py`. |
| **Secret redaction** | WORKING | Shared redaction covers logs, audit payloads, security exports, and builtin hook packets. `latticeai/core/security.py`, `latticeai/core/logging_safety.py`, `latticeai/core/audit.py`, `tests/unit/test_v51_trust_gates.py`. |
| **Auto local file read** | DISABLED BY DEFAULT / BLOCKED WITHOUT APPROVAL | `LATTICEAI_AUTO_READ_CHAT_PATHS` defaults false; even when true, chat does not silently read arbitrary local paths. `latticeai/api/chat.py`, `tests/unit/test_v51_trust_gates.py`. |
| **Model downloads** | OPT-IN | `/engines/pull-model` requires `allow_download=true`; prepare/load download paths already carry explicit consent. `latticeai/api/models.py`. |
| **Cloud models** | OPT-IN EXTERNAL | Cloud calls require configured keys and explicit model choice; no key/token alone starts a call. `latticeai/core/product_hardening.py`, `tests/unit/test_config.py`. |
| **Telegram / Brain Network / update checks** | OPT-IN EXTERNAL | Disabled by default; token presence alone is inert. `latticeai/core/product_hardening.py`. |
| **PostgreSQL / Docker scale mode** | OPT-IN LOCAL/NETWORK DEPENDENCY | SQLite is default; Postgres and Docker setup require explicit configuration/consent. `lattice_brain/storage/factory.py`, `lattice_brain/storage/docker.py`. |
| **Admin Console** | ADMIN-ONLY | Users, roles, audit logs, security events, retention, and index operations remain separated from the Brain user surface. `frontend/src/App.tsx`, `latticeai/api/admin.py`. |
| **Enterprise governance** | PREVIEW / PARTIAL | Community edition exposes descriptors and disabled capabilities honestly; Enterprise enforcement depends on provider implementation. `latticeai/core/enterprise.py`, `latticeai/core/enterprise_admin.py`. |
| **app_factory decomposition** | PARTIAL FOUNDATION | Config, security, Brain, session, hooks, web shell, persistence, lifespan, automation, context/search, platform services, app context, and router registration seams now exist with a frozen 364-entry route snapshot; lower-level orchestration cleanup remains technical debt. `latticeai/app_factory.py`, `latticeai/runtime/`, `tests/unit/test_app_factory.py`, `tests/unit/test_app_factory_early_assembly.py`, `tests/unit/test_runtime_seams.py`. |

## v5.0.0 Multilingual Brain Foundation Release — what changed

v5.0.0 keeps the Living Brain implementation and adds the language foundation
needed for Korean and English users. First-run onboarding, model setup, Brain
home, graph fallback copy, memory-save feedback, and Admin header labels now
react to a persisted language choice. Release metadata, docs, and artifacts are
synchronized to `5.0.0`.

| Feature | Status | Evidence |
| --- | --- | --- |
| **Korean/English language choice** | WORKING | Language selection persists in Zustand/localStorage and is visible on first-run, Brain, and Admin surfaces. `frontend/src/i18n.ts`, `frontend/src/store/appStore.ts`, `frontend/src/components/ProductFlow.tsx`, `frontend/src/App.tsx`. |
| **Bilingual onboarding** | WORKING | Login, analysis, recommendation, install/download/load, and model progress copy use localized strings. `frontend/src/components/ProductFlow.tsx`. |
| **Bilingual Brain exploration** | WORKING | Brain quick views, starter prompts, save feedback, overview cards, and graph fallback copy use localized strings. `frontend/src/App.tsx`. |
| **Publishable version bump** | WORKING | Python, npm, VSIX, Tauri, runtime, and static metadata are synchronized to `5.0.0`. `scripts/bump_version.py`, `tests/unit/test_version_consistency.py`. |
| **Collaboration-backed refactor order** | DOCUMENTED | pts_claudecode and pts_grok review notes define the next debt sequence: config, KG, ToolRegistry, AgentRuntime, app factory. `RELEASE_NOTES_v5.0.0.md`, `docs/CHANGELOG.md`. |
| **Artifact exactness** | WORKING | Release validation expects exact v5.0.0 wheel, sdist, npm tgz, VSIX, and Tauri DMG filenames. `scripts/validate_release_artifacts.py`. |

## v4.7.2 Intuitive Brain UX Release — what changed

v4.7.2 keeps the Living Brain implementation and adds the usability layer needed
for non-technical users: the first-run flow prevents accidental empty-Brain
creation, recommended model setup can be accepted in one click, and Brain home
has direct Memory, Topic, Relationship, and Graph views. Administrator workflows
remain separate under `#/admin`. Release metadata, screenshots/GIFs, docs, and
artifacts are synchronized to `4.7.2`.

| Feature | Status | Evidence |
| --- | --- | --- |
| **Safer first-run login** | WORKING | Saved-user email mismatch and wrong saved-password paths no longer auto-register a new empty Brain. `frontend/src/components/ProductFlow.tsx`, `tests/visual/v3.spec.js`. |
| **One-click model recommendation** | WORKING | Recommended model setup has a primary "추천대로 시작하기" path and clearer large-download messaging without fake ETA. `frontend/src/components/ProductFlow.tsx`. |
| **Direct Brain views** | WORKING | Memory, Topic, Relationship, and Graph are visible quick actions instead of relying on repeated Brain clicks. `frontend/src/App.tsx`, `frontend/src/styles.css`, `tests/visual/v3.spec.js`. |
| **Memory/topic overview** | WORKING | Brain Chat shows recent memories, older memories, major topics, and saved-to-memory status while keeping conversation primary. `frontend/src/App.tsx`. |
| **Admin Console separation** | WORKING | `#/admin` renders a separate admin surface while `/app` remains Brain + conversation. `frontend/src/App.tsx`, `tests/visual/v3.spec.js`. |
| **Admin logs and security events** | WORKING | Audit events, security events, users, policies, and Brain index operations are grouped in the Admin Console. `frontend/src/App.tsx`, `frontend/src/api/client.ts`, `latticeai/api/admin.py`, `latticeai/api/security_dashboard.py`. |
| **Operator filters and retention** | WORKING | Admin audit supports search/severity filters and `/admin/log-retention` reports retained events, prune candidates, and export-before-prune posture. `latticeai/api/admin.py`, `tests/unit/test_t6_identity_policy_invitations.py`. |
| **Admin runtime state separation** | WORKING | Admin Console API state is loaded through a dedicated hook instead of mixing with Brain chat state. `frontend/src/App.tsx`. |
| **Publishable version bump** | WORKING | Python, npm, VSIX, Tauri, runtime, and static metadata are synchronized to `4.7.2`. `scripts/bump_version.py`, `tests/unit/test_version_consistency.py`. |
| **Release evidence refresh** | WORKING | README links point to fresh v4.7.2 screenshots/GIF and a screenshot index. `output/release/v4.7.2/SCREENSHOT_INDEX.md`. |
| **Architecture/docs sync** | WORKING | README, ARCHITECTURE.md, release notes, changelog, security posture, and VS Code extension docs describe v4.7.2 current behavior while preserving v4.6.0 history. |
| **Artifact exactness** | WORKING | Release validation expects exact v4.7.2 wheel, sdist, npm tgz, VSIX, and Tauri DMG filenames. `scripts/validate_release_artifacts.py`. |

## v4.6.0 Living Brain Experience — what changed

v4.6.0 makes the Brain the product: first launch opens with Login only, setup
guides environment analysis and model recommendation, model loading opens the
living Brain conversation, and memories, knowledge, relationships, and the graph
are progressively disclosed.

| Feature | Status | Evidence |
| --- | --- | --- |
| **Login-first entry** | WORKING | `/app` opens to Login only until the local product flow completes. `frontend/src/components/ProductFlow.tsx`, `tests/visual/v3.spec.js`. |
| **Guided setup** | WORKING | Environment analysis, recommended models, and install/download/validate/load run as a full-screen consumer flow with runtime jargon hidden. `frontend/src/components/ProductFlow.tsx`, `frontend/src/api/client.ts`. |
| **Brain-first home** | WORKING | After model loading, `/app` and `/app#/brain` render the Brain conversation without a dashboard/onboarding panel above it. `frontend/src/pages/Brain.tsx`, `frontend/src/components/BrainConversation.tsx`. |
| **Living Brain presence** | WORKING | Animated Brain component reacts to listening, recall, thinking, planning, and activity states. `frontend/src/components/LivingBrain.tsx`, `frontend/src/styles.css`. |
| **Conversation centralization** | WORKING | Chat streaming, image attachment, model status, memory preview, and history are shared through `BrainConversation`. |
| **Graph repositioning** | WORKING | Brain tabs descend Brain -> Memories -> Knowledge -> Relationships -> Graph; `/app#/knowledge-graph` remains available for advanced exploration. `frontend/src/routes.ts`, `frontend/src/pages/Brain.tsx`. |
| **Legacy route compatibility** | WORKING | `/ask` and `/chat` remain aliases into the Brain conversation. `frontend/src/routes.ts`, `tests/visual/v3.spec.js`. |

## v4.5.0 Product Experience Recovery RC — what changed

v4.5.0 restores first-run setup, workspace/model onboarding, model runtime
validation, Basic/Advanced/Admin mode clarity, and graph readability while
preserving the v4.4.0 physical Brain extraction and storage/runtime
architecture.

| Feature | Status | Evidence |
| --- | --- | --- |
| **First-run setup** | WORKING | App shell guide exposes Login -> Workspace Selection -> Environment Analysis -> Model Recommendation -> Model Installation -> Model Validation -> Mode Selection -> Brain Usage. `frontend/src/components/FirstRunGuide.tsx`, `tests/visual/v3.spec.js`. |
| **Model setup flow** | WORKING | Library Models uses the existing streamed prepare/load path and visible consent for runtime install/model download. `frontend/src/pages/Library.tsx`, `frontend/src/api/client.ts`. |
| **Gemma 4 runtime compatibility** | WORKING | Gemma 4 MLX models are marked unsupported when the installed MLX-VLM runtime lacks the Gemma 4 component; loader errors are converted into recovery guidance and alternatives. `latticeai/core/model_compat.py`, `tests/unit/test_model_compat.py`. |
| **Basic-mode product polish** | WORKING | Basic mode hides endpoint/module leakage in status badges, graph copy, model cards, and computer readiness while Advanced/Admin retain detail. `tests/visual/v3.spec.js`. |
| **Graph discoverability** | WORKING | Brain graph/search copy focuses on ideas, relationships, sources, focus, filtering, and readability without changing graph architecture. `docs/V4_5_0_GRAPH_UX_REPORT.md`. |

## v4.4.0 Brain Engine Extraction Release — what changed

v4.4.0 physically moves the Brain Core implementation into the standalone
`lattice_brain` package. It does not change user-facing feature behavior,
storage layouts, migrations, or the API surface.

| Feature | Status | Evidence |
| --- | --- | --- |
| **Physical Brain Core extraction** | WORKING | Graph (`lattice_brain.graph`), memory, context, conversations, ingestion, hook/multi-agent/agent runtime (`lattice_brain.runtime`), workflow, and portability physically live in `lattice_brain`; `latticeai/brain` holds deprecation shims only. `tests/unit/test_lattice_brain_isolation.py`. |
| **Isolation guarantee** | WORKING | Import-hook test fails on any `lattice_brain` → `latticeai` import; end-to-end Brain Core exercise runs without FastAPI. `tests/unit/test_lattice_brain_isolation.py`. |
| **Compatibility shims** | WORKING | Old `latticeai.brain.*`, `latticeai.core.hooks/multi_agent/workflow_engine/graph_curator`, `latticeai.services.ingestion/agent_runtime/kg_portability`, and flat `lattice_brain.*` graph paths alias the physical modules with identity preserved. Full unit suite. |

## v4.3.3 Dead-Code Cleanup Release — what changed

v4.3.3 promotes the post-cleanup main branch after the independent dead-code,
architecture, and runtime audit. It does not introduce feature behavior changes
beyond cleanup, safety, and documentation alignment:

| Area | Status | Evidence |
| --- | --- | --- |
| **Dead-code cleanup** | WORKING | The independent audit cleanup removed unused legacy/static/doc drift paths while preserving compatibility shims, user data paths, and release packaging boundaries. `docs/V4_3_2_DEADCODE_AUDIT_REPORT.md`. |
| **Architecture documentation correction** | WORKING | README, architecture, release notes, and changelog now describe the post-cleanup v4.3.3 release target while preserving v4.3.2 audit reports as historical evidence. |
| **Vercel/static-docs readiness** | WORKING | Vercel remains configured as a static documentation-only check through `vercel.json` and `scripts/build_vercel_static.mjs`; it must not deploy the localhost FastAPI runtime. |
| **README badges** | WORKING | PyPI, npm, VS Code Marketplace, Open VSX, CI, and license badges remain restored with explicit owner-published registry caveats. |
| **Release artifacts** | WORKING | Exact v4.3.3 wheel, sdist, npm tgz, VSIX, and Tauri DMG are the release target artifacts. |

## v4.3.2 Product Polish & Graph UX Overhaul — what changed

v4.3.2 polishes the v4.3.1 desktop product without redesigning the frontend,
Brain Core, storage, or agent/workflow architecture:

| Area | Status | Evidence |
| --- | --- | --- |
| **Brain graph explorer** | WORKING | Brain uses the existing `/knowledge-graph/graph` and hybrid-search APIs to render semantic groups, importance sizing, search, focus neighborhoods, collapsible groups, label modes, and real query results. `tests/visual/v3.spec.js`, `output/audits/v4.3.2-rc/screenshots/02-graph-explorer-before.png`. |
| **Structured product state** | WORKING | Brain, Ask, Capture, Act, Library, and System no longer expose raw JSON dumps in normal product flows; nested state is rendered through structured cards, operation results, and readable status panels. |
| **Archive import/restore UX** | WORKING | System exposes `.latticebrain` export, inspect, verify, import dry-run, confirmed import, restore dry-run, and confirmed restore through existing archive APIs. `output/audits/v4.3.2-rc/logs/archive-import-dry-run.json`. |
| **Desktop lifecycle** | WORKING | Rebuilt Tauri app starts the FastAPI sidecar on `127.0.0.1`, serves `/app`, and releases port 8765 after normal macOS quit. `output/audits/v4.3.2-rc/logs/desktop-shutdown-after-fix.txt`. |
| **Self-audit evidence** | WORKING | End-user flows were exercised with a real local backend, seeded upload, graph persistence, archive creation/verify/import dry-run, workflow create/run surfaces, device identity, storage status, screenshots, and a GIF walkthrough. `docs/V4_3_2_SELF_AUDIT_REPORT.md`. |
| **Release artifacts** | WORKING | Exact v4.3.2 wheel, sdist, npm tgz, VSIX, and Tauri DMG built and validated. `docs/V4_3_2_VALIDATION_REPORT.md`. |

## v4.3.1 End-User Audit Repair — what changed

v4.3.1 repairs the v4.3.0 end-user audit blockers without redesigning the
frontend, Brain Core, storage, or agent/workflow architecture:

| Area | Status | Evidence |
| --- | --- | --- |
| **Desktop sidecar startup** | WORKING | Tauri resolves installed/bundled backend launch paths, writes sidecar logs, exposes backend status, and shuts the child process down on close. |
| **npm clean install** | WORKING | `requirements.txt` is shipped in npm and sdist packaging; npm bootstrap fails closed when dependency install cannot complete. |
| **Model load privacy** | WORKING | `/models/load` and prepare streams refuse implicit engine installs or model downloads unless explicit consent is provided. |
| **Agent honesty** | WORKING | `/agents/api/run` returns unavailable when the orchestrator is simulation-only; simulation output is not recorded as real success. |
| **Workflow usability** | WORKING | Act workflows expose create, import, export, and run controls backed by existing `/workflows/api/*` endpoints. |
| **Storage/archive honesty** | WORKING | Configured ports, Postgres dependency status, sqlite-vec fallback, and `.latticebrain` bundle sections are reported from runtime state. |

## v4.3.0 Portability & Product Hardening — what changed

v4.3.0 hardens the v4.2 Brain Core/storage architecture into a portable,
user-safe desktop release candidate while preserving v4.2.0 capabilities, APIs,
and user data:

| Area | Status | Evidence |
| --- | --- | --- |
| **Portable `.latticebrain` archive** | WORKING | Archive format v2 includes the brain DB, blobs, portable JSON state, workspace export bundles when present, storage metadata, provenance, public device identity metadata, manifest hashes, inspect, verify, import, restore, and dry-run restore. Private device keys are excluded. `test_v42_brain_storage.py`. |
| **Backup / restore safety** | WORKING | Backup restore and archive restore/import require explicit confirmation unless dry-run; corrupt, partial, tampered, wrong-passphrase, and unsupported-version archives fail closed. `test_kg_portability.py`, `test_v42_brain_storage.py`. |
| **Pre-migration backup verification** | WORKING | Non-dry-run SQLite→Postgres migration creates and verifies a SQLite backup before copying data; failure stops migration before Postgres writes. `KGPortabilityService.migrate_sqlite_to_postgres`. |
| **Tauri desktop hardening** | WORKING | Tauri exposes backend origin/status/restart/shutdown, records missing-runtime errors, kills the sidecar on close, and starts with loopback/default-off environment guards. `src-tauri/src/main.rs`, `npm run desktop:tauri:check`. |
| **Privacy / local-first guard** | WORKING | Telegram is disabled by default; token presence alone does not enable Telegram or external connectors; product hardening status reports credentials separately from enabled egress. `test_config.py`, `test_v43_product_hardening.py`. |
| **Enterprise/admin status** | WORKING | `/admin/product-hardening` reports startup posture, storage mode, backup health, device identity, permissions, external integration status, and fail-closed behavior. |
| **API + UI portability controls** | WORKING | OpenAPI regenerated to 318 paths; System settings exposes archive export, inspect, verify, restore dry-run, confirmed restore, backup health, storage, Docker, and migration controls through FastAPI APIs. |
| **Release hardening** | WORKING | Release artifact validation checks exact wheel, sdist, npm tgz, VSIX, and Tauri DMG paths; target-version artifact cleanup avoids stale RC rebuilds. |

## v4.2.0 Brain Core & Storage Rebuild — what changed

v4.2.0 extracts the backend Digital Brain boundary into the independent
`lattice_brain` package and adds a pluggable storage layer while preserving
v4.1.0 APIs, data, and frontend behavior:

| Area | Status | Evidence |
| --- | --- | --- |
| **Brain Core package** | WORKING | `lattice_brain` exposes `BrainCore`, `KnowledgeGraphStore`, memory/context/conversation facades, archive support, and storage engines. FastAPI constructs the graph/conversation runtime through `BrainCore`; root modules remain compatibility shims. `test_v42_brain_storage.py`. |
| **StorageEngine abstraction** | WORKING | `lattice_brain.storage.StorageEngine` defines the contract; `SQLiteEngine` is the default and owns SQLite connection/backup/restore/capability reporting. `test_v42_brain_storage.py`. |
| **sqlite-vec / vector search honesty** | WORKING | `SQLiteEngine` detects sqlite-vec when installed and otherwise reports `bruteforce-cosine`; existing vector search continues as real local cosine retrieval, not fake availability. `test_v42_brain_storage.py`, `test_kg_fts5.py`. |
| **Postgres / pgvector scale mode** | WORKING (opt-in) | `PostgresEngine` requires explicit DSN + optional dependency support, initializes schema and pgvector structures, verifies pgvector distance ordering, and reports unavailable states honestly. SQLite remains default; explicit Postgres selection does not silently fall back. `test_v42_postgres_migration_live.py`. |
| **Docker setup wizard** | WORKING (consent-gated) | `DockerPostgresWizard` writes a local Compose file and starts Docker only when consent is true; API route `/api/brain/storage/postgres/docker` exposes the same behavior. The live validation uses `pgvector/pgvector:pg16` only after explicit consent. `test_v42_brain_storage.py`, `test_v42_postgres_migration_live.py`. |
| **SQLite to Postgres migration** | WORKING | `SQLiteToPostgresMigrator` plans/copies all user tables, preserves rows, and is idempotent through `id`, declared primary keys, or `__source_rowid` conflict keys. Rowid-less FTS5 shadow tables are covered. API route defaults to dry-run planning. `test_v42_brain_storage.py`, `test_v42_postgres_migration_live.py`. |
| **Encrypted .latticebrain archives** | WORKING | AES-256-GCM encrypted archive create/restore over the SQLite brain DB and blob directory; wrong passphrase fails closed. API routes `/api/knowledge-graph/archive*`. `test_v42_brain_storage.py`. |
| **API + UI storage controls** | WORKING | OpenAPI regenerated to 313 paths; System settings exposes storage status, Docker plan/start with explicit consent, and migration planning through real FastAPI APIs. |

## v4.1.0 Frontend & Desktop Rebuild — what changed

v4.1.0 replaces the frontend implementation and desktop shell while preserving
the v4.0.1 backend contracts and Digital Brain capabilities:

| Area | Status | Evidence |
| --- | --- | --- |
| **Desktop shell** | WORKING | Tauri 2.0 primary shell in `src-tauri/` launches the local FastAPI backend and exposes its origin to the SPA; Electron fallback shell lives in `desktop/electron/`. `npm run desktop:tauri:check`, `node --check desktop/electron/main.cjs`. |
| **React SPA** | WORKING | `/app` serves the React + TypeScript + Vite build from `static/app`; source lives in `frontend/`. TypeScript, Vite build, frontend lint, and Playwright visual coverage validate the shell. |
| **Generated API client** | WORKING | `scripts/export_openapi.py` exports 308 FastAPI paths; `openapi-typescript` generates `frontend/src/api/openapi.ts`; `frontend/src/api/client.ts` routes JSON calls through the generated client. |
| **Primary navigation** | WORKING | Brain, Ask, Capture, Act, Library, System are the only primary navigation groups; legacy hash routes map to those capability groups. |
| **Graph-first experience** | WORKING | Brain uses Cytoscape.js for the Knowledge Graph; Act uses React Flow for workflow/agent graph surfaces. |
| **No CDN / offline app assets** | WORKING | Vite output is packaged under `static/app`, service worker precaches the app manifest/assets, and frontend lint scans active static/frontend files for CDN references. |
| **Capability preservation** | WORKING | Brain Core, storage, Knowledge Graph, chat, capture, agents/workflows, tools/MCP, models, workspaces, snapshots, network, and admin/security surfaces call existing backend APIs or show honest unavailable states. |

## v4.0.1 Digital Brain Platform — what changed

v4 makes the v3.6.0 identity true in the implementation. Honesty ledger for
the transformation (every line cites code + tests; suite: **585 unit tests**):

| Area | Status | Evidence |
| --- | --- | --- |
| **Workflow execution** | WORKING (live) | Tool nodes EXECUTE via `dispatch_tool` under governance; approval-requiring tools pause runs (`awaiting_approval`) with a durable cursor; `WorkflowEngine.resume` re-enters without re-running completed nodes; denial fails honestly. The pre-v4 `{recorded:true}` runners are gone; skill nodes refuse explicitly. `test_t7_workflow_execution.py` (6). |
| **Multi-Agent Runtime** | WORKING (llm) / labeled simulation | `llm_role_runner` calls the loaded model (planner/executor/reviewer); unparseable output FAILS the run with raw preserved; `mode` persisted on every run record; simulations never write into the KG. `test_t7_llm_runner.py` (7), `test_truth_floor_t1.py`. |
| **Async run engine** | WORKING | Agent/workflow run endpoints persist queued rows, execute on server-loop tasks via `asyncio.to_thread`, stream progress through the realtime SSE feed, support cooperative cancellation, and mark orphaned active runs `interrupted` on startup while preserving approval pauses. `test_t7_async_run_executor.py` (4), `test_realtime.py`. |
| **Custom agents** | WORKING | Registry config (system_prompt/max_tokens/temperature) actually loaded at run time; honest skip in simulation. `test_t7_llm_runner.py`. |
| **Triggers** | WORKING | Interval scheduler (missed firings → recorded skips) + brain-event triggers on `kg_ingest.*`; `__trigger__` provenance on fired runs. `test_t7_triggers.py` (5). |
| **Ingestion coverage** | WORKING (4/5 paths) | Chat, MCP, uploads, browser/notes all route through `services/ingestion.py` with provenance; `GET /knowledge-graph/provenance/coverage` reports the honest ratio. Workspace OS events remain direct and are not claimed as graph provenance. `test_t4_ingestion_unification.py` (6). |
| **Durable conversations** | WORKING | `latticeai/brain/conversations.py` — unbounded SQLite in the KG db file (backup carries it); idempotent legacy import; the 50-message cap is dead. `test_t4_conversation_store.py` (7). |
| **Garden absorption** | WORKING | Vault = user-owned markdown mirror; brain authoritative (dual-write + startup import); chat garden context = brain query; `/garden/tree` fixed (was a latent 500). `test_t4_garden_absorption.py` (5). |
| **Memory & Context systems** | WORKING | Typed Decision/Experience nodes via the pipeline (simulations refused); `ContextAssembler` builds budgeted, provenance-traced chat context — workspace memories injected at inference for the first time; trace persists with the answer. `test_t5_memory_context.py` (10). |
| **Keyword search** | WORKING (FTS5/fallback) | Trigram FTS5 index w/ Korean substring recall; honest LIKE fallback + `fts_enabled` capability report. `test_kg_fts5.py` (7). |
| **Workspace scoping (reads)** | WORKING | Search channels + graph view filter by membership; `legacy` rows stay machine-visible (documented). `test_t6_scoped_reads.py` (5). |
| **By-id authorization** | FIXED | Snapshot get/area/export/compare + memory delete authorize against the record's own workspace; `/workspace/os` registry leak closed; chat context user isolation. `test_truth_floor_t1.py` (11). |
| **Auth hardening** | WORKING | Hashed session tokens at rest (transparent migration), 8+ alnum password policy, PKCE on SSO. `test_t6_auth_hardening.py` (6). |
| **Identity, policy, invitations, workspace state** | WORKING | Users migrate to stable UUIDs while sessions preserve email compatibility; workspace memberships and KG identity columns re-key non-destructively; `core/policy.py` backs admin enforcement and `/admin/roles`; local invitation tokens create/list/accept/expire; Workspace OS state imports into `knowledge_graph.sqlite`, mirrors JSON for compatibility, and no longer truncates durable collections. `test_t6_identity_policy_invitations.py` (4). |
| **Device identity + Brain Network v1** | WORKING | Ed25519 device keys; signed exports (tamper refused; unsigned-legacy local imports allowed); workspace-filtered export (header no longer lies); paired-peer push/receive with replay protection; `/network/*`; `/app#/network` surfaces device fingerprint, peer pairing, unpair, and signed push. `test_t8_brain_network.py` (7), `tests/visual/v3.spec.js`. |
| **Graph curation** | WORKING | `curate()` gated topic promotion + real `importance_score`; `POST /knowledge-graph/curate`. `test_t4_ingestion_unification.py`. |
| **Packaging** | FIXED | Wheel ships `setup_wizard.py` (root setup.py collision resolved); installed-wheel smoke test (`scripts/wheel_smoke.py`) in release CI; side-effect-free `create_app` factory (subprocess-verified). `test_app_factory.py`, `test_setup_wizard.py`. |
| **Privacy (frontend)** | WORKING | Zero CDN references in shipped pages (fonts/icons/chart.js/marked vendored); sw.js precaches the React/Vite app manifest; frontend lint gates CDN and stale frontend references. `test_t9_privacy_vendoring.py` (6). |
| **Graph explorer** | WORKING | Cytoscape.js canvas (pan/zoom/fit, typed graph elements) replaces the retired static SVG/v3 canvas implementation; Knowledge Graph is the Brain landing view; graph-first navigation. `npm run lint:frontend`. |
| **v4 SPA parity + legacy retirement** | WORKING | Legacy static HTML/CSS/JS pages are deleted and compatibility GET/hash routes land in `/app`; parity views cover token-native account/profile/password, workspaces/org members/invitations/activation, snapshots/time-machine/compare/export/merge-restore, activity/presence, run approvals/cancel/progress, workflow trigger config/status, Brain Network pairing/push, chat context trace, and KG provenance coverage. `test_static_release_hygiene.py`, `test_workspace_os.py`, `tests/visual/v3.spec.js`. |
| **Honest numbers** | FIXED | Fabricated fusion meters removed; recall scores real (shared lexical scorer); recall graph branch fixed (`matches` key). |

### Known owner-only blockers (not implementation gaps)

| Gap | State today | Contract |
| --- | --- | --- |
| pptx history rewrite | deleted at HEAD only | owner decision (force-push) |
| Default production embedder | hash fallback, honestly reported | consent-gated wizard provisioning |

---

## v3.6.0 Knowledge Graph First — what's new

| Area | Status | Evidence |
| --- | --- | --- |
| **Unified ingestion pipeline** | WORKING | `latticeai/services/ingestion.py` — one entrypoint normalizes file/folder/web/tab/text into the graph, idempotent by content hash, routed through `dispatch_tool` (`pre_tool`/`post_tool` fire). `test_ingestion_pipeline.py` (8). |
| **Entity/relationship model** | WORKING | `kg_schema.py` +6 nodes (`Source`/`Repository`/`Meeting`/`Organization`/`Workflow`/`Agent`) +8 edges; additive, lossless `from_legacy`. `test_kg_schema_v36.py` (6). |
| **Browser/web ingestion** | WORKING (backend + extension scaffold) | `latticeai/api/browser.py` (`/api/browser/read-url`, `/ingest-current-tab`); MV3 extension under `browser-extension/` (127.0.0.1-only). `test_browser_ingestion.py` (10). Live URL fetch is exercised via an injected fetcher in tests; real fetch depends on network. |
| **Export/import/backup/restore** | WORKING | `latticeai/services/kg_portability.py` + `/api/knowledge-graph/{export,import,backup,restore,portability,provenance}`. Round-trip, dry-run, schema guard, backup→restore, integrity check. `test_kg_portability.py` (9). |
| **Provenance** | WORKING | `ingestion_provenance` table + `record/get/list/provenance_stats`; every node explainable. Covered by `test_ingestion_pipeline.py`. |
| **Hook coverage (ingestion)** | WORKING | KG ingestion now fires `pre_tool`/`post_tool` (closes the one v3.5.0 gap). `docs/RUNTIME_HOOK_COVERAGE_v3.6.0.md`, `test_runtime_coverage_v36.py`. |
| **KG-first UI** | WORKING (lint/build-verified) | Knowledge Graph view recast with Status/Sources/Capture/Backup tabs; `api.js` fallback-safe methods. Frontend gate: `lint:v3` 64/64 + asset build. Visual behavior not unit-tested (static frontend). |

Validation: unit suite green (incl. new v3.6.0 suites) · check:python · lint
64/64 · build (sdist+wheel+VSIX) · release artifact validation. Carry-over audit:
`docs/CARRYOVER_AUDIT_v3.6.0.md` (zero blocking items).

---

## v3.5.0 Stabilization — what hardened

| Area | Before v3.5.0 | v3.5.0 (verified) |
| --- | --- | --- |
| **Auth / OIDC** | SSO callback **base64-decoded** the `id_token` and trusted its claims — no signature/issuer/audience/expiry/nonce check (forgeable login) | Fail-closed verifier (`core/oidc.py`, RSA/JWKS): signature + `iss`/`aud`/`exp`/`nonce`; `alg:none`/`HS*` rejected; per-login nonce + state enforced (`test_oidc.py`) |
| **Proxy trust** | `client_ip` trusted `X-Forwarded-For`/`CF-Connecting-IP` unconditionally → per-IP rate limits spoofable | Forwarded headers honoured **only** from `LATTICEAI_TRUSTED_PROXIES`; else peer IP (`test_proxy_trust.py`, bypass proof) |
| **Runtime hooks** | `read_file`/`edit_file`/`grep`/`clear_history`, computer-use loop, skill-eval bypassed `pre_tool`/`post_tool` | All routed through `dispatch_tool`; 100% of discovered tool/agent paths covered (`docs/RUNTIME_HOOK_COVERAGE_v3.5.0.md`, `test_runtime_coverage.py`) |
| **`tools.py`** | one 1,525-line module | `tools/` package (computer/filesystem/documents/local_files/knowledge/network/commands + base + registry); flat imports preserved; no circular imports |
| **CI syntax gate** | hand-maintained `py_compile` list (still referenced deleted `tools.py`) | `scripts/check_python.py` discovers + compiles 144 modules; auto-includes new files |
| **UI surfaces** | command-palette scrim blur + 19 legacy `backdrop-filter: blur` surfaces | zero blur surfaces in active v3 CSS; solid/crisp; 13/13 visual tests pass |

Validation: lint 64/64 · check:python 144 · unit 419 · integration 9 · visual 13 ·
build (sdist+wheel, `tools/` included, twine PASSED).

---

## v3.4.1 Runtime Completion (prior release)

**Release type:** runtime completion — makes the v3.4.0 runtime systems
*verifiably* complete and corrects the v3.4.0 overclaims the implementation audit
found. Every v3.4.1 claim below is verified by a **live end-to-end run** against a
booted server (evidence: [`docs/assets/v3.4.1/e2e_runtime_log.txt`](docs/assets/v3.4.1/e2e_runtime_log.txt)),
not by unit tests, mocks, or endpoint existence alone.

## v3.4.1 Runtime Completion — what was partial in v3.4.0, now complete

The v3.4.0 audit found four runtime gaps; v3.4.1 closes them and the overclaims:

| Area | v3.4.0 reality (overclaim) | v3.4.1 (live-verified) |
| --- | --- | --- |
| **Hooks** | "fires from tools and workflows" — actually tool hooks fired from the **HTTP path only**; the agent + multi-agent + platform workflow paths bypassed hooks; 4/7 built-ins were advisory no-ops | One shared `dispatch_tool` lifecycle across **HTTP + agent + workflow** tool paths; workflow hooks fire from **both** the designer and platform paths; full `pre_/post_` × `run/tool/workflow/upload/index` lifecycle; **all 7 built-ins have real runners**; non-executable hooks are explicitly flagged `advisory` |
| **Local Agent** | `online`/`handshake`/`health`/`filesystem_access` were **hardcoded constants** | All **probed**: real filesystem write/read/delete, live graph reachability, derived `mode` (online/degraded/error), `pid`, `version`, handshake `latency_ms`, `last_seen`, `error` |
| **Connect Folder** | wired but **never run end-to-end** | Live: real folder → permission approval → index → Files table → retrieval → hybrid search |
| **Folder Watch** | verified only in isolation; `watchdog` absent at runtime | `watchdog` installed + declared; live create→reindex→`post_index` hook; **restore-on-restart** verified |

Live E2E result (booted server, isolated data dir): **7/7 PASS** + restore-on-restart PASS.
**Method:** current classifications are traced through React views
(`frontend/src/pages/*.tsx`) → generated-client adapter
(`frontend/src/api/client.ts`) → FastAPI router (`latticeai/api/*.py`) →
service/core (`latticeai/services/*`, `latticeai/core/*`, top-level
`knowledge_graph.py`). Historical v3 sections retain their original audit
context when describing retired implementation paths.

**Frontend of record:** the v4.3.3 React/Vite SPA at `/app` (`frontend/` source,
`static/app/` build output). The legacy static pages have been removed;
compatibility GET/hash routes land in the matching `/app#/...` surface.

**Status legend**

| Status | Meaning |
| --- | --- |
| **WORKING** | End-to-end path exists in code and functions in a normal local run. |
| **PARTIAL** | Works for some cases / degraded or config-dependent / missing pieces. |
| **PLACEHOLDER** | UI renders but no real wiring, or backend exists with no UI caller. |
| **DISABLED** | Intentionally off with an honest "not available in this build" message. |
| **BROKEN** | Wired but errors / misbehaves. |

**Overall finding.** Lattice AI's `/app` is, before this release, already
unusually honest: the API adapter never fabricates data — it reports
`source: "live"` vs `"unavailable"` (`api.js:59-73`) and the chat fallback says
so in words (`api.js:393-408`). Most surfaces are **WORKING** or honestly
**DISABLED**. v3.3.0 fixed the handful of real gaps found in the audit; **v3.4.0
closes the remaining functionality gaps it had flagged** — hooks now execute,
uploads appear in Files, the Chat composer accepts images, agents run from their
own view, and the on-device local agent / connect-folder / folder-watch surfaces
are live (see [CHANGELOG](CHANGELOG.md)). Release-note files older than v4.5.0
are intentionally hidden from the Git-tracked public note surface. Enterprise
features remain intentionally **DISABLED**.

---

## Summary table

| Area | Headline status | v3.4.0 change |
| --- | --- | --- |
| Chat | WORKING + **VLM image input now WORKING** | Composer image attach/drag/paste/preview + Vision badge; `image_data` → `/chat` |
| Models / Local Models | WORKING (local inference dep-gated) | `/models` now reports a `vision` capability block |
| Files / File Ingestion | WORKING — **uploads now appear in Files** | Documents table from `/knowledge-graph/documents`; Connect Folder enabled |
| Retrieval / Hybrid Search / Search | WORKING | — |
| Knowledge Graph | WORKING (config-dependent) | `list_documents()` + `/knowledge-graph/documents` |
| Memory | WORKING (recall = workspace+graph) | — |
| Agents | WORKING — **run trigger now in the Agents view** | Run/Stop/Status/Queue/Logs console; pre/post-run hooks fire |
| Workflows / Planning / Pipeline | WORKING (deterministic) | Workflow start/end hooks fire |
| Skills | WORKING (registry + filesystem) | — |
| Hooks | **WORKING — full lifecycle (v3.4.1)** | Shared `dispatch_tool` across HTTP+agent+workflow tool paths; both workflow paths; upload+index granularity; all 7 built-ins have real runners; `advisory` flag |
| MCP / Tools / Marketplace | WORKING management; live MCP calls PARTIAL | Tool hooks fire from **all** tool paths (v3.4.1), not just HTTP |
| Settings / Home / My Computer | WORKING — **Local Agent real probes (v3.4.1)** | `/api/local-agent/status` probed (fs/graph/mode/pid/handshake-latency); was hardcoded in v3.4.0 |
| Authentication | WORKING | — |
| Admin | WORKING read surfaces; Enterprise DISABLED | unchanged — Enterprise stays honestly disabled |

---

## Chat

**WORKING — Send → model → streamed answer.** `chat.js:266 api.streamChat()` →
`api.js` POST `/chat` SSE → `latticeai/api/chat.py:252` → `_stream_chat`
(`chat.py:593-630`) yields `{chunk, model}` then trace + `[DONE]` →
`llm_router.stream_generate` (`llm_router.py:566-627`) for MLX-local or
OpenAI-compatible cloud. *Deps:* a loaded model; MLX (`mlx_vlm`) for local or
`OPENAI_API_KEY` for cloud. *Action:* none.

**WORKING — No-model-loaded handling.** `chat.py:343-355` returns 400
`{error:"no_model_loaded", action:"load_model"}`; adapter preserves it
(`api.js:284-291`) and the view shows an actionable banner, not a fake reply
(`chat.js:287-321`). Covered by `tests/unit/test_v3_chat_no_model.py`.

**WORKING — "Chat unavailable" transport fallback.** `simulateChat`
(`api.js:393-408`) emits an explicit "backend or active model is not reachable"
message tagged `source:"unavailable"`; fired only on real failures.

**WORKING — Conversation history list/open/delete.** `/history/conversations*`
(`chat.py:531-560`), persisted each turn (`chat.py:418, 615`).

**WORKING — Model pill / current model.** `chat.js:410-416` from `GET /models`.

**FIXED in v3.3.0 (was BROKEN) — Document-generation streaming.** The doc-gen
branch (`chat.py:422-464`) streams SSE keyed `text`, but the v3 parser only read
`data.chunk` (old `api.js:313`) → report requests rendered a *false* "Couldn't
reach the model" while the backend actually generated and saved the document
(`chat.py:448`). **Fix:** the parser now accumulates `data.chunk || data.text`
(`api.js`). *Action done.*

**PARTIAL → honest copy (grounding toggles).** The Knowledge Graph / Vector
chips set `state.grounding` (`chat.js:53-57`) and are sent on `/chat`, but
`ChatRequest` (`chat.py:32-44`) has no `grounding` field, so pydantic drops it —
the model always receives KG + gardener context (`chat.py:365-391`). The chips
*do* drive the retrieval-context preview's mode. **v3.3.0:** relabeled the chip
tooltip to "Show the … signal in the retrieval-context panel" so it no longer
implies it gates generation. *Future option:* add `grounding` to `ChatRequest`
and honor it.

**WORKING in v3.4.0 (was PLACEHOLDER) — VLM image input.** The backend already
accepted `image_data` (base64) on `/chat`, decoded it and injected screenshot
context (`chat.py:187-210, 393-396`); v3.4.0 adds the composer affordance:
attach button + hidden file input, drag-and-drop, clipboard paste, a thumbnail
preview with remove, and `image_data` is sent on send (`chat.js`). A
**Vision Enabled / Disabled** badge reads the new `vision` block from `/models`
(`models.py` `_vision_capability`), which derives `supports_vision` from the
active model's compat profile (`model_compat.get_model_profile`). *Live VLM
inference output* still requires a loaded vision model (e.g. an MLX-VLM build) —
runtime-pending, honestly badged when absent.

**Notes / known minor gaps (not changed):** the side-panel retrieval context is
real when `/api/search/hybrid` and `/api/graph` respond (PARTIAL, honest empty
states otherwise); the `current URL` built-in command (`chat.py:328-341`) is
dead from v3 (adapter never sends `client_url`); several command/agent responses
are Korean-only while the SPA is English (i18n inconsistency).

---

## Models / Local Models

**WORKING — Model list + load/unload/switch + recommendations.** `GET /models`
(`models.py:255`), `/models/load` (`:274`), `/models/switch` (`:293`),
`/models/unload[-all]` (`:302/:308`), `/models/recommendations` (`:315`);
engine management `/engines/install|verify-cloud|pull-model|prepare-model[/stream]`
(`models.py:136-204`), `/setup/set-api-key` (`:232`). The adapter distinguishes
loaded vs available honestly (`api.js models()` 148-170). Backed by
`services/model_catalog.py`, `model_runtime.py`, `model_recommendation.py`.

**PARTIAL — Local MLX inference actually runs.** Requires Apple Silicon +
optional `mlx-vlm` (`pyproject.toml` `[local]` extra). Without it, local
generation is unavailable and chat reports `no_model_loaded` — honest, not fake.
Cloud (OpenAI-compatible) works with a key. *Action:* none; document the MLX
dependency in release notes.

**WORKING — Embeddings provider status.** `GET /api/embeddings/status`
(`api.js:189-202`) surfaces the active provider/grade/dims; the default
`LocalEmbeddingModel` is a deterministic feature-hashing embedder honestly
labeled "fallback" (`local_embeddings.py:48`).

---

## Files / File Ingestion

**FIXED in v3.3.0 (was PLACEHOLDER) — Manual document upload.** A complete
backend already existed — `POST /upload/document` (`tools.py:421-434`) →
`process_uploaded_document` (`upload_service.py:15-96`): extension whitelist
(`.pdf/.docx/.xlsx/.pptx/.txt/.md/.csv`), 10 MB cap, magic-byte check, parse,
**chunk → embed → knowledge-graph ingest** (`knowledge_graph.ingest_document`),
audit log. But **no v3 view called it**; the Files drop zone had zero handlers
(decorative). **Fix:** `files.js` drop zone + header/empty-state buttons now do a
real drag-and-drop / picker upload via new `api.uploadDocument()` (multipart),
with progress + result toasts and a table refresh. *Action done.*

**WORKING — Indexed-sources table.** `files.js:116 GET /workspace/indexing`
(`workspace.py:295`) → `WORKSPACE_OS.build_indexing_dashboard` reads real KG
stats + `graph.local_sources()` (`knowledge_graph.py:1709-1743`).

**WORKING in v3.4.0 (was DISABLED) — Connect / watch a folder.** The "desktop
local agent" framing was misleading: the Lattice server *is* the on-device agent
(it runs locally with filesystem access). v3.4.0 surfaces the existing backend.
Files (and My Computer) now expose **Connect folder** → `api.connectFolder(path)`
which runs request → self-approve (the click is the consent) → index + watch
against `/knowledge-graph/local/index` (`local_knowledge_api.py:289-317`). A
Connected-folders panel lists sources with live **Folder Watch** state from
`/knowledge-graph/local/sources` and a Stop-watching action. The watcher
(`LocalKnowledgeWatcher`) genuinely fires debounced reindex on create/update/
delete (verified) when `watchdog` is installed — it is a declared dependency
(`requirements.txt`, `pyproject.toml`); when absent it honestly reports
`available:false` (`local_knowledge_api.py:75-91`).

**WORKING (API-only) — Indexing controls + local read/write/serve.**
`/workspace/indexing/{id}/pause|resume|remove` (`workspace.py:302-318`),
`/local/list|read|serve|write` with approval gating (`local_files.py:42-99`).

**FIXED in v3.4.0 — uploaded documents now appear in Files.** The v3.3.0
limitation (uploads created Document/Chunk nodes but the Files table only listed
`local_sources`, so uploads were searchable but invisible) is resolved.
`KnowledgeGraphStore.list_documents()` (`knowledge_graph.py`) surfaces every
`Document` node with its ingest + index state (`ingested` → `indexed` once
retrieval chunks exist), exposed at `GET /knowledge-graph/documents`
(`knowledge_graph_api.py`). The Files "Uploaded documents" table reads it via
`api.documents()` and **re-hydrates after every upload**, so a just-uploaded file
appears immediately — completing the upload → Files → Knowledge Graph → Hybrid
Search → Chat path. *Verified* end-to-end (ingest a doc → `list_documents` reports
it `indexed` with chunk count).

---

## Retrieval / Hybrid Search / Search

**WORKING — Hybrid (fused) search.** `hybrid-search.js:97 api.hybridSearch()` →
`POST /api/search/hybrid` (`search.py:65-78`) → `search_service.hybrid_search`
(`search_service.py:162-226`) genuinely runs keyword + vector + graph channels
and fuses with weighted `max(score, 1/rank)`, returning real per-signal
`source_scores`. No canned results anywhere in the path.

**WORKING — Vector / Keyword / Graph channels.** Vector cosine over
`vector_embeddings` (`knowledge_graph.py:3728-3797`); keyword SQLite LIKE +
ranking (`:3166-3225`); graph proximity + relationship expansion (`:3349/3424`,
`search_service.py:86-160`).

**WORKING — Index status + rebuild.** `/api/index/status` (`:186`) and
`/api/index/rebuild` (`:193`) back real `index_status`/`rebuild_vector_index`
(`knowledge_graph.py:3653/3543`).

**WORKING — Honest unavailable state when KG off.** Service raises → 404 →
adapter `source:"unavailable"` → view shows empty state, no fabricated results.

**PLACEHOLDER (cosmetic) — "How fusion scores a match" explainer meters.**
`hybrid-search.js:183` renders hardcoded illustrative bars (0.85/0.7/0.55) in the
pre-query intro. *Action:* label as illustrative (low priority).

---

## Knowledge Graph

**WORKING (config-dependent) — Graph view + stats.** `knowledge-graph.js` →
`api.graph()` (`/api/graph` then legacy `/knowledge-graph/graph`) and
`graphStats()` (`/knowledge-graph/stats`), backed by the real SQLite KG
(`knowledge_graph.py`, ~177 KB: nodes/edges/traverse/relationship search/vector
ops all implemented; `knowledge_graph_api.py`). Renders real extracted
entities/relations when `ENABLE_GRAPH` and data exist; honest unavailable empty
state otherwise. *Deps:* `LATTICEAI_ENABLE_GRAPH` (default true).

---

## Memory

**WORKING — Memory Manager dashboard.** `memory.js:56 api.memoryManager()` →
`memory.py:48` → `memory_service.manager` (`memory_service.py:123-190`) builds six
tiers from real stores (workspace/project from WorkspaceOS, agent from snapshots,
conversation from `chat_history.json`, graph/vector from KG). Honest
"unavailable" health when a tier has no backing.

**WORKING — Workspace/agent/conversation tiers; unified recall; compact.** Recall
(`memory_service.py:196-238`) merges workspace `search_memories` + KG `search`.
Compact dedupes and persists (`:277-294`).

**PARTIAL — Project / graph / vector tiers.** Real but config/scenario dependent
(org workspaces; `ENABLE_GRAPH`); some `size_bytes` are hardcoded `0` (shown as
"—", not faked).

**PARTIAL (API-only) — Prune / Clear.** Backend + adapter exist
(`memory.py:74-107`, `api.js`) but there is no UI control. **v3.3.0:** corrected
the view header + recall copy that overstated this (recall searches workspace +
graph, not all six tiers). *Action done (copy).* 

---

## Agents

**WORKING — Roster + runtime status.** `agents.js:57 api.agentRuntime()` →
`/agents/api/runtime/status` (`agents.py:60`) → `agent_runtime.py:138-158` from
real persisted `agent_runs`.

**WORKING — Multi-agent pipeline execution.** `POST /agents/api/run`
(`agents.py:161`) → `MultiAgentOrchestrator.run` (`multi_agent.py:460-561`) drives
planner→executor→reviewer with real handoffs, review, retry loop, and persisted
replayable runs. **The default runner is deterministic and LLM-free by design**
(`multi_agent.py:6-8`) — it reports "Completed N/M planned step(s)", it does not
call a model. Document this in release notes.

**WORKING — Agent Registry.** list/capabilities/register/enable-disable/remove
(`agent_registry.py` API + core), persisted to `agent_registry.json`; builtin
removal honestly blocked.

**WORKING in v3.4.0 (was PLACEHOLDER) — Run trigger from the Agents view.** The
Agents view now has a Run console: a goal field + role chips (seeded from
`runtime.default_pipeline`) → `api.runAgent(goal, roles)` (`POST /agents/api/run`)
with **Run / Stop / Status / Queue / Logs**. Runs are queued durably, then
completed by the async executor; logs poll the persisted row, the Queue tile
reflects `runtime.active_runs`, and Stop requests cooperative cancellation
(sync model/tool calls finish their current step before the final cancelled
status lands). *Verified* on a live server: a run completes (no model required)
and **fires pre_run + post_run hooks** (`ran:1` each) recorded in the hook run
log. No Planning-view dependency.

**Design note (not a bug):** registry enable/disable is metadata — execution
always runs `CORE_PIPELINE`; custom registered agents have no runner, so they are
not executable. The roster `state` and `runtime.ready` are constants
(`agent_runtime.py:130/147`).

---

## Workflows / Planning / Pipeline

**WORKING — Workflow definitions CRUD + run + replay.**
`/workflows/api/definitions*`, `/validate`, `/{id}/run`, `/runs`,
`/runs/{id}/replay` (`workflow_designer.py:71-201`) backed by
`core/workflow_engine.py`. Adapter methods in `api.js` (`workflowDefinitions`,
`runWorkflow`, …).

**WORKING — Planning.** `planning.js:56 api.runAgent()` executes the multi-agent
pipeline (same deterministic runner as Agents).

**PARTIAL — Pipeline view.** Renders ingest/embed/graph stages from real index
status; it visualizes flow rather than triggering arbitrary jobs.

---

## Skills

**WORKING — Skill registry + enable/disable/install/uninstall/update.**
`/workspace/skills*` (`workspace.py:522-562`) and marketplace `/skills/*`
(`mcp.py:257-305`), backed by `core/plugins.py` + on-disk `skills/` (real
`SKILL.md` directories). Execution occurs via the tool/agent runtime. *Action:*
none.

---

## Hooks

**WORKING in v3.4.0 (was PARTIAL/PLACEHOLDER) — hooks now execute.** The v3.3.0
honesty gap (registry-only, no dispatch site) is closed. `core/hooks.py` gains a
real execution engine: `HookContext` / `HookResult`, `register_hook(id, runner)`,
`run_hook`, `run_hooks(kind, …)`, and `fire_hook` (fire-and-forget). A hook runs
either via an **in-process runner** bound by its owning subsystem (built-ins —
`redact-secrets`, `audit-agent-run`, `pipeline-index-status` are bound at startup
in `server_app.py`) or, for user hooks, by executing their `command` as a
**subprocess** (context on stdin + `LATTICE_HOOK_CONTEXT` env). `pre_*` hooks
**gate**: a blocking `pre_run` aborts an agent run, a blocking `pre_tool` aborts
the tool call (a non-zero exit from a `pre_*` command hook blocks fail-closed).
Every dispatch is recorded to a bounded, persisted **run log** (`hooks_runs.json`)
exposed at `GET /api/hooks/runs`; `POST /api/hooks/run` fires on demand.

**v3.4.1 — full lifecycle coverage (corrects the v3.4.0 scope).** A single shared
`dispatch_tool` (`core/hooks.py`) drives `pre_tool → execute → post_tool` for
**all three** tool paths — the HTTP `/tools/*` routes
(`api/tools._tool_response`), the single-agent runtime (`core/agent.py` via
`AgentDeps.hooks`), and the workflow tool node (`platform_runtime._tool_node_runner`).
`pre_workflow`/`post_workflow` fire from **both** the designer endpoint and the
platform path (`platform_runtime.run_workflow_by_id` now passes `hooks` to
`WorkflowEngine`), so the multi-agent executor no longer bypasses workflow hooks.
The upload pipeline fires granular `pre_upload`/`post_upload` + `pre_index`/
`post_index`; the local-folder index and **folder-watch reindex** fire
`pre_index`/`post_index` too. **All 7 built-in hooks have real runners**
(`core/builtin_hooks.py`) — none is a silent no-op; a hook with no bound runner
and no command is flagged `advisory` in the registry + UI. Legacy `workflow`/
`pipeline` kinds are accepted and mapped forward. 19 unit tests
(`tests/unit/test_hooks_dispatch.py`). *Live-verified* (`e2e_runtime_log.txt`):
firing `builtin:redact-secrets` redacted a `token`; an HTTP tool call fired
pre_tool (real `sensitivity`/`policy` output) + post_tool; an agent run auto-fired
pre_run + post_run; an upload fired all four upload+index kinds.

---

## MCP / Tools / Marketplace

**WORKING — MCP management.** `/mcp/tools|installed|custom|connectors|claude-code-servers`,
`/mcp/registry/refresh`, `/mcp/recommend`, `/mcp/install` (`mcp.py:106-243`)
backed by `mcp_registry.py` (~41 KB) + `core/tool_registry.py`.

**PARTIAL — Live MCP tool calls.** `POST /mcp/call` (`mcp.py:354`) exists;
whether a call succeeds depends on actual connected/authenticated servers in the
environment. Honest by construction (failures surface as errors, not fake
success).

**WORKING — Tool registry + governance.** `/tools/permissions` and tool dispatch
(`api/tools.py`, `core/tool_registry.py`, `services/tool_dispatch.py`).

**WORKING — Marketplace templates.** five named agent templates +
clone/export/import/install over the local catalog (`core/marketplace.py`;
covered by `tests/unit/test_v32_platform.py`).

---

## Settings / Home / My Computer

**WORKING — Settings.** Theme/mode persist immediately (`settings.js`);
embeddings status is live; integration-readiness probes report live/unavailable
per endpoint. **v3.3.0:** the About panel now reads the version from `/health`
(was hardcoded `v3.1.0`) — single source of truth, no frontend version literal.

**FIXED in v3.3.0 — Home retrieval status.** `/api/index/status` is vector-centric
and emits no `pipelines` key, but `components.js` pillars/`indexChip`
(`:167/:200`) read `pipelines.{knowledge_graph.entities, vector_index.vectors,
hybrid.strategy, *.state}` → Home always showed a false "Retrieval status
unavailable". **Fix:** `api.indexStatus()` now synthesizes the `pipelines` shape
from the real index status (vectors) + KG stats endpoint (entities), staying
honest (unavailable stays unavailable; a missing entity count yields an
"unavailable" graph pillar, never a fabricated number).

**WORKING / PARTIAL — My Computer.** `/local/sysinfo` and
`/workspace/computer-memory` (`api.js sysinfo/computerMemory`). Hardware stats are
real where the host exposes them; consent-gated computer memory.

**WORKING (v3.4.1 real probes; v3.4.0 was hardcoded) — Local Agent + Connect
Folder + Folder Watch.** My Computer's **Local Agent** panel reads
`GET /api/local-agent/status` (`local_files.py`). **v3.4.0 hardcoded**
`online`/`handshake`/`health`/`filesystem_access` to `true`; **v3.4.1 probes them
for real**: a filesystem write→read→delete in the data dir, a live
`knowledge_graph.stats()` reachability call, and a derived `mode`
(online/degraded/error) — plus `pid`, `version`, handshake `latency_ms`,
`last_seen`, and an `error` string when a probe fails. No fake readiness: a fresh
instance shows 0 folders and `watcher_available:false` when `watchdog` is absent.
The Connect-Folder + Folder-Watch panel mirrors the Files surface (connect, list,
stop-watch). *Live-verified* (`e2e_runtime_log.txt`): `mode=online`, real `pid`,
`handshake.latency_ms`, `graph_reachable=true`, `error=null`. The "Local Agent"
is honestly the on-device Lattice runtime — no separate desktop install.

---

## Authentication

**WORKING — Session auth + RBAC + public mode.** `REQUIRE_AUTH` from config
(`server_app.py:172`); `require_user` raises when unauthenticated
(`server_app.py:764-766`); `require_admin` (`:837`) and a fixed RBAC model
(owner · admin · member · viewer, surfaced in `admin-permissions.js`). Public mode
(`IS_PUBLIC_MODE`, `server_app.py:160/962`) serves a public model and is honestly
labeled. Auth router `auth.py` + sessions `core/sessions.py`. *Action:* none.

---

## Admin

**WORKING — Read surfaces.** `/admin/summary|stats|users|audit|roles|policies`,
`/vpc/status`, `/admin/sso`, `/admin/enterprise` (`admin.py:61-243`) return real
local data (users, audit trail, roles) via `core/audit.py` + WorkspaceOS.

**DISABLED (honest) — Enterprise governance & mutations.** The admin views show
"not available in this build" for DLP rule editing (`admin-security.js:41`), SIEM
/ audit export (`admin-audit.js:44`), Private VPC (`admin-private-vpc.js:9`),
user management (`admin-users.js:11`), and permission editing
(`admin-permissions.js:176`). The backend `core/enterprise.py` /
`enterprise_admin.py` report every Enterprise capability `enabled=False` with a
`COMMUNITY_NOTICE` ("Enterprise extension point and is not [available]") and
**never gate any Community feature**. `siem_export_stub` returns the envelope
*shape* only. This is the honesty pattern the release targets. *Action:* none.

---

## Cross-cutting honesty mechanisms (verified)

- **No fabricated data.** `api.js withFallback` (`:67-73`) returns empty data +
  `source:"unavailable"` on any failure; `unavailableData` never invents
  counters. Every view renders a source badge.
- **No fake chat answers.** `no_model_loaded` is preserved; `simulateChat` is
  explicitly labeled unavailable.
- **Honest disabled states.** Folder connect, admin Enterprise features, and DLP
  all carry consistent "not available in this build" copy.
- **Deterministic agent runner** is documented as LLM-free, not hidden.

## Deployment readiness (evidence)

Lattice AI is a **local-first desktop product**, not a hosted Vercel FastAPI
application. v4.3.3 release prep keeps Vercel pinned to a static documentation-only
build through `vercel.json` and `scripts/build_vercel_static.mjs` so Git
integration checks do not auto-detect or deploy `server.py`. The Vercel output
is `vercel-static/index.html`; the real product runtime remains the Tauri
desktop app plus localhost FastAPI sidecar. See
`docs/V4_3_2_GITHUB_VERCEL_CHECK_REPORT.md`.
