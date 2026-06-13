# Lattice AI v4.5.0 UX Audit Report

## Audit Question

For every screen: would a non-technical user immediately understand what to do next?

## Findings Fixed

- Basic mode leaked implementation vocabulary such as runtime names, raw IDs, hook IDs, tool IDs, graph provenance, and admin diagnostics.
- First-run setup showed the journey, but lacked a dominant next action.
- Ask presented retrieval and graph trace debugging as user-facing context.
- Model recommendation mixed user action status with loader/runtime details.
- System/Admin screens looked like operational dashboards even in Basic mode.

## Changes Applied

- Added Basic-mode friendly summaries in `StructuredView`, `KeyValueList`, and `EntityList`.
- Hid internal IDs and low-level fields in Basic mode while keeping them in Advanced/Admin.
- Added `ModeGate` for diagnostics and Admin controls.
- Humanized tool and hook labels, for example `write_file` -> `Write File`.
- Renamed Basic navigation and panels:
  - `MCP` -> `Tool connections`
  - `Provenance` -> `Sources`
  - `Pipeline` -> `Processing`
  - `Network` -> `Devices`
  - `Settings` -> `Preferences`

## Basic Mode Guardrails

Validated in Playwright:

- Basic model screen does not show `MLX`
- Basic model screen does not show `GGUF`
- Basic graph screen does not show endpoint leakage
- Admin controls require switching to Admin mode

## Remaining UX Risk

The app bundle remains a single large frontend chunk. This is not a functional or visual blocker, but route-level code splitting should be the next frontend performance refactor.
