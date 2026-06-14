# Release Notes

The current release target is v5.1.0. The tracked release-note surface starts at
v4.5.0 so the public Git tree stays focused on the current product era.

## v5.1.0 - Product Trust & Clarity Release

Lattice AI v5.1.0 makes the product easier to understand and safer to trust:
it is a local-first private AI memory layer / Digital Brain where knowledge is
durable and models are replaceable.

### Highlights

- Rewrote the README first screen around the product reason to exist, practical
  use cases, local ownership, and model-agnostic Brain portability.
- Added `PRIVACY.md`, `docs/WHY_LATTICE.md`, and `docs/TRUST_MODEL.md`, and
  refreshed `FEATURE_STATUS.md`, `SECURITY.md`, and `ARCHITECTURE.md`.
- Removed `csp:null` from Tauri production config and added an app-shell CSP
  header validation gate.
- Centralized secret redaction for logs, audit payloads, security exports, and
  builtin hook packets.
- Disabled silent chat auto-file reads and required explicit consent for model
  download requests.
- Added `app_factory.py` builder seams for config, security, and Brain runtime
  construction.
- Bumped synchronized Python, npm, VSIX, Tauri, runtime, and static metadata to
  `5.1.0`.

### Expected Artifacts

- `dist/ltcai-5.1.0-py3-none-any.whl`
- `dist/ltcai-5.1.0.tar.gz`
- `dist/ltcai-5.1.0.vsix`
- `ltcai-5.1.0.tgz`
- `src-tauri/target/release/bundle/dmg/Lattice AI_5.1.0_aarch64.dmg`

Full notes: [RELEASE_NOTES_v5.1.0.md](RELEASE_NOTES_v5.1.0.md)

## v5.0.0 - Multilingual Brain Foundation Release

Lattice AI v5.0.0 starts the major-version cleanup line by making the product
usable in Korean or English from first launch through Brain exploration, while
preserving the existing AgentRuntime, ToolRegistry, Brain Core, Admin Console,
and graph foundations.

### Highlights

- Added a persisted Korean/English language choice to first-run onboarding,
  Brain home, graph exploration, and the Admin Console header.
- Localized login, environment analysis, model recommendation, model
  install/load, Brain quick views, starter prompts, save feedback, and graph
  fallback copy.
- Kept the technical-debt plan explicit: config centralization, KG
  stabilization, ToolRegistry characterization, AgentRuntime extraction, then
  app factory decomposition.
- Bumped synchronized Python, npm, VSIX, Tauri, runtime, and static metadata to
  `5.0.0`.

### Expected Artifacts

- `dist/ltcai-5.0.0-py3-none-any.whl`
- `dist/ltcai-5.0.0.tar.gz`
- `dist/ltcai-5.0.0.vsix`
- `ltcai-5.0.0.tgz`
- `src-tauri/target/release/bundle/dmg/Lattice AI_5.0.0_aarch64.dmg`

Full notes: [RELEASE_NOTES_v5.0.0.md](RELEASE_NOTES_v5.0.0.md)

## v4.7.2 - Intuitive Brain UX Release

Lattice AI v4.7.2 keeps the user experience centered on the Living Brain while
making it more direct for everyday users. First-run login no longer turns a
saved-user typo into a new empty Brain, model setup has a one-click recommended
path, and the Brain home exposes memory, topics, relationships, and the full
graph without requiring repeated exploratory clicks.

### Highlights

- Added saved-profile guards for email mismatch and wrong saved-user password.
- Added one-click recommended model setup and clearer large-download language.
- Added direct Brain view buttons: Memory, Topic, Relationship, and Graph.
- Added Brain overview cards for recent memories, older memories, and major
  topics, plus saved-to-memory feedback after chat.
- Refreshed visual validation and release evidence for the more intuitive Brain
  flow.
- Bumped synchronized Python, npm, VSIX, Tauri, runtime, and static metadata to
  `4.7.2`.

### Expected Artifacts

- `dist/ltcai-4.7.2-py3-none-any.whl`
- `dist/ltcai-4.7.2.tar.gz`
- `dist/ltcai-4.7.2.vsix`
- `ltcai-4.7.2.tgz`
- `src-tauri/target/release/bundle/dmg/Lattice AI_4.7.2_aarch64.dmg`

Full notes: [RELEASE_NOTES_v4.7.2.md](RELEASE_NOTES_v4.7.2.md)

## v4.7.1 - Admin Operations Release

Lattice AI v4.7.1 keeps the user experience centered on the Living Brain while
moving operational work into a separate Admin Console. Users get a simpler Brain
home; administrators get users, logs, security events, policies, and Brain
index operations without crowding the conversation surface.

### Highlights

- Added role permission visibility, audit log search, and severity filtering to
  the dedicated `#/admin` console.
- Added `/admin/log-retention` for local retention posture, retained events,
  prune candidates, and export-before-prune status.
- Split Admin Console data loading into a dedicated frontend hook so user Brain
  state and admin observability state do not share UI runtime state.
- Refreshed visual validation and release evidence for the separated admin
  experience.
- Bumped synchronized Python, npm, VSIX, Tauri, runtime, and static metadata to
  `4.7.1`.

### Expected Artifacts

- `dist/ltcai-4.7.1-py3-none-any.whl`
- `dist/ltcai-4.7.1.tar.gz`
- `dist/ltcai-4.7.1.vsix`
- `ltcai-4.7.1.tgz`
- `src-tauri/target/release/bundle/dmg/Lattice AI_4.7.1_aarch64.dmg`

Full notes: [RELEASE_NOTES_v4.7.1.md](RELEASE_NOTES_v4.7.1.md)

## v4.6.1 - Living Brain Release Refresh

