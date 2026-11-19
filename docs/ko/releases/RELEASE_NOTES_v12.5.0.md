[![English](https://img.shields.io/badge/English-Switch-475569?style=flat-square)](../../releases/RELEASE_NOTES_v12.5.0.md) [![한국어](https://img.shields.io/badge/한국어-현재_언어-2563eb?style=flat-square)](./RELEASE_NOTES_v12.5.0.md)

# Lattice AI 12.5.0 — Deep Vector

> **상태: historical** — 해당 시점의 보존된 릴리스 노트입니다.

2026-11-11

12.4.0 Sharp Weight는 Qwen 3.8 27B 및 Gemma 4 26B QAT/OptiQ 4-bit 빌드로 검증된 모델 카탈로그를 확장했습니다. 12.5.0 Deep Vector는 **Google EmbeddingGemma 2**(`google/embeddinggemma-2`, 768차원)를 Hugging Face 평균 풀링 및 L2 정규화와 함께 Lattice AI에 네이티브 통합하고, LLM 추천 전에 호스트 RAM/VRAM 예약량을 먼저 계산하는 **하드웨어 인식 임베딩 모델 추천 시스템**을 추가했으며, 스크린샷 픽셀 비교를 Pillow 13+ `get_flattened_data()`로 전환하고, GitHub Actions CI/CD를 Node 24 네이티브 메이저 버전으로 업그레이드했으며, `scripts/bump_version.py`에 전체 OpenAPI 스펙·파편 자동 동기화를 구축했습니다.

진입점은 **422개 오퍼레이션 / 41개 패밀리**, Python 워커는 **20개 라우트**를 유지하며, 쓰기는 여전히 단일 `GraphWriter`를 통합니다.

## 주요 반영 사항

- **Google EmbeddingGemma 2 네이티브 통합.** `latticeai/core/embedding_providers/text.py`에 네이티브 Hugging Face 임베딩 프로바이더 지원(`google/embeddinggemma-2`, `embeddinggemma-2`, `embeddinggemma-2-4bit`, `embeddinggemma-2-8bit`, `embeddinggemma-300m`, 768차원 벡터)을 평균 풀링, L2 정규화, 태스크 지시 접두사, `resolve_embedder` 자동 시맨틱 활성화와 함께 추가했습니다. 오프라인이거나 가중치가 다운로드되지 않은 환경에서는 결정론적 해시 임베딩 폴백이 유지됩니다.
- **하드웨어 인식 PC 맞춤형 임베딩 추천.** `rust/lattice-platform/src/modelops/models_catalog/embedding.rs`와 `latticeai/services/embedding_recommendation.py`에 `recommend_embedding_for_host`를 모듈화했습니다. RAM 8GB 이상 호스트에는 `google/embeddinggemma-2`(0.5GB–1.5GB 예약 RAM 예산)를, 그보다 작은 호스트에는 `google/embeddinggemma-300m`(0.3GB 예약)을 추천하며, 호스트 프로브가 LLM 티어 선택 전에 `effective_ram_gb`를 자동 계산합니다.
- **온보딩 및 모델 라이브러리 임베딩 UI.** `ProductFlow.tsx`에 완전한 i18n을 지원하는 PC 맞춤형 EmbeddingGemma 2 추천 카드를 추가하고, 모델 라이브러리(`Library.tsx`) 임베딩 섹션에 원클릭 설치·활성화가 가능한 Google EmbeddingGemma 2를 노출했습니다.
- **Pillow 13+ `get_flattened_data()` 마이그레이션.** `scripts/check_screenshot_pixel_delta.py`에서 Pillow 14에서 폐기 예정인 `diff.getdata()` 호출을 `diff.get_flattened_data()`로 교체하고 전용 단위 테스트를 추가했습니다.
- **Node 24 네이티브 CI/CD 워크플로.** 7개 GitHub Actions 워크플로 전체에 `FORCE_JAVASCRIPT_ACTIONS_TO_NODE24: "true"`를 적용하고 `actions/checkout`, `actions/setup-python`, `actions/setup-node`, `actions/upload-artifact`를 Node 24 네이티브 버전으로 업그레이드했습니다.
- **OpenAPI 스펙 및 픽스처 버전 자동 동기화.** `scripts/bump_version.py`를 확장해 `frontend/openapi.json`(`info.version`), `rust/fixtures/openapi/_envelope.json`(`spec.info.version`), OpenAPI 파편 파일 13개 전체, 재계산된 SHA-256 다이제스트를 포함한 `rust/fixtures/openapi/_index.json`까지 총 49개 타깃을 자동 동기화합니다.
- **종단간 인터랙티브 UI 전수 검증.** 모든 라우트, 컨트롤, 탭, 모달, 버튼이 콘솔 오류 0건 및 5xx 응답 0건으로 동작함을 단언하는 Playwright 테스트 스위트 `tests/visual/v3.all_buttons_inspection.spec.js`를 추가했습니다.

## 배포 아티팩트

- `dist/ltcai-12.5.0-py3-none-any.whl`
- `dist/ltcai-12.5.0.tar.gz`
- `ltcai-12.5.0.tgz`
- `dist/ltcai-12.5.0.vsix`
- `src-tauri/target/release/bundle/dmg/Lattice AI_12.5.0_aarch64.dmg`
