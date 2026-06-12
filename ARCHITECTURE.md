# Lattice AI v4.3.3 Architecture

This is the current v4.3.3 system map. It describes the shipped local-first
desktop product after the dead-code cleanup audit, not historical
v3/v4.0/v4.1/v4.2 plans.

## High-Level System Map

```mermaid
flowchart LR
  User["User"] --> Desktop["Tauri 2 Desktop App"]
  Desktop --> Shell["Desktop Shell"]
  Desktop --> SPA["React + TypeScript + Vite SPA"]
  Shell --> Sidecar["FastAPI Sidecar on localhost"]
  SPA --> Client["Generated OpenAPI Client"]
  Client --> Sidecar
  Sidecar --> Brain["lattice_brain Package"]
  Brain --> Storage["StorageEngine Interface"]
  Storage --> SQLite["SQLiteEngine Default"]
  Storage --> Postgres["PostgresEngine + pgvector Opt-In"]
  Brain --> Archives["Encrypted .latticebrain Archives"]
  Brain --> Runtime["Models, Agents, Workflows, Skills"]
```

The frontend never calls Python directly. The desktop app and browser UI talk to
FastAPI over localhost APIs, and FastAPI imports `lattice_brain` as the Brain
Core boundary.

## Desktop Startup

```mermaid
sequenceDiagram
  actor User
  participant App as Tauri Desktop App
  participant Sidecar as FastAPI Sidecar
  participant API as Localhost API
  participant UI as React SPA

  User->>App: Launch Lattice AI
  App->>Sidecar: Start bundled local backend
  Sidecar->>API: Bind localhost port
  App->>API: Poll health and runtime status
  API-->>App: Ready with version and mode
  App->>UI: Load static app assets
  UI->>API: Fetch app state through OpenAPI client
  User->>App: Quit
  App->>Sidecar: Shutdown child process
  Sidecar-->>App: Port released
```

Startup is local-only. Missing dependencies surface as actionable unavailable
states; they are not hidden behind fake success.

## Tauri Shell And Sidecar

```mermaid
flowchart TB
  subgraph Desktop["macOS Desktop Product"]
    DMG["DMG Installer"]
    Tauri["Tauri Shell"]
    Assets["Packaged React Assets"]
    Python["Bundled Python Runtime"]
    FastAPI["FastAPI Sidecar Process"]
  end

  DMG --> Tauri
  Tauri --> Assets
  Tauri --> Python
  Python --> FastAPI
  Tauri --> Health["Sidecar Health / Status"]
  Tauri --> Quit["Quit / Window Close Handler"]
  Quit --> Stop["Stop Sidecar Cleanly"]
```

Tauri is the primary desktop shell. Electron exists as a fallback-only shell and
is not the release target for v4.3.3.

## React/Vite Frontend

```mermaid
flowchart LR
  subgraph UI["React + TypeScript + Vite"]
    Nav["Primary Navigation"]
    BrainPage["Brain"]
    AskPage["Ask"]
    CapturePage["Capture"]
    ActPage["Act"]
    LibraryPage["Library"]
    SystemPage["System"]
    State["TanStack Query + Zustand"]
    Graphs["Cytoscape.js + React Flow"]
    Components["Tailwind + shadcn-style Components"]
  end

  Nav --> BrainPage
  Nav --> AskPage
  Nav --> CapturePage
  Nav --> ActPage
  Nav --> LibraryPage
  Nav --> SystemPage
  BrainPage --> Graphs
  ActPage --> Graphs
  BrainPage --> State
  AskPage --> State
  CapturePage --> State
  ActPage --> State
  LibraryPage --> State
  SystemPage --> State
  State --> OpenAPI["Generated OpenAPI Client"]
  OpenAPI --> Localhost["FastAPI localhost API"]
  Components --> Nav
```

Visible controls must either call real backend APIs or show an honest
unavailable state. v4.3.3 keeps the graph-first navigation: Brain, Ask,
Capture, Act, Library, and System.

## FastAPI Localhost API

```mermaid
flowchart TB
  FastAPI["FastAPI App on localhost"]
  Health["/health and runtime status"]
  Graph["Knowledge Graph APIs"]
  Search["Hybrid Search APIs"]
  Conversations["Conversation and Memory APIs"]
  Capture["Document Upload and Ingestion APIs"]
  Models["Model Catalog and Runtime APIs"]
  Agents["Agent Runtime APIs"]
  Workflows["Workflow Runtime APIs"]
  Admin["Workspace, Policy, Audit, Admin APIs"]
  Portability["Backup, Restore, Archive APIs"]
  Network["Brain Network and Device Identity APIs"]

  FastAPI --> Health
  FastAPI --> Graph
  FastAPI --> Search
  FastAPI --> Conversations
  FastAPI --> Capture
  FastAPI --> Models
  FastAPI --> Agents
  FastAPI --> Workflows
  FastAPI --> Admin
  FastAPI --> Portability
  FastAPI --> Network
  FastAPI --> BrainCore["lattice_brain"]
```

