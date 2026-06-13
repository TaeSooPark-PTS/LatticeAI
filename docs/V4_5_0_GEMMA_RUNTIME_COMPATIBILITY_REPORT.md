# v4.5.0 Gemma Runtime Compatibility Report

Date: 2026-06-13

## Regression

Gemma 4 12B Instruct could appear ready while load failed with an MLX-VLM
module error for `gemma4_unified`.

## Fix

- Added a lightweight runtime compatibility check in
  `latticeai.core.model_compat`.
- Gemma 4 MLX models are marked unsupported when MLX and MLX-VLM are installed
  but the Gemma 4 `gemma4_unified` runtime component is absent.
- `/models`, `/models/load`, `/engines/prepare-model`, and streamed preparation
  now agree on unsupported status.
- Generic loader failures are converted into friendly recovery payloads.
- Recommendation classification can mark known runtime-incompatible models as
  `not_recommended`.

## User Guidance

The UI recommends:

- update MLX-VLM and re-check compatibility,
- use Qwen3-VL 8B or Qwen3-VL 4B locally,
- use Gemma 4 GGUF through Ollama, LM Studio, or llama.cpp when Gemma 4 is
  required.

## Tests

- `tests/unit/test_model_compat.py`
- `tests/unit/test_model_recommendation.py`
- `tests/visual/v3.spec.js`

## Evidence

Screenshot: `output/audits/v4.5.0-rc/screenshots/03-gemma-runtime-recovery.png`
