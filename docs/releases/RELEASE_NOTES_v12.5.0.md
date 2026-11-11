# Lattice AI 12.5.0 — Deep Vector

> **Status: historical** — point-in-time release note.

2026-11-11

12.4.0 Sharp Weight expanded the verified model catalog with Qwen 3.8 27B and Gemma 4 26B QAT/OptiQ 4-bit builds. 12.5.0 Deep Vector integrates **Google EmbeddingGemma 2** (`google/embeddinggemma-2`, 768-dim) natively into Lattice AI with Hugging Face mean-pooling and L2 normalization, adds **hardware-aware embedding model recommendations** that calculate host RAM/VRAM reservations before recommending LLMs, migrates screenshot pixel diffing to Pillow 13+ `get_flattened_data()`, upgrades GitHub Actions CI/CD to Node 24 native major versions, and automates full OpenAPI specification and fragment synchronization in `scripts/bump_version.py`.

문은 **422 operations / 41 families**, 워커는 **20 routes**. 쓰기는 계속 `GraphWriter` 하나입니다.

## What landed

- **Google EmbeddingGemma 2 native integration.** Added native Hugging Face embedding provider support (`google/embeddinggemma-2`, `embeddinggemma-2`, `embeddinggemma-2-4bit`, `embeddinggemma-2-8bit`, `embeddinggemma-300m`, 768-dimensional vectors) in `latticeai/core/embedding_providers/text.py` with mean pooling, L2 normalization, task instruction prefixes, and automatic semantic activation in `resolve_embedder`. Deterministic hash embedding fallback is preserved when offline or without downloaded weights.
- **Hardware-aware PC-tailored embedding recommendations.** Modularized `recommend_embedding_for_host` in `rust/lattice-platform/src/modelops/models_catalog/embedding.rs` and `latticeai/services/embedding_recommendation.py`. Hosts with >=8GB RAM receive `google/embeddinggemma-2` with a 0.5GB–1.5GB reserved RAM budget; smaller hosts receive `google/embeddinggemma-300m` (0.3GB reserve). The host probe automatically computes `effective_ram_gb` before LLM tier selection.
- **Onboarding and Model Library embedding UI.** Added the PC-tailored EmbeddingGemma 2 recommendation card in `ProductFlow.tsx` with full i18n support, and surfaced Google EmbeddingGemma 2 in the Model Library (`Library.tsx`) embedding section with 1-click install and activation.
- **Pillow 13+ flattened data migration.** Replaced Pillow 14-deprecated `diff.getdata()` calls in `scripts/check_screenshot_pixel_delta.py` with `diff.get_flattened_data()`, supported by dedicated unit test coverage.
- **Node 24 native CI/CD workflows.** Added `FORCE_JAVASCRIPT_ACTIONS_TO_NODE24: "true"` across all 7 GitHub Actions workflows and upgraded `actions/checkout`, `actions/setup-python`, `actions/setup-node`, and `actions/upload-artifact` to Node 24 native versions.
- **Automated OpenAPI specification and fixture version synchronization.** Extended `scripts/bump_version.py` to synchronize `frontend/openapi.json` (`info.version`), `rust/fixtures/openapi/_envelope.json` (`spec.info.version`), all 13 OpenAPI fragment files, and `rust/fixtures/openapi/_index.json` with recomputed SHA-256 digests across 49 total targets.
- **Comprehensive end-to-end interactive UI verification.** Added Playwright test suite `tests/visual/v3.all_buttons_inspection.spec.js` asserting all routes, controls, tabs, modals, and buttons work with 0 console errors and 0 5xx responses.

## Artifacts

- `dist/ltcai-12.5.0-py3-none-any.whl`
- `dist/ltcai-12.5.0.tar.gz`
- `ltcai-12.5.0.tgz`
- `dist/ltcai-12.5.0.vsix`
- `src-tauri/target/release/bundle/dmg/Lattice AI_12.5.0_aarch64.dmg`