FastAPI is the product API source of truth. The frontend consumes generated
OpenAPI types and does not import or execute Python.

## `lattice_brain` Package

```mermaid
flowchart LR
  Brain["lattice_brain"]
  KG["Knowledge System"]
  Memory["Memory System"]
  Context["Context Assembler"]
  Ingestion["Ingestion + Provenance"]
  AgentRuntime["Agent Runtime"]
  WorkflowRuntime["Workflow Runtime"]
  Skills["Skills, Hooks, Plugins"]
  Archive["Portability + Signed Bundles"]
  StorageAbstraction["Storage Abstraction"]

  Brain --> KG
  Brain --> Memory
  Brain --> Context
  Brain --> Ingestion
  Brain --> AgentRuntime
  Brain --> WorkflowRuntime
  Brain --> Skills
  Brain --> Archive
  Brain --> StorageAbstraction
  KG --> StorageAbstraction
  Memory --> StorageAbstraction
  Ingestion --> StorageAbstraction
  Archive --> StorageAbstraction
```

`lattice_brain` is usable by FastAPI, CLI, tests, and future tools. Compatibility
shims remain for older imports, but new runtime construction flows through the
package and application services.

Packaging note (verified v4.3.3): `lattice_brain.core`, `lattice_brain.archive`,
and `lattice_brain.storage.*` are standalone implementations. The knowledge
graph, memory, context, conversation, and ingestion modules are physically
hosted in `latticeai/brain/` and re-exported through `lattice_brain.*` module
paths, so importing those `lattice_brain` modules currently pulls in
`latticeai.brain`. The Brain Core boundary is therefore an import-path contract,
not yet a fully independent distribution.

## StorageEngine

```mermaid
classDiagram
  class StorageEngine {
    <<interface>>
    +health()
    +capabilities()
    +open()
    +backup()
    +restore()
    +migrate()
    +vector_search()
  }

  class SQLiteEngine {
    +local_file_path
    +sqlite_vec_status()
  }

  class PostgresEngine {
    +connection_dsn
    +pgvector_status()
  }

  StorageEngine <|.. SQLiteEngine
  StorageEngine <|.. PostgresEngine
```

SQLite is the default local brain engine. PostgreSQL is optional scale mode and
must be explicitly configured.

## SQLite Default And PostgreSQL Opt-In

```mermaid
flowchart TD
  Start["Start App"] --> Config["Read Storage Configuration"]
  Config --> Default["No opt-in scale config"]
  Default --> SQLite["Use SQLiteEngine"]
  SQLite --> LocalBrain["Local brain file and backups"]
  SQLite --> VecCheck["Check sqlite-vec availability"]
  VecCheck --> VecYes["Report sqlite-vec active"]
  VecCheck --> VecNo["Report honest fallback"]

  Config --> OptIn["Explicit PostgreSQL configuration"]
  OptIn --> Consent["User/admin consent and DSN present"]
  Consent --> Postgres["Use PostgresEngine"]
  Postgres --> PgVector["Verify pgvector capability"]
  PgVector --> PgReady["Report scale mode ready"]
  PgVector --> PgFail["Fail closed with actionable error"]
```

Docker and PostgreSQL are never auto-started by default. SQLite remains required
for normal local-first operation.

## `.latticebrain` Portability

```mermaid
flowchart LR
  Source["Current Brain"] --> Export["Export Archive"]
  Export --> Manifest["Manifest + Version + Storage Metadata"]
  Manifest --> Contents["Graph, Memories, Conversations, Settings, Provenance"]
  Contents --> Sign["Signature / Signed Bundle Metadata"]
  Sign --> Encrypt["Encrypted .latticebrain File"]

  Encrypt --> Inspect["Inspect"]
  Encrypt --> Verify["Verify"]
  Encrypt --> DryRun["Import Dry-Run"]
  DryRun --> Confirm["Explicit User Confirmation"]
  Confirm --> Import["Import / Restore"]
  Verify --> Reject["Fail Closed on Mismatch or Corruption"]
```

Archive import and restore do not destructively overwrite user data without
explicit confirmation. Unsupported versions, corrupt files, wrong passphrases,
and signature mismatches fail closed.

## Backup And Restore

