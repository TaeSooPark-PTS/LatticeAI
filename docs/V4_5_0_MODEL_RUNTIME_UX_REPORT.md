# v4.5.0 Model Runtime UX Report

Date: 2026-06-13

## Restored Flow

Library Models now presents the intended model flow:

1. Environment Analysis
2. Recommended Models
3. Install
4. Download Progress
5. Validate
6. Load
7. Ready

## Implementation

- Frontend uses the existing `/engines/prepare-model/stream` API for progress.
- The consent checkbox is required before a setup action may install a runtime
  or download model files.
- Current model, top pick, computer readiness, and compatibility validation are
  visible in one workflow.
- Advanced/Admin modes show load IDs and engine details; Basic mode shows
  product-level status and recovery.

## Failure UX

Runtime failures show:

- a clear fallback or unsupported runtime state,
- friendly explanation,
- recovery guidance,
- alternatives,
- no raw Python module exception in Basic mode.

## Evidence

Screenshot: `output/audits/v4.5.0-rc/screenshots/02-model-setup-flow.png`
