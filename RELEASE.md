# Lattice AI Release Guide

이 문서는 `npm`, `PyPI`, `VS Code`, `Cursor`, `Antigravity`, `Open VSX` 배포를
한 번에 처리하기 위한 체크리스트입니다.

> **v0.5.0부터 `.github/workflows/release.yml`은 build-only입니다.** v* 태그를
> push하면 단위 테스트와 빌드 산출물(`python -m build`, `twine check`,
> `npm pack`, `vsce package`)만 생성하고 **어떤 배포도 수행하지 않습니다**.
> PyPI / npm / VS Code Marketplace / Open VSX 배포는 아래 수동 절차로 로컬에서
> 직접 인증 후 진행합니다. GitHub Secrets에 배포 토큰을 저장하지 않습니다.

## v0.5.0 릴리스 노트 (2026-05-31)

MLX 샘플링 API 호환성 버그 수정 + 릴리스 워크플로 build-only 전환. 자세한
내용은 [`docs/CHANGELOG.md`](docs/CHANGELOG.md)의 `[0.5.0]` 항목 참고.

- **Fixed**: 로컬 MLX 추론에서 `generate_step() got an unexpected keyword
  argument 'temp'` 오류 수정 — `temp=` 대신 `sampler=make_sampler(temp=…)` 전달
  (mlx_lm ≥ 0.20 / mlx_vlm API 변경 대응, 8개 호출부).
- **Changed**: 릴리스 워크플로를 build-only로 전환 — publish job 4종과
  `if: secrets.*` 제거, 테스트·빌드까지만 수행.
- 빌드 산출물만 생성 — 어떤 배포도 수행하지 않음.

## v0.4.0 릴리스 노트 (2026-05-31)

Knowledge Graph v2 read/write cutover. 자세한 내용은
[`docs/CHANGELOG.md`](docs/CHANGELOG.md)의 `[0.4.0]` 항목 참고.

- KGStoreV2 read/write cutover 완료 (legacy ↔ v2 동등성 보장)
- Dual-write projection 도입 (legacy 타입/summary/metadata를 `attrs._kg`에 보존)
- 모든 그래프 read에 deterministic ordering(`… , id ASC`) 적용
- 삭제 미러링 완성 (clear_all / delete_conversation / 로컬 폴더 재인덱싱)
- Legacy/V2 equivalence test suite 추가, 단위 테스트 **181 pass**
- 빌드 산출물만 생성 — 어떤 배포도 수행하지 않음

> 참고: legacy `nodes`/`edges`는 여전히 durable write source이며, v2는 동일
> 트랜잭션에서 갱신되는 프로젝션입니다. `LATTICEAI_KG_READ_V2=0`으로 legacy
> read 경로로 즉시 롤백할 수 있습니다.

## TODO — 후속 작업 (v0.4.0 이후, 이번 릴리스 범위 밖)

아래 항목은 의도적으로 v0.4.0에 **포함하지 않았습니다**. 별도 작업/PR로 진행:

- **Dead code cleanup** — cutover로 main-path에서 미사용이 된 코드 정리.
- **`migrate_legacy_to_v2()` 제거** — backfill을 프로젝션이 대체해 main-path에서
  dead(현재 `kg_schema._cli`만 사용). 제거 또는 CLI 전용으로 강등.
- **`KGStoreV2.upsert_*` 정리** — 프로젝션은 raw SQL, read는 뷰를 쓰므로
  production 경로 미사용. 정리 시 `test_document_generation` 동반 조정 필요.
- **KG schema redesign / `NodeType` 재설계** — v2 `type` 칼럼이 현재 legacy
  자유문자열을 담는 타협 상태. 무손실 superset enum으로 재설계하면 typed/embedding
  의미론을 복원 가능.
- 잔여 저위험 항목: 혼합 `_kg` 상태 리프로젝션 가드(any→all), 프로젝션 실패 시
  edge 발산 모니터링.

## 0) 릴리스 전 체크

1. `python3 -m pytest tests/unit/ -v` — 단위 테스트 모두 통과 확인
2. `docs/CHANGELOG.md`의 최신 항목 작성 완료
3. CI(GitHub Actions) `ci.yml`이 main에서 green

## 1) 공통 준비

1. 버전 업데이트(세 곳 모두 동일하게 유지)
   - `package.json` (root)
   - `pyproject.toml`
   - `vscode-extension/package.json`
2. 루트에서 빌드/기본 검증
   - `npm run check:python`
   - `npm run build:python`

## 2) npm 배포

1. 로그인
   - `npm login`
2. 배포
   - `npm run publish:npm`

## 3) PyPI 배포

1. 업로드 도구 설치
   - `python3 -m pip install --upgrade build twine`
2. 빌드
   - `npm run build:python`
3. 업로드
   - `npm run publish:pypi`

참고:
- TestPyPI 먼저 쓰려면:
  - `python3 -m twine upload --skip-existing --repository testpypi dist/*.tar.gz dist/*.whl`

## 4) VS Code / Cursor / Antigravity 확장 배포

`vscode-extension` 디렉터리 기준:

1. 의존성 설치 및 빌드
   - `npm install`
   - `npm run build`
2. VSIX 생성
   - `npm run package:vsix`
3. VS Code Marketplace 배포
   - `npm run publish:vscode`
4. Open VSX 배포 (Cursor/일부 포크 호환)
   - `npm run publish:openvsx`
5. 로컬 설치 (VS Code/Cursor/Antigravity)
   - `npm run install:all`

토큰:
- VS Code Marketplace: `vsce login <publisher>`
- Open VSX: `ovsx create-namespace <publisher>` / `ovsx publish ... -p <token>`

## 5) Antigravity/Cursor 관련 메모

- `Cursor`, `Antigravity`는 VSIX 설치가 가능하므로 `install:all`로 로컬 검증 가능.
- 원격 “스토어 등록”은 해당 스토어 정책/토큰이 필요합니다.
- 스토어 API/토큰 준비 후에는 같은 VSIX를 재사용해 등록하면 됩니다.

