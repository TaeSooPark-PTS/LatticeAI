# Lattice AI Release Guide

이 문서는 `npm`, `PyPI`, `VS Code`, `Cursor`, `Antigravity`, `Open VSX` 배포를
한 번에 처리하기 위한 체크리스트입니다.

> **v0.5.0부터 `.github/workflows/release.yml`은 build-only입니다.** v* 태그를
> push하면 단위 테스트와 빌드 산출물(`python -m build`, `twine check`,
> `npm pack`, `vsce package`)만 생성하고 **어떤 배포도 수행하지 않습니다**.
> PyPI / npm / VS Code Marketplace / Open VSX 배포는 아래 수동 절차로 로컬에서
> 직접 인증 후 진행합니다. GitHub Secrets에 배포 토큰을 저장하지 않습니다.

## v1.4.0 릴리스 노트 (2026-05-31)

Server App Final Decomposition — 목표 줄 수 미달 없이 핵심 클러스터를 실제
router/service 계층으로 이동.
자세한 내용은 [`docs/CHANGELOG.md`](docs/CHANGELOG.md)의 `[1.4.0]` 항목 참고.

- **Changed**: `latticeai/server_app.py` 5,381 → 1,303줄. 2,000줄 이하와
  1,500줄 이하 목표를 모두 달성.
- **Added**: `latticeai/api/{chat,tools,computer_use,local_files,permissions,garden,setup,static_routes}.py`,
  `latticeai/services/{model_runtime,tool_dispatch,upload_service,app_context}.py`,
  `latticeai/api/deps.py`.
- **Changed**: chat/history/agent, model runtime/provider helpers, tools/local/
  CU/permissions/upload, garden/setup/static UI pages, MCP/KG glue를
  `server_app.py` 밖으로 이동.
- **Added**: v1.4 decomposition guard
  (`tests/unit/test_server_app_v14_decomposition.py`)로 line-count,
  independent import, version metadata를 검증.
- **Changed**: README / RELEASE / CHANGELOG / SECURITY / package scripts의
  current-release 문맥을 v1.4.0으로 정렬하고 README 내부 0.6.0 current 충돌 제거.
- **Validation**: route compatibility, streaming contract, model endpoint
  presence, MCP/KG presence, import/startup, tools/local/CU route snapshot,
  Python/VSIX/npm packaging을 검증.
- 테스트/빌드/패키징 산출물만 생성 — 패키지 스토어 publish는 수동 절차로만 진행.

## v1.3.0 릴리스 노트 (2026-05-31)

server_app.py 추가 분해(phase 3) — 안전망 우선 구축 후 model/MCP 라우터 추출.
자세한 내용은 [`docs/CHANGELOG.md`](docs/CHANGELOG.md)의 `[1.3.0]` 항목 참고.

- **Added**: route-compatibility 안전망(`tests/unit/test_route_compatibility.py`) —
  209개 public path + import/startup + streaming/model/MCP/KG contract를 동결.
  코드 이동 **전에** 구축해 누락/리네임/깨진 import를 즉시 검출.
- **Added**: `latticeai/api/models.py`(`create_models_router`) — `/models*`,
  `/engines*`, `/setup/set-api-key`. `latticeai/api/mcp.py`(`create_mcp_router`) —
  `/mcp/*`, `/skills/*`, `/plugins/directory*`, `/mcp/call`.
- **Changed**: server_app.py ~5,948 → ~5,382줄. API path/schema, `server:app`
  import path, CLI/UI/KG/Admin/Security/VS Code 호환 전부 유지(route snapshot로 검증).
- **Note**: chat/streaming, `/tools/*`·`/cu/*`·`/local/*`·`/upload`·`/permissions`,
  ~1,700줄 model/engine provider helper 블록은 다음 패스로 이월(안전망이 이미
  해당 이동을 de-risk). 2,000줄 목표는 아직 미달성.
- CI 하드닝(VSIX compile guard, Node.js 24, 버전 한정 validator) 유지.
- 테스트/빌드/패키징 산출물만 생성 — 어떤 배포도 수행하지 않음.