Lattice AI v4.6.1 is the publishable Living Brain release refresh. It preserves
the v4.6.0 Living Brain implementation while moving the release artifacts and
owner publishing commands to `4.6.1` because PyPI versions/files are immutable
once published or reserved.

### Highlights

- Bumped synchronized Python, npm, VSIX, Tauri, runtime, and static metadata to
  `4.6.1`.
- Refreshed README around the current Login -> Environment Analysis ->
  Recommended Models -> Install & Load -> Brain Chat flow.
- Captured fresh Living Brain screenshots/GIF evidence for Brain Chat and the
  five Brain depths.
- Updated architecture and release docs without redesigning the backend
  architecture.
- Kept tracked release notes visible from v4.5.0 through v5.1.0.

### Expected Artifacts

- `dist/ltcai-4.6.1-py3-none-any.whl`
- `dist/ltcai-4.6.1.tar.gz`
- `dist/ltcai-4.6.1.vsix`
- `ltcai-4.6.1.tgz`
- `src-tauri/target/release/bundle/dmg/Lattice AI_4.6.1_aarch64.dmg`

Full notes: [RELEASE_NOTES_v4.6.1.md](RELEASE_NOTES_v4.6.1.md)

## v4.6.0 - Living Brain Experience

Lattice AI v4.6.0 makes the Brain the product. First launch now moves through
Login, environment analysis, recommended models, guided install/load, and then
the living Brain conversation; memories, knowledge, relationships, and the
advanced graph sit behind progressive disclosure.

### Highlights

- First launch opens to Login only, then a friendly setup flow that recommends
  models instead of exposing a catalog.
- Home after model load opens directly into Brain plus conversation instead of
  a graph, dashboard, or status surface.
- The living Brain remains visible while chatting and reacts to listening,
  recall, thinking, planning, and agent/workflow activity.
- `/ask` and `/chat` remain compatible routes but now land in the Brain
  conversation.
- Primary navigation is reduced to Brain, Memory, Files, Automations, Models,
  and Settings.
- The graph is preserved as advanced exploration at the deepest Brain layer.

### Expected Artifacts

- `dist/ltcai-4.6.0-py3-none-any.whl`
- `dist/ltcai-4.6.0.tar.gz`
- `dist/ltcai-4.6.0.vsix`
- `ltcai-4.6.0.tgz`
- `src-tauri/target/release/bundle/dmg/Lattice AI_4.6.0_aarch64.dmg`

Full notes: [RELEASE_NOTES_v4.6.0.md](RELEASE_NOTES_v4.6.0.md)

## v4.5.1 RC - Product Reimagining

Lattice AI v4.5.1 replaces the desktop product surface on top of the v4.5.0
capability recovery. It preserves Brain Core, StorageEngine, FastAPI, Tauri,
backup/restore, model runtime, and portability behavior while changing the
visible shell, navigation, onboarding, hierarchy, and visual language.

### Highlights

- Home, Ask, Add, Automate, Library, and Care replace the prior dashboard
  navigation as the primary product model.
- First-run setup becomes a non-technical journey: Make it yours, Choose a
  space, Meet your Mac, Pick a brain, Install locally, Try a question, Set the
  pace, Explore memory.
- The app shell now uses a compact premium desktop chrome, command palette,
  responsive mobile drawer, and ambient brain canvas.
- Global styling moves to a calmer Digital Brain palette with fixed responsive
  type sizing and 8px-or-smaller card radii.
- Legacy hash routes continue to resolve into the replacement SPA.

### Expected Artifacts

- `dist/ltcai-4.5.1-py3-none-any.whl`
- `dist/ltcai-4.5.1.tar.gz`
- `dist/ltcai-4.5.1.vsix`
- `ltcai-4.5.1.tgz`
- `src-tauri/target/release/bundle/dmg/Lattice AI_4.5.1_aarch64.dmg`

Full notes: [RELEASE_NOTES_v4.5.1.md](RELEASE_NOTES_v4.5.1.md)

## v4.5.0 RC - Product Experience Recovery

Lattice AI v4.5.0 restores the original end-user setup and model experience on
top of the v4.4.0 physical Brain extraction. It does not redesign
`lattice_brain`, StorageEngine, FastAPI, Tauri, backup/restore, or portability
architecture. This RC builds validated artifacts only; it does not tag, create a
GitHub Release, publish packages, or deploy.

### Highlights

- First-run setup now surfaces Login -> Workspace Selection -> Environment
  Analysis -> Model Recommendation -> Model Installation -> Model Validation ->
  Mode Selection -> Brain Usage from the app shell and command palette.
- Library Models exposes the existing prepare/load stream as a readable setup
  flow: Environment Analysis, Recommended Models, Install, Download Progress,
  Validate, Load, and Ready.
- Runtime install/model download remains explicit-consent only. No model files
  are downloaded and no local runtime installation starts from token/model
  presence alone.
- Gemma 4 MLX models are blocked from "ready" when installed MLX-VLM lacks the
  Gemma 4 `gemma4_unified` component. Users see friendly recovery guidance and
  alternatives such as Qwen3-VL local models or Gemma 4 GGUF through local
  server runtimes.
- Basic mode hides developer endpoint/module leakage in status badges, graph
  copy, model cards, and computer readiness while Advanced/Admin retain
  inspection detail.

### Expected Artifacts

- `dist/ltcai-4.5.0-py3-none-any.whl`
- `dist/ltcai-4.5.0.tar.gz`
- `dist/ltcai-4.5.0.vsix`
- `ltcai-4.5.0.tgz`
- `src-tauri/target/release/bundle/dmg/Lattice AI_4.5.0_aarch64.dmg`

Full notes: [RELEASE_NOTES_v4.5.0.md](RELEASE_NOTES_v4.5.0.md)
