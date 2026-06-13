# v4.5.0 Gemma Runtime Compatibility Report

Date: 2026-06-13

## Regression

Gemma 4 12B Instruct and Gemma 4 26B A4B were both presented as local Gemma 4
models, but they do not enter the same loader path. The first real divergence is
the local `config.json`: 12B declares `model_type: gemma4_unified`, while 26B
A4B declares `model_type: gemma4`. Installed MLX-VLM 0.5.0 can load the 26B
standard `gemma4` path, but it lacks `mlx_vlm.models.gemma4_unified`, and
MLX-LM does not provide a `gemma4_unified` loader either.

## Fix

- `latticeai.core.model_compat` reads the local model metadata before load and
  reports `runtime_update_needed` for 12B `gemma4_unified` when
  `mlx_vlm.models.gemma4_unified` is absent.
- `latticeai.models.router` keeps the v3 MLX-VLM path first and only retries
  MLX-LM for standard Gemma 4 metadata. It does not route `gemma4_unified`
  through the incompatible MLX-LM fallback.
- `/models` preserves pulled/ready state for no-alias local MLX models, so 26B
  A4B stays ready while 12B shows the honest runtime-update state.
- Generic loader failures are converted into friendly recovery payloads without
  hiding the root cause.
- Recommendation classification marks the 12B MLX snapshot not recommended until
  the installed runtime can load it, while the 26B A4B path remains recommended.

## User Guidance

The UI recommends:

- update MLX-VLM to a version with `mlx_vlm.models.gemma4_unified`
  (`mlx-vlm>=0.6.3`) before loading the 12B MLX snapshot,
- use Gemma 4 26B A4B locally while the 12B runtime update is pending,
- use Gemma 4 12B GGUF through Ollama, LM Studio, or llama.cpp as a local-server
  alternative.

## Tests

- `tests/unit/test_model_compat.py`
- `tests/unit/test_model_recommendation.py`
- `tests/unit/test_mlx_router_gemma4_fallback.py`
- `tests/unit/test_models_api_runtime_routing.py`
- `tests/visual/v3.spec.js`

## Evidence

Screenshot: `output/audits/v4.5.0-rc/screenshots/03-gemma-runtime-recovery.png`