## v1.2.0 릴리스 노트 (2026-05-31)

server_app.py 모듈화(routers + service layer) + workspace/org guardrail 강화.
자세한 내용은 [`docs/CHANGELOG.md`](docs/CHANGELOG.md)의 `[1.2.0]` 항목 참고.

- **Changed**: `latticeai/server_app.py`를 ~6,585 → ~5,948줄로 축소. Workspace OS/
  Organization API와 health/engine summary endpoint를 전용 router(+service layer)로
  추출. `server_app`은 app assembly/lifespan/middleware/router include 중심.
  `server:app` import path·API path·schema 모두 유지.
- **Added**: `latticeai/api/workspace.py`(`create_workspace_router`),
  `latticeai/api/health.py`(`create_health_router`),
  `latticeai/services/{workspace_service,model_service,chat_service}.py`.
- **Changed**: workspace read/write가 `WorkspaceService` 게이트를 통과 — 비멤버는
  org read/write 불가, viewer는 write 불가, owner/admin만 멤버 관리. no-auth
  로컬 owner fallback 유지, named stranger bypass 차단.
- **Added**: graph/skills가 machine-global 공유 상태임을 `shared_global_areas`로 명시.
- **Added**: `test_server_app_modularization.py`, `test_workspace_service.py`.
- CI 하드닝(VSIX compile guard, Node.js 24, 버전 한정 validator, no `dist/*` glob) 유지.
- 테스트/빌드/패키징 산출물만 생성 — 어떤 배포도 수행하지 않음.

## v1.1.0 릴리스 노트 (2026-05-31)

Organization Workspace foundation + open-core Enterprise seam + CI/release
하드닝. 자세한 내용은 [`docs/CHANGELOG.md`](docs/CHANGELOG.md)의 `[1.1.0]` 항목 참고.

- **Added**: Personal / Organization workspace 분리 모델(`workspace_id`, `type`,
  `owner_user_id`, `members`, `roles`, `settings`)과 `/workspace/orgs/*`,
  `/workspace/registry`, `/workspace/activate`, `/workspace/editions` API.
- **Added**: owner/admin/member/viewer 역할과 read/write/manage_members/
  manage_workspace 권한. Snapshot/Memory/Agent/Workflow/Trace/Timeline에
  `workspace_id` 스코핑(`X-Workspace-Id` 헤더).
- **Added**: open-core Enterprise seam(`latticeai/core/enterprise.py`) —
  `Edition`/`EnterpriseCapability` enum + `CapabilityRegistry`. Community는 어떤
  Enterprise 기능도 활성화하지 않으며 Community 기능을 제한하지 않음.
  `docs/ENTERPRISE.md`, `docs/EDITION_STRATEGY.md` 참고.
- **Added**: `scripts/validate_release_artifacts.py` — 단일 버전 산출물 존재/버전
  일치/VSIX entrypoint 검증, `dist/*` 글롭 혼입 경고.
- **Changed**: `release.yml` Node.js 24 대응(`FORCE_JAVASCRIPT_ACTIONS_TO_NODE24`),
  `actions/checkout@v5`/`setup-node@v5`/`setup-python@v6`로 갱신. 산출물 업로드와
  `twine check`를 태그 버전으로만 한정 — **`dist/*` 글롭 업로드 금지**.
- **Changed**: 1.0.x Workspace OS state는 로드 시 비파괴 마이그레이션으로 v1.1
  모델로 승격(레거시 레코드는 Personal workspace로 매핑).
- 테스트/빌드/패키징 산출물만 생성 — 어떤 배포도 수행하지 않음.

> **릴리스 산출물 업로드 규칙**: PyPI/npm/VSIX 업로드 시 `dist/*` 글롭을 쓰지 말고
> 항상 해당 버전 파일명만 명시한다. CI는 `validate_release_artifacts.py`로 이를
> 강제한다.

## v1.0.1 릴리스 노트 (2026-05-31)

CI packaging 회귀 수정 patch. 자세한 내용은
[`docs/CHANGELOG.md`](docs/CHANGELOG.md)의 `[1.0.1]` 항목 참고.

