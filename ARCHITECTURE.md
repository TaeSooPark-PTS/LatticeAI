# Lattice AI Current Architecture

Last updated for the 6.4.0 Digital Brain Quality Hardening target.

Lattice AI is a local-first Digital Brain that keeps your knowledge durable
across any AI model. Product category is the Digital Brain; core capability is
the private AI memory layer; UX metaphor is the Living Brain. The model is a
replaceable voice, while the user's conversations, documents, decisions,
relationships, workflows, and project context are durable assets.

The architecture keeps Brain plus conversation as the normal user surface,
keeps graph exploration available without making it the product identity, and
preserves the trust boundary around CSP, local file reads, secret redaction,
model downloads, and external communication opt-in. The structured model
capability registry exposes HF verification, hardware fit, modality, download
strategy, load strategy, license, and safety notes before consent.

## System Map

```mermaid
flowchart LR
  User["User"] --> Tauri["Tauri 2 Desktop Shell"]
  Tauri --> Assets["Packaged React/Vite Assets"]
  Tauri --> Sidecar["FastAPI Sidecar on Localhost"]
  Browser["Browser / Local App URL"] --> Assets
  Assets --> Client["Generated OpenAPI Client"]
  Client --> Sidecar
  Sidecar --> Services["latticeai Application Services"]
  Services --> Brain["Independent lattice_brain Brain Core"]
  Brain --> Storage["StorageEngine"]
  Storage --> SQLite["SQLite Default"]
  Storage --> Postgres["PostgreSQL + pgvector Opt-In"]
  Brain --> Archives["Encrypted .latticebrain Archives"]
  Brain --> Runtime["Models, Agents, Workflows, Skills"]
```

The frontend never imports Python. The desktop shell and browser UI call FastAPI
over localhost APIs. FastAPI composes application services around the independent
`lattice_brain` package.

## Product Flow

```mermaid
flowchart TD
  Start["Launch /app"] --> Login["Login: local profile"]
  Login --> Analysis["Environment Analysis"]
  Analysis --> Recommend["Recommended Models"]
  Recommend --> Consent["Explicit Install / Download Consent"]
  Consent --> Prepare["Install -> Download -> Validate -> Load"]
  Prepare --> BrainChat["Brain Chat Home"]
  BrainChat -. operator path .-> Admin["Separate Admin Console"]
  BrainChat --> Depth1["Level 1: Living Brain"]
  BrainChat --> Overview["Brain Overview: recent / older / topics"]
  BrainChat --> Depth2["Memory quick view"]
  BrainChat --> Depth3["Topic quick view"]
  BrainChat --> Depth4["Relationship quick view"]
  BrainChat --> Depth5["Full graph quick view"]
  Depth1 --> Depth2
  Depth2 --> Depth3
  Depth3 --> Depth4
  Depth4 --> Depth5
```

The graph is an implementation and exploration layer. It emerges from the Brain
after the user travels through memory, knowledge, and relationships; it is not
the first screen or a dashboard replacement.

The Admin Console is also separate from the normal user flow. Operators reach it
through `#/admin`; everyday users stay in the Brain surface unless they choose
to open admin controls.

## Tauri Shell

```mermaid
sequenceDiagram
  actor User
  participant App as Tauri App
  participant Sidecar as FastAPI Sidecar
  participant API as Localhost API
  participant UI as React App

  User->>App: Launch Lattice AI
  App->>Sidecar: Start bundled backend
  Sidecar->>API: Bind 127.0.0.1
  App->>API: Poll health
  API-->>App: Ready with version/mode
  App->>UI: Load packaged assets
  UI->>API: Fetch product state
  User->>App: Quit
  App->>Sidecar: Shutdown child process
```

Tauri 2 is the release desktop shell. Electron remains fallback-only. The
sidecar runs on localhost, reports health/version/mode, and is stopped by the
desktop lifecycle handler. Packaged production builds use a non-null CSP that
defaults to local assets and localhost API/WebSocket endpoints, blocks external
scripts/frames/objects, and keeps development CSP separate.

## React/Vite Frontend

