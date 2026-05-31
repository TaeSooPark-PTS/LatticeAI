# Lattice AI Plugins

This directory holds **Plugin SDK** packages discovered by the Lattice AI server
at runtime. Each plugin is a folder containing a `plugin.json` manifest.

A plugin is an additive, versioned, permissioned unit that **extends** the
existing Skill / Tool / Workflow surfaces — it never replaces them. Installed
standalone skills keep working unchanged.

See [`docs/PLUGIN_SDK.md`](../docs/PLUGIN_SDK.md) for the full manifest schema,
permission model, lifecycle, and execution boundary.

## Bundled examples

| Plugin | What it shows |
| --- | --- |
| `hello-world` | Bundling a skill + a workflow template + a declarative action |
| `git-insights` | Declaring `run_tools` and surfacing read-only git insights through the permission boundary |

## Minimal manifest

```json
{
  "id": "my-plugin",
  "name": "My Plugin",
  "version": "1.0.0",
  "description": "What it does.",
  "lattice_version": ">=2.0.0",
  "permissions": ["read_workspace"],
  "provides": { "skills": [], "tools": [], "workflows": [], "actions": [] }
}
```

Permissions must be drawn from the SDK allow-list; the execution boundary
refuses any capability a plugin did not declare and was not granted at install.
