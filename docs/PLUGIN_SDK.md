# Lattice AI Plugin SDK

The Plugin SDK is the extension layer for the Lattice AI Agentic Workspace
Platform. v2.1.0 keeps the v2.0 plugin model and adds execution observability plus
local marketplace-template foundations. It lets you package skills, tools,
workflow templates, and actions into one versioned, permissioned unit — a
*plugin*. A plugin is a directory under the configured `plugins` root that ships
a `plugin.json` manifest.

The SDK is intentionally **additive**. Plugins *extend* the existing Skill, Tool,
and Workflow surfaces; they never replace them. Standalone skills that are already
installed keep working untouched, and a plugin that bundles a skill registers it
through the existing skill registry rather than owning a parallel one.

> **Compatibility.** v1.x data and APIs are preserved. The Plugin SDK adds new
> code (`latticeai/core/plugins.py`, `latticeai/api/plugins.py`) and new
> persisted state (`plugin_registry` inside the Workspace OS store). Nothing in
> the v1.x contract changes. The new HTTP routes live under `/plugins/registry`
> and friends, which do **not** collide with the pre-existing
> `/plugins/directory` marketplace routes.

The host SDK version is exposed as:

```python
PLUGIN_SDK_VERSION = "2.1.0"
```

## v2.1 additions

- `execute_action(...)` emits `plugin_started`, `plugin_completed`, and
  `execution_failed` through the existing Workspace OS timeline/realtime feed.
- Plugin outputs can be carried inside agent context packets and replayed from
  agent/workflow run history.
- The local template catalog (`latticeai.core.marketplace`) adds Plugin,
  Workflow, and Agent template metadata, export/import, install hooks, and a
  template registry without introducing a cloud marketplace service.

---

## The `plugin.json` manifest

Every plugin ships a `plugin.json` at the root of its directory. The manifest is
parsed and validated into an immutable `PluginManifest`.