```mermaid
flowchart LR
  subgraph UI["React + TypeScript + Vite"]
    ProductFlow["Login / Analysis / Recommendations / Install"]
    Brain["Brain Chat"]
    Depths["Living Brain Depths"]
    Capture["Files / Capture"]
    Automations["Agents / Workflows"]
    Models["Model Setup"]
    Settings["Care / Settings"]
    State["TanStack Query + Zustand"]
    Graphs["Cytoscape.js + React Flow"]
  end

  ProductFlow --> Brain
  Brain --> Depths
  Depths --> Graphs
  Capture --> State
  Automations --> State
  Models --> State
  Settings --> State
  State --> OpenAPI["Generated OpenAPI Types"]
  OpenAPI --> Localhost["FastAPI localhost API"]
```

Visible controls either call real backend APIs or show an honest unavailable
state. Model install/download/load work remains explicit-consent only.

## FastAPI Localhost API

```mermaid
flowchart TB
  API["FastAPI App"]
  API --> Health["/health and runtime status"]
  API --> Accounts["Local account/session APIs"]
  API --> Conversations["Conversation and memory APIs"]
  API --> Graph["Knowledge Graph APIs"]
  API --> Search["Hybrid search APIs"]
  API --> Capture["Document upload and ingestion APIs"]
  API --> Models["Model recommendation and runtime APIs"]
  API --> Agents["Agent runtime APIs"]
  API --> Workflows["Workflow runtime APIs"]
  API --> Storage["Storage / backup / restore APIs"]
  API --> Archive[".latticebrain archive APIs"]
  API --> Network["Brain Network and device identity APIs"]
  API --> BrainCore["lattice_brain"]
```

FastAPI is the product API source of truth. The generated OpenAPI client keeps
frontend calls aligned with backend routes. `app_factory.py` now exposes builder
seams for config, security, and Brain runtime construction; future decomposition
should continue by moving app composition into focused modules without restoring
import-time side effects.

## Brain Core

```mermaid
flowchart LR
  Brain["lattice_brain"]
  Brain --> GraphCore["Graph schema, projection, retrieval"]
  Brain --> Memory["Memory and context"]
  Brain --> Conversations["Conversations"]
  Brain --> Ingestion["Ingestion and provenance"]
  Brain --> Runtime["Hooks, agents, multi-agent runtime"]
  Brain --> Workflow["Workflow runtime"]
  Brain --> Storage["Storage abstraction"]
  Brain --> Portability["Backup, restore, archives"]
```

`lattice_brain` is the independent Brain Core used by FastAPI, CLI, tests, and
future tools. It contains graph, memory, context, conversations, ingestion,
agent/hook runtime, workflow, portability, archive, embeddings, and storage
modules. Compatibility shims remain in `latticeai`, but isolation tests prevent
`lattice_brain` from importing `latticeai`.

## Brain Quality Pipeline

```mermaid
flowchart LR
  Input["Conversation / Document / Graph Query"] --> Scope["Workspace + Owner Scope"]
  Scope --> Retrieval["Keyword + Vector + Graph Retrieval"]
  Retrieval --> Quality["lattice_brain.quality"]
  Quality --> Embed["Embedding fallback labels + drift / re-index plan"]
  Quality --> Fusion["BM25 + dense score fusion + reranker fallback"]
  Quality --> Memory["Memory candidate scoring / dedupe / conflict / retention"]
  Quality --> GraphQ["Graph confidence / evidence / duplicate metrics"]
  Quality --> ContextQ["Structured context sections + guardrails"]
  ContextQ --> Prompt["Attributed prompt context"]
  Quality --> Bench["Recall benchmark metrics"]
```

The 6.4.0 quality layer is intentionally non-destructive. It does not mutate
the graph schema or replace the existing ingestion pipeline. Instead it adds
explicit quality data structures around the existing Brain paths: fallback
embeddings are labelled fallback, provider/model drift produces a re-index
plan, retrieval combines lexical and dense signals through a local-safe fusion
contract, graph edges carry confidence/evidence metrics, and prompt context is
assembled with attribution, confidence, timestamp, and known/inferred/stale/
unknown guardrails.

