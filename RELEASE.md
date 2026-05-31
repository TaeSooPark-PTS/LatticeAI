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

## TODO — 후속 작업 (이번 릴리스 범위 밖)

완료된 항목 (KGStoreV2 정규화 리팩터링):

- ✅ **`migrate_legacy_to_v2()` 제거** — dead code 제거. 리프로젝션은
  `knowledge_graph._backfill_v2_if_needed` 단일 경로로 통합. CLI `migrate`
  서브커맨드도 제거.
- ✅ **KG schema redesign / `NodeType` 재설계** — `attrs._kg` 패스스루 제거,
  legacy 자유문자열 타입을 무손실 `NodeType`/`EdgeType` superset으로 정규화
  (`type`), 원본은 `legacy_type` 칼럼에 보존. summary/metadata는 1급 칼럼으로
  승격. 엣지 정체성은 `(source,target,legacy_type)`로 보존.

남은 항목:

- **`KGStoreV2.upsert_*` / read API 정리** — 프로젝션은 raw SQL, read는 뷰를
  쓰므로 production 경로 미사용. (단, `test_document_generation`이 native
  `upsert_node`/`get_node`를 사용하므로 정리 시 동반 조정 필요.)
- **뷰 byte-faithfulness 한계(기존 제약)** — 프로젝션은 legacy `title`/`summary`를
  `[:240]`/`[:1000]`로 자르고 `metadata_json`을 `sort_keys`로 재인코딩한다.
  `_upsert_*` 경로로 쓰인 행은 동일하지만, 외부에서 직접 삽입된 초과 길이/비정렬
  키 행은 `metadata_json LIKE` 검색에서 legacy와 미세하게 달라질 수 있음. (리팩터
  이전부터 동일하게 존재하던 제약 — 이번 변경이 악화시키지 않음. faithful 프로젝션
  + equivalence 테스트 케이스 추가로 별도 개선 가능.)
- **마이그레이션 원자성** — `_init_v2_schema`의 DROP/init/backfill/version-set이
  분리된 트랜잭션. 단일 트랜잭션으로 묶으면 중간 크래시 시 torn 상태 가능성 제거.
- **dual-write 불변식 가드** — 모든 legacy write가 `_upsert_*`를 경유한다는 가정.
  직접 INSERT 경로가 생기면 v2가 조용히 발산할 수 있음(현재 모니터링 없음).

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