- **Fixed**: Release (build-only) 워크플로의 `Build VSIX` 단계가
  `Extension entrypoint(s) missing: extension/out/extension.js`로 실패하던 문제
  수정. `vsce package` 전에 TypeScript 컴파일이 없었음(`vscode-extension/out/`은
  gitignore되어 clean CI checkout에 부재).
- **Added**: `vscode-extension/package.json`에 `vscode:prepublish` → `compile`
  (`tsc -p .`) 스크립트를 추가해 `vsce package`가 항상 entrypoint를 컴파일하도록
  하고 로컬/CI 빌드 경로를 일치시킴.
- **Changed**: `release.yml`이 packaging 전에 `npm run compile` 실행 +
  `out/extension.js` 존재를 검증.
- **Changed**: Python/npm/VS Code extension/FastAPI `/health` 버전을 `1.0.1`
  으로 정렬.
- 테스트/빌드/패키징 산출물만 생성 — 어떤 배포도 수행하지 않음.

## v1.0.0 릴리스 노트 (2026-05-31)

AI Workspace OS integration release. 자세한 내용은
[`docs/CHANGELOG.md`](docs/CHANGELOG.md)의 `[1.0.0]` 항목 참고.

- **Added**: `/workspace` command center and `/workspace/*` APIs for Graph,
  Snapshot, Memory, Agent, Workflow, Skills, and Timeline.
- **Added**: reentrant first-run onboarding, Graph RAG answer traces, indexing
  dashboard, snapshots, Time Machine, Knowledge Diff, Personal Memory,
  Multi-Agent Graph, Relationship Explorer, approval-gated Local Computer
  Memory, Skill Marketplace state, and Workflow Graph.
- **Added**: VS Code workflow commands for Refactor Selection, Generate Tests,
  Send To Lattice, and Ask About Current File while preserving Explain
  Selection.
- **Changed**: Python/npm/VS Code extension/FastAPI `/health` 버전을 `1.0.0`
  으로 정렬.
- 테스트/빌드/패키징 산출물만 생성 — 어떤 배포도 수행하지 않음.

## v0.6.0 릴리스 노트 (2026-05-31)

Runtime / registry / config extraction release. 자세한 내용은
[`docs/CHANGELOG.md`](docs/CHANGELOG.md)의 `[0.6.0]` 항목 참고.

- **Changed**: `server.py`를 historical `server:app` 호환 엔트리포인트로 축소하고
  FastAPI 앱 조립/라우트 wiring을 `latticeai.server_app`으로 이동.
- **Changed**: tool dispatch, governance, permission view, MCP description,
  prompt catalog를 `latticeai.core.tool_registry.ToolRegistry`로 통합.
- **Changed**: planner / executor / critic / memory updater prompts를
  `latticeai.core.agent_prompts`로 분리. `AgentRuntime`은 injected state-machine
  core로 유지.
- **Changed**: Python/npm/VS Code extension/FastAPI `/health` 버전을 `0.6.0`으로
  정렬.
- 테스트/빌드/패키징 산출물만 생성 — 어떤 배포도 수행하지 않음.

## v0.5.1 릴리스 노트 (2026-05-31)

KGStoreV2 정규화 스키마 + 마이그레이션 하드닝 + native API 정리. 자세한 내용은
[`docs/CHANGELOG.md`](docs/CHANGELOG.md)의 `[0.5.1]` 항목 참고.

- **Changed**: `attrs._kg` 패스스루 제거 — legacy 타입을 무손실 `NodeType`/
  `EdgeType` superset으로 정규화(`type`), 원본은 `legacy_type` 칼럼에 보존.
  summary/metadata 1급 칼럼화. 엣지 정체성 `(source,target,legacy_type)`.
- **Changed**: `_init_v2_schema` 마이그레이션을 단일 트랜잭션으로 원자화(중간 실패
  롤백, legacy 무손상). 프로젝션이 legacy 값을 verbatim 저장 → 뷰가 byte-faithful.
