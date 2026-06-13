# v4.5.0 Gemma Runtime Compatibility Report

Date: 2026-06-13

## Regression

Gemma 4 12B Instruct regressed when the v4.5.0 compatibility preflight treated
the missing MLX-VLM `gemma4_unified` drafter module as a blanket unsupported
model verdict, even though Gemma 4 worked in v3 and the catalog includes
compatible local fallback runtimes.

## Fix

- `latticeai.core.model_compat` now reports `fallback_available` for this
  condition instead of `unsupported`.
- The router keeps the v3 MLX-VLM path first, then retries Gemma 4 through
  MLX-LM when MLX-VLM rejects the local metadata.
- `/models`, `/models/load`, `/engines/prepare-model`, and streamed preparation
  no longer block Gemma 4 solely because the optional drafter module is absent.
- Generic loader failures are converted into friendly fallback payloads.
- Recommendation classification no longer marks Gemma 4 `not_recommended`
  when a compatible local fallback runtime remains available.

## User Guidance

The UI recommends:

- try the v3 MLX-VLM path first,
- use the MLX-LM text fallback if MLX-VLM rejects the Gemma 4 metadata,
- use Gemma 4 GGUF through Ollama, LM Studio, or llama.cpp if both MLX local
  routes fail.

## Tests

- `tests/unit/test_model_compat.py`
- `tests/unit/test_model_recommendation.py`
- `tests/visual/v3.spec.js`

## Evidence

Screenshot: `output/audits/v4.5.0-rc/screenshots/03-gemma-runtime-recovery.png`
