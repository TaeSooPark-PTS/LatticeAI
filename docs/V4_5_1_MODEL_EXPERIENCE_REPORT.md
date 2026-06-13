# v4.5.1 Model Experience Report

## Goal

Normal users should be able to install and use a local model without learning
MLX, MLX-LM, MLX-VLM, Ollama, GGUF, or runtime internals.

## Preserved Capability

v4.5.1 keeps the v4.5.0 model recommendation and prepare/load stream:

- Environment analysis.
- Recommended model selection.
- Explicit download/install consent.
- Progress reporting.
- Validation.
- Load/ready state.
- Compatibility recovery guidance.

## Reimagined Presentation

The model experience now sits under Library in the new product shell. The first
session points users to "Pick a brain" and "Install locally" instead of runtime
labels. Calm mode keeps implementation terms hidden while Deep/Admin retain
diagnostics.

## Non-Goals

No model runtime behavior, download policy, local/cloud opt-in policy, or
compatibility routing was changed in v4.5.1.

## Evidence

- Model UI: `frontend/src/pages/Library.tsx`
- Model API client: `frontend/src/api/client.ts`
- Related historical runtime report:
  `docs/V4_5_0_MODEL_RUNTIME_UX_REPORT.md`
