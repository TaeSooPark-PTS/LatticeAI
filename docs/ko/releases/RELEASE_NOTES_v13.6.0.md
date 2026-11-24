[![English](https://img.shields.io/badge/English-Switch-475569?style=flat-square)](../../releases/RELEASE_NOTES_v13.6.0.md) [![한국어](https://img.shields.io/badge/한국어-현재_언어-2563eb?style=flat-square)](./RELEASE_NOTES_v13.6.0.md)

# Lattice AI v13.6.0 — Living Synthesis (2026-11-24)

워크스페이스에 남아 있던 마지막 TypeScript 5 의존성(`openapi-typescript@7.13.0`, `openapi-ts5` 별칭, `scripts/run_openapi_typescript.mjs`, `overrides`)을 무의존성 네이티브 TypeScript 7 (`tsgo`) OpenAPI 타입 생성기(`scripts/generate_openapi_types.mjs`)로 완전 대체하고, 5개의 대형 `tests/unit/test_cov_p1b_remaining*.py` 커버리지 묶음 파일을 공용 테스트 더블(`tests/unit/_runtime_test_fakes.py`)과 9개의 도메인별 소형 단위 테스트 모듈로 분할했습니다.

## 주요 변경 사항

- **TypeScript 5 완전 제거 및 네이티브 TypeScript 7 OpenAPI 타입 생성기 도입 (`scripts/generate_openapi_types.mjs`, `scripts/check_openapi_drift.mjs`, `package.json`, `package-lock.json`):**
  - `openapi-typescript@7.13.0`, `openapi-ts5`(`npm:typescript@^5.9.3`) 별칭, `scripts/run_openapi_typescript.mjs` 래퍼 및 `package.json` `overrides` 우회 설정을 제거하고 결정론적 무의존성 ESM OpenAPI 3.x 타입 생성기(`scripts/generate_openapi_types.mjs`)로 대체했습니다.
  - `typecheck:frontend`를 네이티브 `typescript@7.0.2`(`tsgo`) 기반 `npx tsc -p tsconfig.json --noEmit`으로 직접 실행하도록 복원해 `package-lock.json` 전체에서 TypeScript 5 패키지를 완전히 제거했습니다.
  - 415개 API 경로·457개 오퍼레이션 전체에서 `openapi-fetch` `paths`, `components["schemas"]`, `operations` 타입 호환성과 Vitest 100% 커버리지를 유지합니다.
- **Python 단위 테스트 도메인 모듈 분할 (`tests/unit/_runtime_test_fakes.py`, `tests/unit/test_*.py`):**
  - 총 3,379줄 규모의 대형 커버리지 묶음 파일 5개(`tests/unit/test_cov_p1b_remaining*.py`)를 도메인별 소형 모듈 9개(각 170~449줄)로 분할했습니다: `test_model_engines_servers.py`, `test_model_engines_install_and_download.py`, `test_model_runtime_loading_flow.py`, `test_api_lifespan_csrf_and_users.py`, `test_tools_filesystem_knowledge_and_search.py`, `test_core_config_sessions_and_quiet.py`, `test_model_runtime_services_and_audit.py`, `test_worker_foundation_and_runtime_edges.py`, `test_worker_router_and_edge_cases.py`.
  - 5곳에 중복 정의되어 있던 `_Done`, `_Popen`, `_UrlResp`, `_fast_deadline` 테스트 더블을 `tests/unit/_runtime_test_fakes.py`로 통합해 45개 전체 테스트와 `96%+` Python 라인 커버리지를 그대로 유지했습니다.

## 정확한 빌드 산출물

- `dist/ltcai-13.6.0-py3-none-any.whl`
- `dist/ltcai-13.6.0.tar.gz`
- `ltcai-13.6.0.tgz`
- `dist/ltcai-13.6.0.vsix`
- `src-tauri/target/release/bundle/dmg/Lattice AI_13.6.0_aarch64.dmg`
