[![English](https://img.shields.io/badge/English-Switch-475569?style=flat-square)](../../releases/RELEASE_NOTES_v14.1.1.md) [![한국어](https://img.shields.io/badge/한국어-현재_언어-2563eb?style=flat-square)](./RELEASE_NOTES_v14.1.1.md)

# Lattice AI v14.1.1 — Living Synthesis (2026-11-25)

## 주요 변경 사항

### 1. 저장소 전역 Python 포매팅(`ruff format`), CI 강제 및 `git blame` 위생 관리
- **전체 트리 포매팅 (`ruff format .`):** 전체 Python 코드베이스의 219개 파일을 전용 포매팅 커밋(`55ba57c381a433066fd94434c3a6250c4a1beb59`)으로 일괄 정렬해 단일 표준 스타일을 확립했습니다.
- **깔끔한 `git blame` 이력 (`.git-blame-ignore-revs`):** 저장소 루트에 `.git-blame-ignore-revs`를 추가하고 위 포매팅 커밋을 등록해 `git blame`이 실제 의미적 작성자를 유지하도록 했습니다.
- **CI 포매팅 게이트 강제 (`package.json`, `.github/workflows/ci.yml`):** `ci.yml`이 실행하는 `npm run lint:python`에 `ruff check .`, `npm run typecheck:python`과 함께 `ruff format --check .`를 추가해 향후 PR에서의 포맷 드리프트를 차단합니다.

### 2. 저장소 전역 `C901` 복잡도 예외 완전 제거 (`max-complexity = 20` 전역 강제)
- **라우터 분해 (`latticeai/api/models.py`, `latticeai/api/worker_compute.py`):** `create_models_router`와 `create_worker_compute_router`를 응집도 높은 모듈 수준 하위 등록 함수와 헬퍼로 분해해 API/OpenAPI 변경 없이 모든 Python 함수를 McCabe 복잡도 `20` 이하로 유지했습니다.
- **전역 `C901` 적용 (`pyproject.toml`):** `[tool.ruff.lint.per-file-ignores]`에서 `"latticeai/api/*.py"` 및 `"latticeai/app_factory.py"`의 `"C901"` 예외를 모두 삭제하여 저장소 어디에도 `C901` 예외가 남지 않도록 했습니다.
- **파일 길이 상한 준수 (`tests/unit/test_worker_compute_extract.py`):** `/worker/extract` 단위 테스트를 `tests/unit/test_worker_compute_extract.py`로 분리해 추적 중인 모든 파일이 1,000줄 상한(`check:max-file-lines`) 이내를 유지합니다.

### 3. 서버 i18n 래칫 미분류 모듈 `0`건 달성 (`scripts/check_server_i18n.mjs`)
- **명시적 모듈 분류:** `latticeai/api/` 내 미분류 상태였던 마지막 2개 모듈 — `worker_compute_render.py`(HTTP 라우트 없는 순수 Office/PDF 바이너리 빌더 및 Pydantic 스키마)와 `worker_resilience.py`(`lattice-host`에 기계 판독용 오류 코드를 반환하는 내부 루프백 `/worker/*` 타임아웃·멱등 미들웨어) — 를 감사한 뒤 `NON_ROUTER_HELPERS`에 명시 등록했습니다.
- **Fail-Closed 래칫 잠금:** 향후 `latticeai/api/` 아래에 추가되는 `.py` 모듈이 `LOCALIZED` 또는 `NON_ROUTER_HELPERS` 중 어느 쪽에도 등록되지 않으면 `scripts/check_server_i18n.mjs`가 즉시 실패(`exit 1`, `0 unclaimed`)하도록 강화했습니다.

### 4. Dockerfile 소유권 및 실행 권한 일관화
- **명시적 런타임 바이너리 권한 (`Dockerfile`):** 43번 줄(`COPY --from=host-builder --chown=lattice:lattice --chmod=0755 /build/rust/target/release/lattice-host /usr/local/bin/lattice-host`)에 소유권과 실행 권한을 명시해 런타임 스테이지의 모든 복사 항목이 일관된 권한 정책을 따르도록 정리했습니다.