- **Removed**: production 미사용 native KGStoreV2 데이터 API(`upsert_*`/`get_node`/
  `search_*`)·`Node`/`Edge` 모델·관련 dead helper 제거. `KGStoreV2`는 schema/init/
  projection 지원 역할만 유지. 테스트의 직접 의존 제거.
- 단위 테스트 192 통과. 빌드 산출물만 생성 — 어떤 배포도 수행하지 않음.

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
- ✅ **마이그레이션 원자성** — `_init_v2_schema`의 DROP→CREATE→VIEWS→BACKFILL→
  version-stamp 전체를 단일 트랜잭션(`BEGIN` + `_exec_script`로 implicit-commit
  회피)으로 처리. 중간 실패 시 전부 롤백 → 이전 프로젝션·version 보존, 다음 기동에
  재시도. legacy `nodes`/`edges`는 마이그레이션이 절대 건드리지 않음(손상 불가).
- ✅ **뷰 byte-faithfulness** — 프로젝션이 legacy `title`/`summary`/`metadata_json`을
  **verbatim** 저장(truncation·`sort_keys` 재인코딩 제거). 절단/정렬은 `_upsert_*`이
  legacy 기록 시 1회 수행하고 동일 값을 프로젝션에 전달. NULL summary·비정렬 멀티키·
  초과 길이까지 뷰가 legacy와 byte-identical(`test_view_is_byte_faithful_to_legacy`).
  `summary` 칼럼은 nullable로 변경. (projection_version 3→4 → 자동 리빌드.)
- ✅ **dual-write 불변식 가드** — 모든 legacy write는 `_upsert_*`(유일한 2개
  writer)를 경유하고 모든 delete는 v2에 미러됨을 구조적으로 확인. 런타임 진단
  `_v2_sync_report()`(legacy↔v2 id-set 일치) 추가 + 불변식 테스트.

남은 항목:

- **`KGStoreV2.upsert_*` / read API 정리** — 프로젝션은 raw SQL, read는 뷰를
  쓰므로 production 경로 미사용. (단, `test_document_generation`이 native
  `upsert_node`/`get_node`를 사용하므로 정리 시 동반 조정 필요.)
- **dual-write 모니터링 자동화** — `_v2_sync_report()`는 현재 테스트/진단용.
  주기적/기동시 헬스체크로 노출하면 우회 write 회귀를 조기 감지 가능.

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
   - 직접 실행 시:
     `python3 -m twine upload dist/ltcai-1.4.0-py3-none-any.whl dist/ltcai-1.4.0.tar.gz`

참고:
- TestPyPI 먼저 쓰려면:
  - `python3 -m twine upload --skip-existing --repository testpypi dist/ltcai-1.4.0.tar.gz dist/ltcai-1.4.0-py3-none-any.whl`

## 4) VS Code / Cursor / Antigravity 확장 배포

`vscode-extension` 디렉터리 기준:

1. 의존성 설치 및 빌드
   - `npm install`
   - `npm run build`
2. VSIX 생성
   - `npm run package:vsix`
3. VS Code Marketplace 배포
   - `npm run publish:vscode`
   - 직접 실행 시:
     `npx vsce publish --packagePath dist/ltcai-1.4.0.vsix`
4. Open VSX 배포 (Cursor/일부 포크 호환)
   - `npm run publish:openvsx`
   - 직접 실행 시:
     `npx ovsx publish dist/ltcai-1.4.0.vsix`
5. 로컬 설치 (VS Code/Cursor/Antigravity)
   - `npm run install:all`

토큰:
- VS Code Marketplace: `vsce login <publisher>`
- Open VSX: `ovsx create-namespace <publisher>` / `ovsx publish ... -p <token>`

## 5) Antigravity/Cursor 관련 메모

- `Cursor`, `Antigravity`는 VSIX 설치가 가능하므로 `install:all`로 로컬 검증 가능.
- 원격 “스토어 등록”은 해당 스토어 정책/토큰이 필요합니다.
- 스토어 API/토큰 준비 후에는 같은 VSIX를 재사용해 등록하면 됩니다.