```mermaid
flowchart TD
  BackupStart["User Requests Backup"] --> Preflight["Preflight Integrity Check"]
  Preflight --> CreateBackup["Create Backup Artifact"]
  CreateBackup --> VerifyBackup["Verify Backup"]
  VerifyBackup --> Record["Record Backup Health"]

  RestoreStart["User Requests Restore"] --> RestoreDryRun["Restore Dry-Run"]
  RestoreDryRun --> RestoreReport["Integrity and Impact Report"]
  RestoreReport --> ConfirmRestore["Explicit Confirmation"]
  ConfirmRestore --> ApplyRestore["Apply Restore"]
  ApplyRestore --> VerifyRestore["Post-Restore Integrity Check"]

  Preflight --> BackupFail["Fail Closed on Corruption"]
  RestoreDryRun --> RestoreFail["Fail Closed on Unsupported or Partial Archive"]
```

Restore dry-run and integrity checks are user-facing product flows, not only
developer utilities.

## Local-First Privacy Boundary

```mermaid
flowchart LR
  subgraph Local["Default Local Boundary"]
    Desktop["Tauri Desktop"]
    API["FastAPI localhost"]
    SQLite["SQLite Brain"]
    Files["Local Files and Archives"]
    LocalModels["Local Model Runtime when available"]
  end

  subgraph External["Opt-In External Boundary"]
    CloudModels["Cloud Model Providers"]
    Telegram["Telegram"]
    BrainNetwork["Brain Network Peers"]
    Docker["Docker / PostgreSQL Setup"]
    Downloads["Model Downloads"]
    Updates["Update Checks"]
  end

  Desktop --> API
  API --> SQLite
  API --> Files
  API --> LocalModels
  API -. explicit user action .-> CloudModels
  API -. explicit user action .-> Telegram
  API -. explicit user action .-> BrainNetwork
  API -. explicit user action .-> Docker
  API -. explicit user action .-> Downloads
  Desktop -. documented updater path .-> Updates
```

Token presence alone must not start external communication. External connectors,
Brain Network, Docker setup, downloads, and cloud model calls require explicit
opt-in paths.

## Release Artifact Map

```mermaid
flowchart TB
  Source["Source Tree v4.3.3"] --> FrontendBuild["Vite Frontend Build"]
  Source --> PythonBuild["Python Build"]
  Source --> NpmPack["npm pack"]
  Source --> VsixBuild["VSIX Package"]
  Source --> TauriBuild["Tauri Build"]

  FrontendBuild --> StaticAssets["static/app Assets"]
  PythonBuild --> Wheel["dist/ltcai-4.3.3-py3-none-any.whl"]
  PythonBuild --> Sdist["dist/ltcai-4.3.3.tar.gz"]
  NpmPack --> Tgz["ltcai-4.3.3.tgz"]
  VsixBuild --> Vsix["dist/ltcai-4.3.3.vsix"]
  TauriBuild --> Dmg["src-tauri/target/release/bundle/dmg/Lattice AI_4.3.3_aarch64.dmg"]
  StaticAssets --> Wheel
  StaticAssets --> Tgz
  StaticAssets --> Dmg
```

Release uploads must use exact filenames. Do not upload a broad `dist/`
wildcard because historical artifacts can remain there.

## Vercel Static Documentation Build

```mermaid
flowchart LR
  Vercel["Vercel Git Check"] --> Config["vercel.json"]
  Config --> Other["Framework Preset: Other"]
  Config --> Build["node scripts/build_vercel_static.mjs"]
  Build --> Output["vercel-static/index.html"]
  Output --> DocsOnly["Documentation-only landing page"]
  DocsOnly -. does not deploy .-> Server["server.py / FastAPI Runtime"]
```

Vercel is configured as a harmless static documentation check. It must not
auto-detect `server.py`, deploy FastAPI, or host a fake desktop product.

## Known Limitations

- v4.3.3 is the GitHub Release target, but external registries are
  owner-published and can lag behind repository release preparation.
- PostgreSQL/pgvector is optional scale mode and needs explicit configuration.
- Docker is consent-gated and never starts automatically.
- Ask requires a loaded model for generated answers.
- Optional cloud providers require explicit keys and user action.
- Historical docs under `docs/` can describe older releases; use this file,
  `README.md`, `FEATURE_STATUS.md`, and v4.3.3 release notes for current
  behavior. v4.3.2 reports remain historical evidence for the product audit.

## Evidence Pointers

- Self-audit: `docs/V4_3_2_SELF_AUDIT_REPORT.md`
- Validation: `docs/V4_3_2_VALIDATION_REPORT.md`
- Cleanup audit: `docs/V4_3_2_DEADCODE_AUDIT_REPORT.md`
- Release notes: `RELEASE_NOTES_v4.3.3.md`
- Graph UX: `docs/V4_3_2_GRAPH_UX_REPORT.md`
- Product polish: `docs/V4_3_2_PRODUCT_POLISH_REPORT.md`
- GitHub/Vercel status: `docs/V4_3_2_GITHUB_VERCEL_CHECK_REPORT.md`