### Schema

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `id` | string | yes | Lowercase alphanumeric with `-` or `_`, 2–64 chars (`^[a-z0-9][a-z0-9_-]{1,63}$`). Directory name should match. |
| `name` | string | yes | Human-readable name. Falls back to `id` if omitted. |
| `version` | string | yes | Semantic version (`^\d+\.\d+\.\d+([.-][0-9A-Za-z.]+)?$`). |
| `description` | string | no | Short summary. |
| `author` | string | no | Author or organization. |
| `lattice_version` | string | no | Minimum host version this plugin requires. May be bare (`"2.1.0"`) or prefixed (`">=2.1.0"`). Empty means "any host". |
| `permissions` | string[] | no | Must be a subset of the [permission allow-list](#permissions). Unknown values are rejected. |
| `provides` | object | no | What the plugin contributes. Keys must be in `("skills", "tools", "workflows", "actions")`; each value is a list of names. |
| `entrypoint` | string | no | Reserved for an optional code entrypoint. |
| `homepage` | string | no | Project / docs URL. |

The `provides` object declares the surfaces the plugin extends:

```json
{
  "provides": {
    "skills": ["..."],
    "tools": ["..."],
    "workflows": ["..."],
    "actions": ["..."]
  }
}
```

The allowed keys are fixed:

```python
PLUGIN_PROVIDES = ("skills", "tools", "workflows", "actions")
```

### Validation rules

`validate_manifest(data, *, path="")` returns `(manifest_or_None, errors)`. A
manifest with any error returns `None` for the manifest, so a caller can never
accidentally treat an invalid plugin as runnable. The validator enforces:

- `id`, `name`, and `version` are present.
- `id` matches the id pattern; `version` is a valid semantic version.
- `permissions` is a list and every entry is in `PLUGIN_PERMISSIONS`.
- `provides` is an object, every key is in `PLUGIN_PROVIDES`, and every value is
  a list.
- If `lattice_version` is set, the host must be compatible (see
  [version compatibility](#version-compatibility)).

```python
def validate_manifest(
    data: Dict[str, Any], *, path: str = ""
) -> Tuple[Optional[PluginManifest], List[str]]:
    ...
```

### Parsed manifest

```python
@dataclass(frozen=True)
class PluginManifest:
    id: str
    name: str
    version: str
    description: str = ""
    author: str = ""
    lattice_version: str = ""
    permissions: Tuple[str, ...] = ()
    provides: Dict[str, List[str]] = field(default_factory=dict)
    entrypoint: str = ""
    homepage: str = ""
    path: str = ""
```

`PluginManifest.public()` returns the JSON-safe shape used by the API, including a
computed `compatible` flag and the on-disk `path`:

```json
{
  "id": "hello-world",
  "name": "Hello World",
  "version": "1.0.0",
  "description": "...",
  "author": "Lattice AI",
  "lattice_version": "2.0.0",
  "permissions": ["read_workspace", "run_skills"],
  "provides": {
    "skills": ["hello_skill"],
    "workflows": ["hello-workflow"],
    "actions": ["greet"]
  },
  "entrypoint": "",
  "homepage": "https://github.com/TaeSooPark-PTS/LatticeAI/blob/main/docs/PLUGIN_SDK.md",
  "path": "/abs/path/to/plugins/hello-world",
  "compatible": true
}
```

---

## Permissions

Permissions are a small, fixed allow-list. A manifest may only request
permissions from this set; the execution boundary refuses any capability the
plugin did not declare *and* was not granted at install time.

```python
PLUGIN_PERMISSIONS = (
    "read_workspace",
    "write_workspace",
    "read_graph",
    "write_graph",
    "run_tools",
    "run_skills",
    "run_workflows",
    "run_agents",
    "network",
    "manage_memory",
)
```

| Permission | Grants |
| --- | --- |
| `read_workspace` | Read workspace state. |
| `write_workspace` | Mutate workspace state. |
| `read_graph` | Read the knowledge graph. |
| `write_graph` | Write to the knowledge graph. |
| `run_tools` | Invoke tools (required by the `run_tool` action). |
| `run_skills` | Invoke skills (required by the `run_skill` action). |
| `run_workflows` | Invoke workflows (required by the `run_workflow` action). |
| `run_agents` | Invoke agents (required by the `run_agent` action). |
| `network` | Make outbound network calls. |
| `manage_memory` | Read/write persistent memory. |

The list is kept deliberately small so the Enterprise seam can layer
finer-grained policy on top without changing the community contract. Each
permission maps to a concrete thing the [execution boundary](#permissioned-execution-boundary)
will or will not allow.

---

## Version compatibility

A plugin's `lattice_version` is treated as a **minimum**: the host major version
must match, and the host version must be greater than or equal to the required
version. An empty/missing requirement means "any host".

```python
def is_compatible(required: str, current: str = PLUGIN_SDK_VERSION) -> bool:
    """True if a plugin requiring ``required`` runs on ``current`` host version.

    ``required`` is a minimum (major must match, host must be >= required).
    Empty / missing requirement is treated as "any host".
    """
```

A leading `>=` is stripped before comparison, so both forms below behave
identically:

```json
{ "lattice_version": "2.0.0" }
{ "lattice_version": ">=2.0.0" }
```

Examples against a host of `2.1.0`:

| Required | Compatible | Why |
| --- | --- | --- |
| `""` (missing) | yes | Any host. |
| `2.0.0` / `>=2.0.0` | yes | Same major, host `>=` required. |
| `2.1.0` / `>=2.1.0` | yes | Same major, exact current host. |
| `2.1.0` | no | Host is lower than the required minimum. |
| `1.0.0` | no | Major mismatch. |
| `3.0.0` | no | Major mismatch. |

Compatibility is checked at validation time and again at install time, so an
incompatible plugin can never be activated.

---

## Lifecycle

```
discover  ->  validate  ->  install  ->  enable / disable  ->  uninstall
```

Lifecycle *state* (installed / enabled / version / status) is delegated to the
Workspace OS store via a small `store` port, so plugins reuse the same
local-first JSON persistence, workspace scoping, and timeline events as skills.
The registry itself owns only manifest parsing and the execution boundary.

```python
class PluginRegistry:
    def __init__(self, plugins_dir: Path | str, *, store: Any = None): ...
```

### Discover

`discover()` scans `plugins_dir` for `<id>/plugin.json` manifests. It performs no
import-time I/O — the filesystem is only touched when you call it. Each manifest
is validated; results are split into `valid` and `invalid`.

```python
def discover(self) -> Dict[str, Any]:
    # {"valid": List[PluginManifest], "invalid": List[{path, id?, errors}]}
    ...
```

`catalog()` merges discovered manifests with persisted lifecycle state for the
UI, returning `sdk_version`, the permission/provides vocabularies, the merged
`plugins` list, any `invalid` entries, `plugins_dir`, and `total`.

### Validate

A manifest can be validated standalone (without touching disk) via
`validate_manifest` or over HTTP via `POST /plugins/validate`.

### Install

`install(plugin_id, *, register_skill=None)` activates a discovered plugin. It
re-checks compatibility, registers the skills the plugin bundles through the
**existing** skill registry, and records lifecycle state in the store.

```python
def install(
    self,
    plugin_id: str,
    *,
    register_skill: Optional[Callable[[str, str], Any]] = None,
) -> Dict[str, Any]:
    ...
```

`register_skill(skill_name, plugin_id)` is injected by the host so plugins
*extend* the existing skill registry instead of owning a parallel one. For each
name under `provides.skills`, the registry calls `register_skill`; registration
is best-effort (a failure for one skill does not abort the install). The returned
dict contains the public manifest, the persisted `registry` entry, and the list
of `registered_skills`.

Install persists this entry into the store's `plugin_registry`:

```python
entry = self.store.mark_plugin_installed(
    plugin_id,
    version=manifest.version,
    metadata={
        "permissions": list(manifest.permissions),
        "provides": {k: list(v) for k, v in manifest.provides.items()},
        "registered_skills": registered_skills,
    },
)
```

The granted permissions are captured in `metadata.permissions` at this point —
this is the "granted at install time" set the execution boundary enforces.

If the plugin is missing/invalid, or its `lattice_version` is incompatible with
the host, `install` raises `PluginError`.

### Enable / Disable

```python
def set_enabled(self, plugin_id: str, enabled: bool) -> Dict[str, Any]: ...
```

A disabled plugin remains installed but cannot execute actions (the boundary
returns `blocked`). Enable/disable is a toggle on the persisted registry entry.

### Uninstall

```python
def uninstall(self, plugin_id: str) -> Dict[str, Any]: ...
```

Uninstall marks the registry entry `installed: false` and `enabled: false`. It is
non-destructive to the on-disk plugin directory; re-installing re-activates it.

### Persisted state

State lives in `WorkspaceOSStore.plugin_registry`, a dict keyed by `plugin_id`.
The store methods used by the registry are:

```python
def list_plugin_registry(self) -> Dict[str, Any]: ...
def mark_plugin_installed(self, plugin_id, *, version="0.0.0",
                          metadata=None) -> Dict[str, Any]: ...
def mark_plugin_uninstalled(self, plugin_id) -> Dict[str, Any]: ...
def set_plugin_enabled(self, plugin_id, enabled) -> Dict[str, Any]: ...
```

A persisted entry looks like:

```json
{
  "id": "hello-world",
  "installed": true,
  "enabled": true,
  "version": "1.0.0",
  "install_status": "ready",
  "validation_status": "valid",
  "metadata": {
    "permissions": ["read_workspace", "run_skills"],
    "provides": {
      "skills": ["hello_skill"],
      "workflows": ["hello-workflow"],
      "actions": ["greet"]
    },
    "registered_skills": ["hello_skill"]
  },
  "updated_at": "..."
}
```

Each lifecycle mutation also records a Workspace OS timeline event
(`plugin_installed`, `plugin_uninstalled`, `plugin_enabled`, `plugin_disabled`)
under the `plugins` channel.

---

## Permissioned execution boundary

Plugin actions never run arbitrary code directly. They pass through
`execute_action`, which enforces enablement and permissions, then dispatches to a
host-provided runner.

```python
def execute_action(
    self,
    plugin_id: str,
    action: str,
    args: Optional[Dict[str, Any]] = None,
    *,
    runners: Optional[Dict[str, Callable[..., Any]]] = None,
) -> PluginExecutionResult:
    ...
```

`runners` maps a capability (`"tools"`, `"skills"`, `"workflows"`, `"agents"`) to
a callable the host injects. Built-in actions map to a capability and the
permission they require:

| Action | Capability | Required permission |
| --- | --- | --- |
| `run_tool` | `tools` | `run_tools` |
| `run_skill` | `skills` | `run_skills` |
| `run_workflow` | `workflows` | `run_workflows` |
| `run_agent` | `agents` | `run_agents` |
| *(any other)* | `actions` | *(none)* |

The boundary applies these checks in order:

1. **Unknown / invalid plugin** → `status: "error"` (`plugin not found or invalid`).
2. **Disabled plugin** → `status: "blocked"` (`plugin is not enabled`). When a
   store is wired, the plugin must be enabled (falling back to `installed`).
3. **Permission not declared in the manifest** → `status: "blocked"`
   (`plugin did not declare required permission '...'`).
4. **Permission not granted at install time** → `status: "blocked"`
   (`permission '...' not granted at install time`). The granted set comes from
   the persisted `metadata.permissions`.
5. **No host runner wired for the capability** → `status: "skipped"`
   (`no host runner for capability '...'`). This is safe-by-default: a missing
   runner never crashes the caller.
6. Otherwise the runner is invoked. Success → `status: "ok"` with `output`; a
   raised exception → `status: "error"` with `reason`.

The runner is called as:

```python
output = runner(plugin_id=plugin_id, action=action, args=args, manifest=manifest)
```

### Result shape

```python
@dataclass
class PluginExecutionResult:
    plugin_id: str
    action: str
    status: str  # "ok" | "blocked" | "error" | "skipped"
    output: Any = None
    reason: str = ""
```

`as_dict()` serializes it for the API:

```json
{
  "plugin_id": "git-insights",
  "action": "run_tool",
  "status": "ok",
  "output": "...",
  "reason": ""
}
```

`PluginError` is raised for validation / lifecycle / execution failures at the
registry level (for example, installing a missing or incompatible plugin).

---

## HTTP API

The API router is built with the same router-factory convention as the rest of
`latticeai.api`: `server_app` constructs the dependencies and passes them in; the
module never imports the app.

```python
def create_plugins_router(
    *,
    registry,
    require_user: Callable[[Request], str],
    require_admin: Callable[[Request], Any],
    append_audit_event: Callable[..., None],
    register_skill: Optional[Callable[[str, str], Any]] = None,
    plugin_runners_factory: Optional[Callable[[], Dict[str, Callable[..., Any]]]] = None,
    ui_file_response: Optional[Callable[[Path], Any]] = None,
    static_dir: Optional[Path] = None,
) -> APIRouter:
    ...
```

> All paths are namespaced under `/plugins/registry` (and sibling action routes)
> so they do **not** collide with the pre-existing `/plugins/directory`
> marketplace routes.

### `GET /plugins/registry`

Requires an authenticated user. Returns the full `catalog()` (SDK version,
permission/provides vocabularies, merged plugin list, invalid manifests,
`plugins_dir`, total).

### `GET /plugins/registry/{plugin_id}`

Requires an authenticated user. Returns the public manifest plus the persisted
registry state. `404` if the plugin is not found or invalid.

```json
{
  "plugin": { "...PluginManifest.public()..." },
  "registry": { "...persisted entry..." }
}
```

### `POST /plugins/validate`

Requires an authenticated user. Validates a manifest dict without touching disk.

```json
// request
{ "manifest": { "id": "...", "name": "...", "version": "1.0.0" } }

// response
{
  "ok": true,
  "errors": [],
  "manifest": { "...public()..." }
}
```

### `POST /plugins/install`

Requires **admin**. Installs the plugin (registering bundled skills via the
injected `register_skill`) and appends a `plugin_install` audit event. Returns
the install result. `400` on failure (`PluginError` message).

```json
{ "plugin_id": "hello-world" }
```

### `POST /plugins/uninstall`

Requires **admin**. Uninstalls the plugin and appends a `plugin_uninstall` audit
event.

```json
{ "plugin_id": "hello-world" }
```

### `POST /plugins/enable`

Requires an authenticated user. Enables the plugin.

```json
{ "plugin_id": "hello-world" }
```

### `POST /plugins/disable`

Requires an authenticated user. Disables the plugin.

```json
{ "plugin_id": "hello-world" }
```

### `POST /plugins/execute`

Requires an authenticated user. Runs an action through the
[execution boundary](#permissioned-execution-boundary), using runners from
`plugin_runners_factory()` (or an empty map if no factory is wired). Appends a
`plugin_execute` audit event including the resulting status.

```json
// request
{
  "plugin_id": "git-insights",
  "action": "run_tool",
  "args": { "tool": "git_status" }
}

// response — PluginExecutionResult.as_dict()
{
  "plugin_id": "git-insights",
  "action": "run_tool",
  "status": "ok",
  "output": "...",
  "reason": ""
}
```

### `GET /plugins/sdk`

Requires an authenticated user. Serves the Plugin SDK UI page (`plugins.html`).
Returns `404` if the UI response helper / static directory are not wired or the
page is missing.

### Request models

```python
class PluginActionRequest(BaseModel):
    plugin_id: str
    enabled: Optional[bool] = None
    version: Optional[str] = None

class PluginValidateRequest(BaseModel):
    manifest: Dict[str, Any] = {}

class PluginExecuteRequest(BaseModel):
    plugin_id: str
    action: str
    args: Dict[str, Any] = {}
```

---

## Bundled example plugins

Two example plugins ship with the platform under the `plugins` root.

### `hello-world`

Demonstrates bundling a skill, a workflow template, and a custom action. It
requests only `read_workspace` and `run_skills`.

```json
{
  "id": "hello-world",
  "name": "Hello World",
  "version": "1.0.0",
  "description": "Example plugin demonstrating the Lattice AI Plugin SDK: bundles a skill, a workflow template, and a greet action.",
  "author": "Lattice AI",
  "lattice_version": "2.0.0",
  "permissions": ["read_workspace", "run_skills"],
  "provides": {
    "skills": ["hello_skill"],
    "workflows": ["hello-workflow"],
    "actions": ["greet"]
  },
  "entrypoint": "",
  "homepage": "https://github.com/TaeSooPark-PTS/LatticeAI/blob/main/docs/PLUGIN_SDK.md"
}
```

On install, `hello_skill` is registered through the existing skill registry. The
`greet` action maps to the generic `actions` capability (no permission gate); it
runs only when the host wires an `actions` runner, otherwise it is reported
`skipped`.

### `git-insights`

Surfaces read-only git status and log insights through the permissioned tool
execution boundary. It declares its requirement as `">=2.0.0"`, demonstrating the
prefixed form of `lattice_version`.

```json
{
  "id": "git-insights",
  "name": "Git Insights",
  "version": "1.0.0",
  "description": "Example plugin that surfaces read-only git status and log insights through the permissioned tool execution boundary.",
  "author": "Lattice AI",
  "lattice_version": ">=2.0.0",
  "permissions": ["read_workspace", "run_tools"],
  "provides": {
    "tools": ["git_status", "git_log"],
    "actions": ["summarize_repo"]
  },
  "entrypoint": "",
  "homepage": "https://github.com/TaeSooPark-PTS/LatticeAI/blob/main/docs/PLUGIN_SDK.md"
}
```

Because it declares `run_tools`, a `run_tool` action passes the permission gate
(provided `run_tools` was also granted at install time) and dispatches to the
host's `tools` runner. Without a `tools` runner wired, the same call is reported
`skipped` rather than failing.

---

## Authoring checklist

1. Create a directory `plugins/<id>/` matching your `id`.
2. Add a `plugin.json` with `id`, `name`, and `version` at minimum.
3. Request only the permissions you need (subset of `PLUGIN_PERMISSIONS`).
4. Declare what you `provide` (`skills` / `tools` / `workflows` / `actions`).
5. Set `lattice_version` to your minimum supported host (e.g. `">=2.0.0"`).
6. Validate via `POST /plugins/validate` (or `validate_manifest`).
7. Install (admin), enable, and execute actions through `POST /plugins/execute`.

Remember: plugins **extend** existing skills, tools, and workflows — they never
replace them, and all v1.x data and APIs remain intact.