Workspace scope is enforced before results enter the quality pipeline. Graph,
node, neighborhood, relationship, keyword, vector, graph, and hybrid retrieval
paths carry the caller's allowed workspace set. Memory Manager mutation paths
intersect requested ids/kinds with the caller's scoped memory set; global graph
clear is blocked until a workspace-safe graph delete path exists.

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

SQLite is the default local brain store. PostgreSQL/pgvector is optional scale
mode and requires explicit configuration. Docker/Postgres setup is consent-gated
and never starts automatically.

## Backup, Restore, And Portability

```mermaid
flowchart TD
  Backup["Create Backup"] --> VerifyBackup["Verify Backup"]
  Restore["Restore Request"] --> DryRun["Restore Dry-Run"]
  DryRun --> Confirm["Explicit Confirmation"]
  Confirm --> Apply["Apply Restore"]

  Export["Export .latticebrain"] --> Manifest["Manifest + hashes + metadata"]
  Manifest --> Encrypt["Encrypted archive"]
  Encrypt --> Inspect["Inspect"]
  Encrypt --> Verify["Verify"]
  Encrypt --> ImportDryRun["Import Dry-Run"]
  ImportDryRun --> ImportConfirm["Explicit Confirmation"]
  ImportConfirm --> Import["Import / Restore"]
```

`.latticebrain` archives are portable encrypted brain bundles. Inspect, verify,
dry-run import, and confirmed import/restore keep rollback and safety paths
available. Corrupt, partial, tampered, wrong-passphrase, or unsupported-version
archives fail closed.

## Local-First Boundary

```mermaid
flowchart LR
  subgraph Local["Default Local Boundary"]
    Desktop["Tauri Desktop"]
    API["FastAPI localhost"]
    SQLite["SQLite Brain"]
    Files["Local files and archives"]
    LocalModels["Local model runtime when available"]
  end

  subgraph External["Explicit Opt-In"]
    CloudModels["Cloud model providers"]
    Telegram["Telegram"]
    BrainNetwork["Brain Network"]
    Docker["Docker / PostgreSQL setup"]
    Downloads["Model downloads"]
    Updates["Update checks"]
  end

  Desktop --> API
  API --> SQLite
  API --> Files
  API --> LocalModels
  API -. user action .-> CloudModels
  API -. user action .-> Telegram
  API -. user action .-> BrainNetwork
  API -. user action .-> Docker
  API -. user action .-> Downloads
  Desktop -. documented updater path .-> Updates
```

Token presence alone must not start external communication. External
connectors, downloads, Docker, Brain Network, and cloud model calls require
explicit opt-in paths.

## Release Artifact Map

```mermaid
flowchart TB
  Source["Source Tree 6.4.0"] --> FrontendBuild["Vite Frontend Build"]
  Source --> PythonBuild["Python Build"]
  Source --> NpmPack["npm pack"]
  Source --> VsixBuild["VSIX Package"]
  Source --> TauriBuild["Tauri Build"]

  FrontendBuild --> StaticAssets["static/app Assets"]
  PythonBuild --> Wheel["dist/ltcai-6.4.0-py3-none-any.whl"]
  PythonBuild --> Sdist["dist/ltcai-6.4.0.tar.gz"]
  NpmPack --> Tgz["ltcai-6.4.0.tgz"]
  VsixBuild --> Vsix["dist/ltcai-6.4.0.vsix"]
  TauriBuild --> Dmg["src-tauri/target/release/bundle/dmg/Lattice AI_6.4.0_aarch64.dmg"]
  StaticAssets --> Wheel
  StaticAssets --> Tgz
  StaticAssets --> Dmg
```

Release uploads must use exact filenames. Do not upload `dist/*`.

## Known Limitations

- 5.2.0 added a structured model capability registry and user-facing catalog
  filtering without a backend security redesign.
- External registries can lag behind the GitHub Release because package-store
  publishing is owner-controlled.
- PostgreSQL/pgvector is opt-in scale mode; SQLite is the default.
- Docker, model downloads, cloud calls, Telegram, and Brain Network require
  explicit user action.
- Model-free states are reported honestly. The UI should not fabricate answers
  when no model is loaded.
- Historical reports under `docs/` preserve older release behavior and should
  not be rewritten as 6.4.0 claims.
