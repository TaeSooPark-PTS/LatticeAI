[![English](https://img.shields.io/badge/English-Switch-475569?style=flat-square)](../../releases/RELEASE_NOTES_v13.4.2.md) [![한국어](https://img.shields.io/badge/한국어-현재_언어-2563eb?style=flat-square)](./RELEASE_NOTES_v13.4.2.md)

# Lattice AI v13.4.2 — Living Synthesis (2026-11-24)

Apple Silicon 환경에서 계층형 RAM/SSD KV 캐시를 지원하는 **oMLX (`jundot/omlx`)** 엔진을 정식 통합하고, **`mlx-community/Qwen3.8-27B-4bit`** 모델을 32GB 이상 Mac의 추천 모델 및 다운로드 가능 모델 카탈로그에 등록했습니다.

## 주요 변경 사항

- **oMLX 엔진 통합 (`latticeai/models/model_providers.py`, `latticeai/services/model_engines.py`, `latticeai/services/model_runtime/`, `rust/lattice-platform/src/modelops/models_catalog/mod.rs`):**
  - `OPENAI_COMPATIBLE_PROVIDERS`, `ENGINE_INSTALLERS`, `LOCAL_ENGINES`, `/engines` 상태 API에 `omlx`를 등록했습니다.
  - 하드웨어 메모리 용량에 맞춰 KV 캐시 및 메모리 가드를 자동 조정하는 `ensure_omlx_server()` / `omlx_serve_args()`를 구현했습니다 (`--memory-guard balanced`, 32GB 기준 `--hot-cache-max-size 2GB` / 48GB+ 기준 `4GB`, `--paged-ssd-cache-dir ~/.omlx/kv-cache`, `--paged-ssd-cache-max-size 32GB`, `--max-concurrent-requests 2`, `--initial-cache-blocks 128`).
  - `org--repo` ↔ `org/repo` Hugging Face 캐시 정규화를 추가해 사용자가 `hf download`로 내려받은 모델을 oMLX가 즉시 인식·서빙할 수 있도록 했습니다.
- **추천 및 다운로드 카탈로그 (`latticeai/services/model_capability_catalog.py`, `latticeai/services/model_capability_registry.py`, `latticeai/services/model_catalog.py`):**
  - Apple M5 32GB 환경 실측 검증(oMLX 프리픽스 KV 캐시 히트 `69/74` 토큰, warm TTFT `0.45s`) 결과를 반영해 `mlx-community/Qwen3.8-27B-4bit`의 `recommended_ram_gb`를 `32.0`으로 조정했습니다.
  - `omlx:mlx-community--Qwen3.8-27B-4bit`를 비롯한 Apple Silicon MLX 모델들을 oMLX 추천·다운로드 카탈로그에 노출했습니다.

## 배포 아티팩트

- `dist/ltcai-13.4.2-py3-none-any.whl`
- `dist/ltcai-13.4.2.tar.gz`
- `ltcai-13.4.2.tgz`
- `dist/ltcai-13.4.2.vsix`
- `src-tauri/target/release/bundle/dmg/Lattice AI_13.4.2_aarch64.dmg`
