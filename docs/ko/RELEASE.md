[![English](https://img.shields.io/badge/English-Switch-475569?style=flat-square)](../../RELEASE.md) [![한국어](https://img.shields.io/badge/한국어-현재_언어-2563eb?style=flat-square)](./RELEASE.md)

# Lattice AI 릴리스 가이드

이 문서는 `npm`, `PyPI`, `VS Code Marketplace`, `Cursor`, `Antigravity`, `Open VSX`, macOS Apple Silicon DMG 배포를 한 번에 관리하기 위한 체크리스트이자 정본 릴리스 원장입니다.

> **CI 게이트 기반 자동 Release → CD (`.github/workflows/release.yml` → `.github/workflows/publish.yml`)**:
> `.github/workflows/release.yml`은 `main` 브랜치에서 `CI`(`ci.yml`)가 `success`로 완료되면 자동으로 트리거됩니다(수동 태그 push 및 `workflow_dispatch`도 지원). 정확한 커밋 SHA에 대한 그린 `CI` 실행을 강제 검증하고, `scripts/bump_version.py <VERSION> --check`, `scripts/check_current_release_docs.mjs`, `scripts/check_doc_languages.mjs` 통과를 확인한 뒤 미릴리스 `package.json` 버전을 자동 태깅합니다(이미 태그된 일반 커밋이나 `[skip release]`/`[no release]` 커밋은 안전하게 건너뜀). 이어서 정확한 버전의 산출물을 빌드·서명해 GitHub Release를 게시하고 `.github/workflows/publish.yml`(CD)을 연쇄 실행해 npm, PyPI, VS Code Marketplace, Open VSX에 배포합니다. CD는 이미 게시된 버전을 `already-published`로 안전하게 건너뛰므로 재실행해도 멱등합니다.

> **정확한 버전 파일명만 사용 (와일드카드 업로드 금지)**:
> 수동 폴백 배포 시에는 반드시 정확한 버전 파일명을 지정합니다.
> - PyPI: `twine upload dist/ltcai-X.Y.Z-py3-none-any.whl dist/ltcai-X.Y.Z.tar.gz`
> - npm: `npm publish ltcai-X.Y.Z.tgz`
> - VS Code Marketplace: `vsce publish --packagePath dist/ltcai-X.Y.Z.vsix`
> - Open VSX: `ovsx publish dist/ltcai-X.Y.Z.vsix`
> - macOS DMG: `src-tauri/target/release/bundle/dmg/Lattice AI_X.Y.Z_aarch64.dmg`

> **릴리스 증거물 보존 정책 (`output/release/`)**: 버전별 스크린샷/영상 증거는 최신 3개 버전만 보관합니다. `npm run release:evidence`가 캡처 후 자동으로 `scripts/prune_release_evidence.mjs`를 실행해 오래된 버전을 정리하며(`LTCAI_RELEASE_EVIDENCE_KEEP`으로 조정), 과거 증거는 언제든 해당 태그를 체크아웃해 재생성할 수 있습니다.

## v13.4.2 — Living Synthesis (2026-11-24)

Apple Silicon 환경에서 계층형 RAM/SSD KV 캐시를 지원하는 **oMLX (`jundot/omlx`)** 엔진을 통합하고(`--memory-guard balanced`, `--hot-cache-max-size`, `--paged-ssd-cache-dir`, `--max-concurrent-requests 2`, `--initial-cache-blocks 128`), **`mlx-community/Qwen3.8-27B-4bit`**를 32GB+ Mac의 추천 및 다운로드 가능 모델로 등록했습니다(`recommended_ram_gb=32.0`).

릴리스 노트는 [docs/ko/releases/RELEASE_NOTES_v13.4.2.md](./releases/RELEASE_NOTES_v13.4.2.md).

빌드 산출물은 `dist/ltcai-13.4.2-py3-none-any.whl`, `dist/ltcai-13.4.2.tar.gz`, `ltcai-13.4.2.tgz`, `dist/ltcai-13.4.2.vsix`, `src-tauri/target/release/bundle/dmg/Lattice AI_13.4.2_aarch64.dmg` 입니다.

## v13.4.1 — Living Synthesis (2026-11-23)

전 스택 Dependabot PR `#58`, `#60`, `#61`의 메이저/브레이킹 의존성 업그레이드 호환성 및 컴파일 실패를 해결한 유지보수 릴리스:
- **프론트엔드 (`npm` / TypeScript 7.0.2 / Vitest 5.1.1):** `openapi-typescript@7.13.0` 피어 의존성 충돌을 `overrides`로 해결하고, TS 7 대응 `tsconfig.json`(`baseUrl` 제거, 상대 `paths`) 및 `frontend/src/vite-env.d.ts`를 추가했으며, 111개 전체 테스트 파일에서 100% 커버리지를 유지했습니다.
- **Rust 워크스페이스 (`rust/` 및 `src-tauri/`):** `sha2` 0.11 / `digest` 0.11(`hex_lower` / `sha256_text`), `rusqlite` 0.43(`i64` 바인딩), `fancy-regex` 0.20(`Captures<'_, str>`), `axum` 0.8(네이티브 비동기 `FromRequestParts`), `scrypt` 0.12(3인자 `Params::new`), `zip` 9(`ZipFile<'_, R>`, `Result<Cow<'_, str>, ZipError>`, `SimpleFileOptions`), `reqwest` 0.13, `tauri` 2.11.6 호환성을 반영했습니다.

릴리스 노트는 [docs/ko/releases/RELEASE_NOTES_v13.4.1.md](./releases/RELEASE_NOTES_v13.4.1.md).

빌드 산출물은 `dist/ltcai-13.4.1-py3-none-any.whl`, `dist/ltcai-13.4.1.tar.gz`, `ltcai-13.4.1.tgz`, `dist/ltcai-13.4.1.vsix`, `src-tauri/target/release/bundle/dmg/Lattice AI_13.4.1_aarch64.dmg` 입니다.

## v13.4.0 — Living Synthesis (2026-11-20)

모델별 코드 분기 없이 동작하는 **범용 적응형 에이전트 하네스(`rust/lattice-agent/src/kernel/{probe.rs,agentloop/,transcript/,parse/}`)**, **모듈화된 네이티브 웹 하네스 및 컴퓨터 유즈 브리지 정합성(`rust/lattice-agent/src/tools/web/`, `latticeai/tools/{web.py,computer.py}`)**, **로컬 `Qwen2.5-0.5B-Instruct-Q4_K_M` 대상 25개 태스크 실측 통과율을 `28.0%`(Raw) / `24.0%`(Standard) → `84.0%`(`+56.0 pp` / 상대 `+200.0%`)로 끌어올린 실증 벤치마크**, **100% 영문 ↔ 한국어 양방향 문서 대등성(`npm run docs:check-languages`)**을 담은 범용 적응형 실행 릴리스.

- **범용 적응형 에이전트 하네스:** 3턴 마이크로 프로브(`probe.rs`), 관찰 우선 도구 순위화(`ranking.rs`) 및 한/영 도메인 분류(`domain.rs`), 결정론적 UI 바운딩 박스 중앙 좌표 해석(`ui_resolve.rs` → 컴퓨터 유즈 `100.0%`), JSON 구문 복구·ChatML 프리필(`repair.rs`, `action_salvage_text.rs`), 활성/폐기 증거 분리 합성(`rag_grounding.rs`, `summaries.rs` → RAG 그라운딩 `100.0%`)을 도입했다.
- **25개 태스크 실증 벤치마크:** `scripts/benchmark_adaptive_harness.py`와 `rust/lattice-host/examples/harness_bench.rs`로 로컬 `llama-server` 실측을 수행해 **도구 호출 80.0% (`+60.0 pp`)**, **웹 탐색 80.0% (`+80.0 pp`)**, **컴퓨터 유즈 100.0% (`+100.0 pp`)**, **답변 품질 60.0% (Standard 대비 `+40.0 pp`)**, **RAG 그라운딩 100.0% (`+40.0 pp` / Standard 대비 `+80.0 pp`)**, **평균 지연 시간 -63.3%**를 달성했다.
- **100% 한/영 문서 대등성:** 루트, 크레이트, GitHub 가이드, 벤치마크 보고서 전반에 한국어 미러(`docs/ko/**`)와 양방향 언어 전환 배지를 완성했다.

릴리스 노트는 [docs/ko/releases/RELEASE_NOTES_v13.4.0.md](./releases/RELEASE_NOTES_v13.4.0.md), 벤치마크는 [docs/ko/ADAPTIVE_HARNESS_BENCHMARK.md](./ADAPTIVE_HARNESS_BENCHMARK.md).

빌드 산출물은 `dist/ltcai-13.4.0-py3-none-any.whl`, `dist/ltcai-13.4.0.tar.gz`, `ltcai-13.4.0.tgz`, `dist/ltcai-13.4.0.vsix`, `src-tauri/target/release/bundle/dmg/Lattice AI_13.4.0_aarch64.dmg` 입니다.

## v13.3.1 — Living Synthesis (2026-11-19)

**시맨틱 임베딩 계약용 2단계 비동기 증분 청크 동기화(`plan_incremental_sync` + 비동기 `/worker/embed` + `commit_incremental_sync`, `watch_deliver.rs`)**, **`AnswerProof.tsx` 주장 검증 텔레메트리(`claim_verification`) 및 `BrainIntelligencePanel.tsx` 개체 통합·시간축 요약 카드(한/영 i18n · Vitest 100%)**, **전 스택 주간 Dependabot(`.github/dependabot.yml`) 자동 감시**를 통합한 완성도 업그레이드 릴리스.

- **2단계 비동기 시맨틱 증분 동기화:** `incremental_sync.rs`를 `plan_incremental_sync`와 `commit_incremental_sync`로 분리하고 `watch_deliver.rs`를 추출해, 로컬 해시 및 시맨틱 임베딩 계약 모두에서 변경된 청크만 비동기 `/worker/embed`로 재임베딩하고 미변경 청크 벡터를 재사용하도록 완성했다(`vault_watch_bridge.rs` 종단간 검증 포함).
- **주장 검증·개체 통합·시간축 UI 연결:** `AnswerProof.tsx`에 주장 검증률·인용 정확도·수치/극성 불일치·대체 출처 인용·기권 권고 배지를 추가하고, `BrainIntelligencePanel.tsx`에 `entity_resolution`(스캔·클러스터·후보/`SAME_AS` 연결)과 `temporal_timeline`(활성·이력·미도달 사실·상충 경고) 요약 카드를 한/영 완전 로컬라이제이션 및 Vitest 100% 커버리지로 연결했다.
- **공급망 자동 감시:** `.github/dependabot.yml`을 추가해 Python(`pip`), Node(`npm`, `vscode-extension`), Rust(`rust`, `src-tauri`), GitHub Actions 의존성 권고를 매주 자동 점검한다.

릴리스 노트는 [docs/releases/RELEASE_NOTES_v13.3.1.md](../releases/RELEASE_NOTES_v13.3.1.md).

빌드 산출물은 `dist/ltcai-13.3.1-py3-none-any.whl`, `dist/ltcai-13.3.1.tar.gz`, `ltcai-13.3.1.tgz`, `dist/ltcai-13.3.1.vsix`, `src-tauri/target/release/bundle/dmg/Lattice AI_13.3.1_aarch64.dmg` 입니다.

## v13.3.0 — Living Synthesis (2026-11-19)

**v13.2.0 핵심 엔진 프로덕션 전면 연결(`post_idempotent_compute`, `verify_grounding_and_abstention`, `entity_resolution`·`temporal`, `DebouncedWatchEngine`·`sync_document_incremental`, `DurableWorkflowStore`)**, **`worker_resilience.py` 동시성·취소·스트리밍 타임아웃 및 `ingest.rs` 임시 파일 충돌/오버레이 오류 하드닝**, **Rust 비테스트 `.unwrap()` 0건 달성·중복 `unsafe libc` 시간 블록 7개 단일화(`clock.rs`)·전 스택 800줄 초과 프로덕션 파일 0건 달성(Vitest 100% 유지)**을 통합한 프로덕션 완성·하드닝 릴리스.

- **핵심 브레인·워크플로 엔진 라이브 경로 연결:** `WorkerSeamClient::post_idempotent_compute`(`/worker/{parse,extract,embed,render/*}` 및 `query_embed.rs` 단일 시도 코얼레싱), `assess_answer_grounding` 내 `claim_verification` 검증 텔레메트리(`helpers.rs`), `BrainIntelligenceService::consolidate` 내 `entity_resolution`·`temporal_timeline`(`consistency.rs`), 비블로킹 `DebouncedWatchEngine` + `sync_document_incremental`(`watch_bridge.rs`), `DurableWorkflowStore` 원자적 체크포인트(`executor.rs`)를 실트래픽 경로에 연결했다.
- **워커 복원력·인제스트 안전성 하드닝:** `worker_resilience.py` 코얼레싱 대기자 `504`/`503` 구조화 오류 응답, 응답 바디 스트리밍 타임아웃 포함, `CancelledError` 발생 시 `_inflight` 즉시 해제를 적용하고 회귀 테스트를 추가했으며, `local_files_api/ingest.rs`의 원자적 카운터 임시 파일명과 `structure_overlay_warning` 오류 노출을 도입했다.
- **안전성·모듈화 최종 정리:** `rust/lattice-platform/src/`의 잔여 비테스트 `.unwrap()` 9건을 전량 제거(워크스페이스 비테스트 `.unwrap()` 0건)하고, 중복된 `unsafe libc` 로컬 시간 블록 7개를 `rust/lattice-core/src/graph_write/clock.rs` 단일 안전 API로 통합했으며(`lattice-retrieval`의 미사용 `libc` 의존성 제거), `provider.rs`·`intents.rs`·`helpers.rs`·`extraction.py`를 분할해 전 스택 수기 프로덕션 파일 800줄 초과 0건을 달성했다.

릴리스 노트는 [docs/releases/RELEASE_NOTES_v13.3.0.md](../releases/RELEASE_NOTES_v13.3.0.md).

빌드 산출물은 `dist/ltcai-13.3.0-py3-none-any.whl`, `dist/ltcai-13.3.0.tar.gz`, `ltcai-13.3.0.tgz`, `dist/ltcai-13.3.0.vsix`, `src-tauri/target/release/bundle/dmg/Lattice AI_13.3.0_aarch64.dmg` 입니다.

## v13.2.1 — Living Synthesis (2026-11-19)

**전 스택 모듈화 리팩터링(`lattice-auth`, `lattice-core`, `lattice-ingest`, `lattice-retrieval`, `lattice-agent`, `lattice-platform`, `lattice-host`, `model_capability_registry.py`, `worker_compute.py`, `brainData.ts`)**, **Rust·Python·Frontend 미사용 데드 코드 및 은퇴 레이어 정리(Vitest 100% 유지)**, **Ubuntu `t64` 시각 회귀 CI 미러 페일오버 하드닝**을 통합한 유지보수·아키텍처 정돈 릴리스.

- **전 크레이트 Rust 모듈 분할:** `lattice-agent`(`guided/suggestions.rs`, `proposals_store.rs`, `action_salvage.rs`, `catalog_args.rs`, `tests_governance.rs`), `lattice-platform`(`run_handlers.rs`, `probe_sysinfo.rs`, `handlers_orgs.rs`, `handlers_snapshots.rs`, `share_bundle.rs`, `network_boundary_stores.rs`, `executor_steps.rs`, `mcp_streamable.rs`), `lattice-host`(`gateway/tests.rs`, `gateway_source_contracts.rs`) 및 `lattice-auth`·`lattice-core`·`lattice-ingest`·`lattice-retrieval`의 대형 파일을 응집도 높은 서브모듈로 분리했다.
- **Python·Frontend 모듈화 및 데드 코드 제거:** `model_capability_registry.py`(`model_capability_types.py`, `model_capability_catalog.py`), `worker_compute.py`(`worker_compute_render.py`), `brainData.ts`(`brainDataReaders.ts`, `brainDataReadiness.ts`)를 분할하고, 미참조 빌더/헬퍼 및 구형 오비탈 Brain 레이어(`BrainGraphLayer`, `BrainMemoryLayer`, `BrainRelationshipLayer`, `graphLayout` 등)를 정리하면서 Vitest 100% 커버리지를 유지했다.
- **시각 회귀 CI 복원력 강화:** Ubuntu `t64` 패키지 탐색을 단일 패스로 묶고 `15s` 미러 타임아웃 및 `mirrors.ubuntu.com` 자동 페일오버를 적용했다.

릴리스 노트는 [docs/releases/RELEASE_NOTES_v13.2.1.md](../releases/RELEASE_NOTES_v13.2.1.md).

빌드 산출물은 `dist/ltcai-13.2.1-py3-none-any.whl`, `dist/ltcai-13.2.1.tar.gz`, `ltcai-13.2.1.tgz`, `dist/ltcai-13.2.1.vsix`, `src-tauri/target/release/bundle/dmg/Lattice AI_13.2.1_aarch64.dmg` 입니다.

## v13.2.0 — Living Synthesis (2026-11-19)

**이중 시간축(Bi-temporal) 지식 그래프·비파괴 한/영 개체 통합(`temporal.rs`, `entity_resolution.rs`)**, **주장 단위 근거 검증·기권(`grounding.rs`) 및 검색/생성 분리형 모델 무관 RAG 평가(`rag_eval_grounded.rs`)**, **OS 이벤트 디바운스 감시·청크 단위 증분 재임베딩·표/페이지/OCR 구조 보존 수집(`incremental_watch.rs`, `incremental_sync.rs`, `document_structure.rs`)**, **원자적 체크포인트·멱등 원장·승인 재시작 복구·롤백을 갖춘 내구성 DAG 에이전트 워크플로 엔진(`kernel/workflow.rs`)**, **워커 멱등 재시도·타임아웃 격리(`worker.rs`, `worker_resilience.py`)·클라우드 비밀 파일 차단(`boundary.rs`)·정직한 그래프 상태 프로브(`onedoor.rs`, `audit.rs`) 및 Section 16 종단간 검증 스위트(`roadmap_section16_suite.rs`)**를 통합한 로드맵 완성 릴리스.

- **이중 시간축 지식 진화·개체 통합 & 근거 기반 RAG:** `temporal.rs`와 `entity_resolution.rs`가 유효 시간(`valid_from`..`valid_until`)·트랜잭션 시간 시점 조회와 비파괴 `SAME_AS` 한/영 별칭 병합·원복을 제공하고, `grounding.rs`·`rag_eval_grounded.rs`가 주장 단위 수치/극성/대체 지식 누수 검증, 근거 부족 시 자동 기권, 로컬/클라우드 모델 교체 불변성을 보장한다.
- **이벤트 기반 증분 동기화·멀티모달 구조 수집 & 내구성 DAG 워크플로:** `incremental_watch.rs`·`incremental_sync.rs`·`document_structure.rs`가 디바운스 파일 감시, 변경 청크만 재임베딩하는 증분 동기화, 표(`table_structured`)·페이지·캡션·OCR 보존 수집을 수행하며, `kernel/workflow.rs`가 단계별 체크포인트, SHA-256 멱등 원장, 상수 시간 승인 토큰 재시작 복구, 실패 시 파일 스냅샷 롤백, `USED_BY_AGENT`/`GENERATED_BY` 계보 기록을 제공한다.
- **워커 복원력·클라우드 비밀 경계·정직한 텔레메트리 & Section 16 검증 스위트:** `WorkerSeamClient::post_idempotent_json`·`worker_resilience.py` 멱등 재시도/타임아웃 격리, `.env`·개인키 등 비밀 파일 클라우드 유출 차단(`boundary.rs`), `empty`/`ok`/`unavailable` 그래프 상태 분리(`onedoor.rs`, `audit.rs`), 그리고 `roadmap_section16_suite.rs`(16.1 분리형 RAG, 16.2 1월→3월→5월 시간 진화, 16.3 클라우드 경계, 16.4 장애·인덱싱 중단·승인 재시작 복구) 전수 검증을 완료했다.

릴리스 노트는 [docs/releases/RELEASE_NOTES_v13.2.0.md](../releases/RELEASE_NOTES_v13.2.0.md).

빌드 산출물은 `dist/ltcai-13.2.0-py3-none-any.whl`, `dist/ltcai-13.2.0.tar.gz`, `ltcai-13.2.0.tgz`, `dist/ltcai-13.2.0.vsix`, `src-tauri/target/release/bundle/dmg/Lattice AI_13.2.0_aarch64.dmg` 입니다.

## v13.1.0 — Living Synthesis (2026-11-18)

**지식 그래프 인터랙티브 인스펙터·2-Hop 이웃 추론 합성(`neighborhood_synthesis.rs`, `GraphNodeInspector.tsx`, `MultiHopSynthesisView.tsx`) 및 원클릭 상충 기억 정리(`ContradictionInspector.tsx`)**, **채팅 답변 원클릭 Brain 기억 승격(`Save to Brain`)·인라인 변경 제안 비교/승인(`InlineStagedProposals.tsx`)·실시간 에이전트 도구 표시 및 출처 원문 그래프 점프(`AnswerProof.tsx`)**, **임베딩 백로그 진행률 바·즉시 인덱싱(`POST /api/index/drain`)·`stale_embedder` 보존(`health.rs`)·모델 다운로드 바이트 진행률 및 협력적 취소(`InstallScreen.tsx`, `ModelsPanel.tsx`)·`⌘K` 노드 포커스·반응형 작업공간 캐시 무효화·한/영 무누수 로컬라이제이션(Vitest 100%)**을 통합한 사용자 체감 대형 업그레이드 릴리스.

- **인터랙티브 지식 그래프 & 상충 정리:** `/api/graph/node`(`include_reasoning=true`)가 단일 스코프 SQLite 조회로 1-hop/2-hop 추론 체인·매개 개념·상충 엣지를 합성하며, `GraphNodeInspector.tsx`·`DigitalBrainExplorer.tsx`·`ContradictionInspector.tsx`가 이웃 노드 피벗, 상충 필터, `/`·`Esc` 단축키, 상충 제안 생성 및 원클릭 해결(`keep_old`·`replace`·`keep_both_temporal`)을 제공한다.
- **실행형 채팅·에이전트 워크스페이스 UX:** 답변 하단 **Brain에 기억하기** 원클릭 인제스트, 에이전트 변경 제안 직후 인라인 diff 확인 및 승인/거절, 실행 중 활성 도구명 표시, 출처 원문 모달 내 그래프 이동 및 로컬 원본 이미지 동의 미리보기를 연결했다.
- **일상 사용성·다운로드 취소 & 한/영 무결성:** `VectorFreshnessNotice` 진행률 바 및 **지금 인덱싱** 드레인 버튼, 온보딩·라이브러리 모델 다운로드 `MB/GB`·파일명 표시와 협력적 다운로드 취소(`AbortController` + `unloadModel`), 커맨드 팔레트 지식 노드 즉시 포커스 및 언어 전환, 작업공간 전환 시 즉시 쿼리 무효화, 한/영 오류 메시지 전수 로컬라이제이션을 달성했다.

릴리스 노트는 [docs/releases/RELEASE_NOTES_v13.1.0.md](../releases/RELEASE_NOTES_v13.1.0.md).

빌드 산출물은 `dist/ltcai-13.1.0-py3-none-any.whl`, `dist/ltcai-13.1.0.tar.gz`, `ltcai-13.1.0.tgz`, `dist/ltcai-13.1.0.vsix`, `src-tauri/target/release/bundle/dmg/Lattice AI_13.1.0_aarch64.dmg` 입니다.

## v13.0.0 — Sovereign Synthesis (2026-11-18)

**크로스 도메인 아키텍처 분리 강제(`lattice_core::redact`, `lattice_agent::seam`, `domain_boundaries.rs`) 및 통합 Rust 서버 i18n(`lattice-auth`→`lattice-core::messages`)**, **다단계(2-Hop) 지식 그래프 추론 경로·매개 개념·상충 감지 합성(`reasoning_paths.rs`, `MultiHopSynthesisView.tsx`)**, **단일 트랜잭션 리뷰 스토어·폴더 재구성 원클릭 되돌리기(`reversible_reorganization.rs`, `ReviewCard.tsx`) 및 대화 문서 세션 영속화(`DocumentSessions::with_data_dir`)**, **프론트엔드 API 모듈 분할(`streaming.ts`, `transfers.ts`, Vitest 100%) 및 Tauri 백엔드 모듈화**를 통합한 메이저 릴리스.

- **크로스 도메인 분리 & 정적 경계 검증:** `lattice_core::redact`로 비밀 마스킹을 일원화하고 `lattice_agent::seam::worker`를 분리해 `kernel/`·`tools/`가 상위 HTTP `surface/`를 참조하지 않도록 강제했으며, `modelops`→`adminops` 측면 결합 제거와 `domain_boundaries.rs` 정적 레이어링 테스트를 구축했다.
- **다단계(2-Hop) GraphRAG 추론 합성:** `bm25/reasoning_paths.rs`와 `fusion.rs`가 `Seed → Bridge → Target` 2-hop 추론 체인, 매개 개념(`bridge_concepts`), 상충·대체 관계(`CONTRADICTS`·`SUPERSEDES`)를 산출하고 Brain 하이브리드 검색(`MultiHopSynthesisView.tsx`)에서 즉시 시각화한다.
- **원자적 거버넌스·원클릭 폴더 되돌리기·세션 영속화 & 모듈화:** `WorkspaceOsStore` 단일 트랜잭션 리뷰+타임라인 기록, `folder_reorganization` 승인 시 자동 역방향 원복 제안 생성 및 원클릭 되돌리기 UI, `chat_document_sessions.json` 재시작 복원, `lattice-auth` 메시지 카탈로그 단일화(`check_server_i18n.mjs` 146개 Rust 메시지 검증), `client.ts`·`src-tauri/src/backend.rs` 분할을 완료했다.

릴리스 노트는 [docs/releases/RELEASE_NOTES_v13.0.0.md](../releases/RELEASE_NOTES_v13.0.0.md).

빌드 산출물은 `dist/ltcai-13.0.0-py3-none-any.whl`, `dist/ltcai-13.0.0.tar.gz`, `ltcai-13.0.0.tgz`, `dist/ltcai-13.0.0.vsix`, `src-tauri/target/release/bundle/dmg/Lattice AI_13.0.0_aarch64.dmg` 입니다.

## v12.10.0 — Grounded Self (2026-11-17)

**결정론적 에이전트 트랜스크립트 근거 검증(`grounding.rs`) 및 답변 인용 증빙(`AnswerProof.tsx`)**, **Brain 자기 모델 패널(`SelfModelPanel.tsx`) 및 지식 그래프 `PART_OF` 엣지(`edges`·`edges_v2`) 연쇄 삭제 보장**, **협력적 모델 다운로드 취소(`499`) 및 다운로드 직후 시맨틱 임베딩 자동 전환·리인덱싱 드레인**, **`http_reply.rs` 도메인 간 결합 제거·서버 i18n 6/6 라우터 잠금·병렬 `cargo nextest`·`release.yml`→`publish.yml` CD 체이닝**을 통합한 릴리스.

- **트랜스크립트 근거 검증 & 인용 증빙:** `rust/lattice-agent/src/kernel/transcript/grounding.rs`가 미열람 파일 기반 추측 응답과 환각 `path:line`·심볼 인용을 모델별 분기 없이 차단하고, `AnswerProof.tsx`와 브라우저 확장(`popup.js`)이 인용 근거 배지와 서버 언어 동기화를 제공한다.
- **자기 모델(Self-Model) 패널 & `PART_OF` 연쇄 삭제:** `SelfModelPanel.tsx`(Vitest 100%)와 `self_model_write.rs`로 선호·습관·결정·특성·관계 관리 및 제안 즉시 승인/거절을 연결하고, `graph_write_schema.rs`·`native_hnsw.rs`·`vector_alignment.rs`·`cloud_lane.rs`로 v1/v2 엣지 연쇄 삭제와 정렬된 벡터 검색·클라우드 경계 E2E를 검증했다.
- **협력적 다운로드 취소, 자동 시맨틱 전환 & 병렬 Rust 테스트/체인 CD:** `download.py`·`embedding_recommendation.py`·`index_api/engine.rs`에서 다운로드 취소(`499`)와 임베딩 모델 설치 직후 자동 전환·배치 리인덱싱을 지원하며, `http_reply.rs` 분리, Python 워커 라우터 6개 전수 i18n 잠금, `scripts/run_rust_tests.mjs` 병렬 `cargo nextest`, `.github/workflows/{release,publish}.yml` 자동 CD 체이닝을 완료했다.

릴리스 노트는 [docs/releases/RELEASE_NOTES_v12.10.0.md](../releases/RELEASE_NOTES_v12.10.0.md).

빌드 산출물은 `dist/ltcai-12.10.0-py3-none-any.whl`, `dist/ltcai-12.10.0.tar.gz`, `ltcai-12.10.0.tgz`, `dist/ltcai-12.10.0.vsix`, `src-tauri/target/release/bundle/dmg/Lattice AI_12.10.0_aarch64.dmg` 입니다.

## v12.9.0 — Semantic Guard (2026-11-17)

**시맨틱 쿼리 임베딩 계약 정렬(`query_embed.rs`) 및 순수 Rust 네이티브 HNSW(`NativeHnswIndex`)·스코프 Python HNSW 사이드카**, **네트워크 경계·클라우드 컨텍스트 fail-closed 격리(`network_boundary_guard.rs`, `lattice-chat/src/boundary.rs`)**, **데스크톱(Tauri)·CLI·호스트 슈퍼바이저·멀티스테이지 Docker 가드레일**, **실시간 Workspace OS SSE(`/api/realtime/stream`) 및 `useAppBootstrap` 분리**, **`.latticebrain` v2 아카이브 KDF 강화 및 CI 게이트 확장**을 통합한 릴리스.

- **시맨틱 쿼리 정렬 & 네이티브/사이드카 HNSW:** `rust/lattice-retrieval/src/{query_embed.rs,native_hnsw.rs,vector_hnsw.rs}`와 `latticeai/core/vector_index/{hnsw.py,sidecar.py}`에서 `/api/search/hybrid`·`/memory/recall`·`/chat` 쿼리 임베딩을 활성 `EmbeddingIndexContract`에 정렬하고 순수 Rust HNSW 및 `(workspace_id, model_id, dim)` 스코프 사이드카 영속화를 제공한다.
- **네트워크 경계 격리 & 데스크톱/CLI/Docker 가드레일:** `network_boundary_guard.rs`와 `lattice-chat/src/{boundary.rs,cloud/context.rs}`가 `local_only`/`cloud_allowed` 동의 및 민감도 필터를 서버 측에서 강제하며, `src-tauri/src/backend.rs`·`bin/ltcai.js`·`rust/lattice-host`·`Dockerfile`의 프로세스 수명주기 및 컨테이너 가드레일을 보강했다.
- **실시간 SSE 스트림, v2 KDF & CI 게이트:** `workspaceos/realtime.rs`와 `useRealtimeStream.ts`(Vitest 100%)로 실시간 이벤트 스트리밍을 연결하고 `useAppBootstrap.ts`를 분리했으며, `portability/{crypto.rs,kdf.rs}` v2 KDF 강화(v1 복호화 하위 호환) 및 `.github/workflows/{ci,dependency-audit,e2e-sidecar}.yml` 게이트를 강화했다.

릴리스 노트는 [docs/releases/RELEASE_NOTES_v12.9.0.md](../releases/RELEASE_NOTES_v12.9.0.md).

빌드 산출물은 `dist/ltcai-12.9.0-py3-none-any.whl`, `dist/ltcai-12.9.0.tar.gz`, `ltcai-12.9.0.tgz`, `dist/ltcai-12.9.0.vsix`, `src-tauri/target/release/bundle/dmg/Lattice AI_12.9.0_aarch64.dmg` 입니다.

## v12.8.0 — Trusted Brain (2026-11-16)

**임베딩 계약·지식 라이프사이클 일원화**, **SQLite `LIMIT` 이전 워크스페이스 스코핑·벡터 차원 정렬·바운드 사이드카 캐시**, **사용자·워크스페이스·테넌트 fail-closed 게이트웨이 격리**, **암호화 `.latticebrain` 아카이브 및 정직한 PostgreSQL `planned` 보고**, **프론트엔드 기능 모듈 분리**, **PR 검색 게이트·macOS DMG CI/CD 자동화**를 통합한 릴리스.

- **임베딩 계약 & 지식 라이프사이클:** `rust/lattice-core/src/embedding_contract.rs`, `graph_write/lifecycle.rs`, `latticeai/core/embedding_providers/`, `latticeai/worker_app.py`에서 `EmbeddingIndexContract`, 세분화된 `IngestStatus`, 통합 `GraphWriter` 삭제, `KnowledgeLifecycleState`를 강제한다.
- **검색 스코핑·정렬·캐시 & 게이트웨이 격리:** `rust/lattice-retrieval`에서 `LIMIT` 전 SQL 필터링, `vector_search_aligned`, `SidecarCachePool` LRU 상한(`DEFAULT_MAX_CACHE_BYTES`)을 적용했고, `rust/lattice-agent`/`lattice-host`/`lattice-auth`에서 `IsolatedRootSpec`·`resolve_agent_scope`로 멀티유저·테넌트·워크스페이스 격리와 감사 로깅을 E2E 검증했다.
- **포터빌리티 아카이브, UI 모듈화 & macOS DMG 자동화:** `rust/lattice-platform/src/knowledge/portability/`에서 AEAD 암호화 `.latticebrain` 검증·복원 및 X25519 공유를 제공하고, `Library.tsx`·`System.tsx`·`Capture.tsx`를 `frontend/src/features/`로 분할(Vitest 100%)했으며, `.github/workflows/{ci,release,publish}.yml`에 검색 게이트와 Apple Silicon DMG 빌드·서명·SHA256SUMS 업로드를 추가했다.

릴리스 노트는 [docs/releases/RELEASE_NOTES_v12.8.0.md](../releases/RELEASE_NOTES_v12.8.0.md).

빌드 산출물은 `dist/ltcai-12.8.0-py3-none-any.whl`, `dist/ltcai-12.8.0.tar.gz`, `ltcai-12.8.0.tgz`, `dist/ltcai-12.8.0.vsix`, `src-tauri/target/release/bundle/dmg/Lattice AI_12.8.0_aarch64.dmg` 입니다.

## v12.7.0 — Fast Index (2026-11-16)

**Pre-Tokenized BM25F Sidecar (`Bm25SidecarIndex`)**, SQLite `bm25_node_stats` 영속화, **역색인 포스팅 리스트·GraphRAG 인접 캐시**, **제품 중심 `README.md` 개편 및 `v12.0.0+` 릴리스 히스토리 정리**를 포함한 릴리스. 문은 **422 오퍼레이션 / 41 패밀리**, 워커는 **20 라우트** (변동 없음). 쓰기는 계속 `GraphWriter` 하나다.

- **Pre-Tokenized BM25F Sidecar & `bm25_node_stats` SQLite 영속화.** `rust/lattice-core/src/bm25_stats.rs`, `db/tables.rs`, `graph_write/{mod,primitives,documents,curation,provenance}.rs`에 `bm25_node_stats` 스키마·`TableOwnership`을 등록하고 `GraphWriter::open`에서 `persist_bm25_sidecar`를 활성화해 노드 저장/민감도 갱신 시 필드 길이(`dl_title`, `dl_tags`, `dl_body`, `dl_meta`)와 토큰 빈도(`tf_json`)를 미리 영속화하며, 삭제·큐레이션·재임포트 시 자동 정리한다. `bm25_node_stats`가 없는 읽기 전용/동결 골든 DB는 안전하게 스킵해 호환성을 유지한다.
- **O(1) 사이드카 인덱스 & GraphRAG 인접 캐시.** `rust/lattice-retrieval/src/bm25/{sidecar,sidecar_cache,scoring,graph_rag,fusion,tokenizer,mod,tests}.rs` 및 `service_hybrid.rs`에서 `Bm25SidecarIndex`가 `(node_id, updated_at, content_hash)` 기준 증분 동기화, O(1) 필드 길이·용어 빈도 조회, 역색인 포스팅 후보 평가 및 `expand_knowledge_graph_with_sidecar` / `score_and_expand_with_sidecar`용 `title_to_doc_idxs`·`wikilink_to_doc_idxs`·`tag_to_doc_idxs`·`edge_adj` 캐시를 제공한다.
- **제품 중심 `README.md` 및 `v12.0.0+` 릴리스 정리.** `README.md`를 제품 소개·빠른 시작·8패널 Product Tour 중심으로 재구성하고, 로컬(`docs/releases/`, `docs/reviews/`, `RELEASE.md`, `RELEASE_NOTES.md`, `docs/CHANGELOG.md`, `FEATURE_STATUS.md`, `SECURITY.md`, `vscode-extension/README.md`)과 GitHub Releases에서 pre-v12 노트를 정리하며 `scripts/check_current_release_docs.mjs`에 `v12.0.0+` 하한을 적용했다.

릴리스 노트는 [docs/releases/RELEASE_NOTES_v12.7.0.md](../releases/RELEASE_NOTES_v12.7.0.md).

빌드 산출물은 `dist/ltcai-12.7.0-py3-none-any.whl`, `dist/ltcai-12.7.0.tar.gz`, `ltcai-12.7.0.tgz`, `dist/ltcai-12.7.0.vsix`, `src-tauri/target/release/bundle/dmg/Lattice AI_12.7.0_aarch64.dmg` 입니다.

## v12.6.0 — Hybrid Graph (2026-11-15)

**3-way BM25F + Vector + Knowledge Graph RRF 하이브리드 인덱싱·검색**, 그래프 인식 에이전트 스킬 및 MCP 검색 도구, **멀티스케일 서브워드 `FallbackTextEmbedder`**, **비인증 EmbeddingGemma 2 공개 미러 및 양자화(GGUF/MLX/ONNX) fallback**, **SSE 실시간 다운로드 속도(`MB/s`)·남은 시간(`ETA`) 스트리밍 UI**, **Pillow 13+ 레거시 제거**, **Ubuntu 24.04/26.04 `t64`·Chromium CI 복원력**, **모듈화 `scripts/bump_version/` CLI**를 통합한 릴리스. 문은 **422 오퍼레이션 / 41 패밀리**, 워커는 **20 라우트** (변동 없음). 쓰기는 계속 `GraphWriter` 하나다.

- **3-Way BM25F + Vector + Knowledge Graph RRF 하이브리드 검색.** `rust/lattice-retrieval/src/bm25/`, `service_hybrid.rs`, `hybrid.rs`, `search_api/handlers.rs`에 필드 가중 오카피 BM25F(`k1=1.2, b=0.75`, 한국어 조사 제거·CJK 바이그램·식별자 서브워드), 1-hop/2-hop 지식 그래프 구조 확장(`RELATES_TO`, `MENTIONS`, `PART_OF`, 공유 개념 노드, `[[wikilinks]]`, `#tags`), 3-way RRF 융합 및 `/api/search/hybrid` zero-hit rescue fallback을 구현했다.
- **그래프 스킬 및 에이전트·MCP 그래프 도구.** `rust/lattice-platform/src/toolsurface/tools/knowledge/`와 `rust/lattice-agent`(`tools/catalog.rs`, `prompts/mod.rs`)에서 `knowledge_search`/`obsidian_search`의 `mode`(`hybrid`/`bm25f`/`graph`/`graphrag`/`subgraph`), `hops`, `include_graph` 서브그래프 추출과 `knowledge_graph_search`/`knowledge_graph_context`, `GRAPH_SKILL_BRIEFS`를 지원한다.
- **멀티스케일 서브워드 `FallbackTextEmbedder` (#13.1).** `latticeai/core/embedding_providers/text.py`와 `rust/lattice-core/src/embeddings.rs`의 오프라인 임베더(`768d`, `lattice-fallback-subword-v2:768`)를 `3..5-gram` 서브워드, 한국어 조사 제거 음절·초성 쌍, CJK n-gram, 2-probe 부호 있는 Blake2b 투영 및 L2 정규화로 고도화했다.
- **EmbeddingGemma 2 비인증 공개 미러 및 양자화 fallback (#14.2).** `HF_TOKEN` 미설정 또는 gated `401`/`403` 응답 시 `mlx-community/embeddinggemma-2-4bit`, `mlx-community/embeddinggemma-2-8bit`, `ggml-org/embeddinggemma-2-GGUF`, `onnx-community/embeddinggemma-300m-ONNX` 공개 미러로 자동 전환하며 `HF_ENDPOINT` 커스텀 미러를 지원한다.
- **실시간 SSE 다운로드 진행률 스트리밍 UI (#15.2).** 다운로드 엔진(`download.py`, `rust/lattice-platform/src/modelops`)이 `downloaded_bytes`, `total_bytes`, `percent`, `speed_bps`, `eta_seconds`, `gated_fallback`을 SSE(`text/event-stream`)로 전송하고 `ProductFlow.tsx`·`InstallScreen.tsx`·`Library.tsx`에 실시간 진행률·속도(`MB/s`)·ETA·미러 안내를 시각화했다(Vitest 100% 커버리지).
- **Pillow 13+ 정리(#13.2), Ubuntu 24.04/26.04 `t64` CI 복원력(#14.1) & `scripts/bump_version/` CLI(#15.1).** `scripts/check_screenshot_pixel_delta.py`에서 `diff.getdata()` 레거시를 제거하고, `.github/workflows/*.yml`과 `scripts/check_ubuntu_runner_compat.mjs`에 `t64` 패키지 fallback 및 Chromium 검증을 추가했으며, `scripts/bump_version/` 패키지(`all`, `openapi`, `cargo`, `markers` 서브커맨드)로 모듈화했다.

릴리스 노트는 [docs/releases/RELEASE_NOTES_v12.6.0.md](../releases/RELEASE_NOTES_v12.6.0.md).

빌드 산출물은 `dist/ltcai-12.6.0-py3-none-any.whl`, `dist/ltcai-12.6.0.tar.gz`, `ltcai-12.6.0.tgz`, `dist/ltcai-12.6.0.vsix`, `src-tauri/target/release/bundle/dmg/Lattice AI_12.6.0_aarch64.dmg` 입니다.

## v12.5.0 — Deep Vector (2026-11-11)

Hugging Face에서 제공되는 **Google EmbeddingGemma 2**(`google/embeddinggemma-2`, 768차원)를 네이티브 임베딩 제공자로 전면 통합하고, 사용자 호스트 하드웨어(RAM/VRAM)를 정밀 측정하여 임베딩 모델 메모리를 사전 예약한 후 최적 LLM을 선정하는 맞춤형 모델 추천 시스템을 도입한 릴리스. Pillow 13+ `get_flattened_data()` 전환, GitHub Actions CI/CD 워크플로우의 Node 24 네이티브 업그레이드, `scripts/bump_version.py`의 OpenAPI 스펙·파편 49개 타깃 자동 갱신을 완료했다. 문은 **422 오퍼레이션 / 41 패밀리**, 워커는 **20 라우트** (변동 없음). 쓰기는 계속 `GraphWriter` 하나다.

- **Google EmbeddingGemma 2 네이티브 통합.** `latticeai/core/embedding_providers/text.py`에 Hugging Face 임베딩 프로바이더(`google/embeddinggemma-2`, `embeddinggemma-2`, `embeddinggemma-2-4bit`, `embeddinggemma-2-8bit`, `embeddinggemma-300m`, 768차원)를 구현하고 Mean pooling, L2 정규화, 태스크 접두사를 지원하며, 오프라인 시 결정론적 해시 임베딩 fallback을 유지했다.
- **PC 하드웨어 맞춤형 임베딩 모델 추천 시스템.** `rust/lattice-platform/src/modelops/models_catalog/embedding.rs` 및 `latticeai/services/embedding_recommendation.py`로 분리 모듈화하여 8GB 이상 호스트는 EmbeddingGemma 2(0.5GB~1.5GB RAM 예약), 미만 호스트는 EmbeddingGemma 300M(0.3GB RAM 예약)을 추천하며 가용 메모리(`effective_ram_gb`)를 사전 차감하여 LLM을 추천하도록 했다.
- **온보딩 및 모델 라이브러리 임베딩 UI.** 온보딩 화면(`ProductFlow.tsx`)에 PC 맞춤 EmbeddingGemma 2 추천 카드를 추가(다국어 i18n 지원)하고, 모델 라이브러리(`Library.tsx`)에 추천 임베딩 모델 섹션 및 원클릭 준비 버튼을 배치했다.
- **Pillow 13+ 권장 API 전환 및 CI/CD 현대화.** `scripts/check_screenshot_pixel_delta.py`에서 Pillow 14 제거 예정인 `diff.getdata()`를 `diff.get_flattened_data()`로 교체하고, GitHub Actions 워크플로우 7종 전체에 Node 24 네이티브 메이저 버전과 `FORCE_JAVASCRIPT_ACTIONS_TO_NODE24: "true"`를 적용했다.
- **OpenAPI 명세 및 파편 버전 동기화 자동화.** `scripts/bump_version.py`가 `frontend/openapi.json`, `rust/fixtures/openapi/_envelope.json`, 13개 패밀리 파편, `_index.json`(SHA-256 재계산) 등 총 49개 타깃을 일괄 갱신하도록 확장했다.
- **전체 인터랙티브 UI 점검 완료.** `tests/visual/v3.all_buttons_inspection.spec.js` Playwright E2E 스위트로 전체 라우트와 모든 버튼을 클릭 검증하여 0 콘솔 에러, 0 서버 에러를 확인했다.

릴리스 노트는 [docs/releases/RELEASE_NOTES_v12.5.0.md](../releases/RELEASE_NOTES_v12.5.0.md).

빌드 산출물은 `dist/ltcai-12.5.0-py3-none-any.whl`, `dist/ltcai-12.5.0.tar.gz`, `ltcai-12.5.0.tgz`, `dist/ltcai-12.5.0.vsix`, `src-tauri/target/release/bundle/dmg/Lattice AI_12.5.0_aarch64.dmg` 입니다.

## v12.4.0 — Sharp Weight (2026-11-11)

Hugging Face에서 검증된 **Qwen 3.8 27B 4-bit**(`mlx-community/Qwen3.8-27B-4bit`)와 **Gemma 4 26B A4B QAT / QAT-OptiQ 4-bit**(`mlx-community/gemma-4-26B-A4B-it-qat-4bit`, `mlx-community/gemma-4-26B-A4B-it-qat-OptiQ-4bit`)를 모델 추천 및 다운로드 카탈로그에 추가하고, Python(`pillow>=13,<14`)과 npm 의존성 보안 취약점을 전수 해소한 릴리스. 문은 **422 오퍼레이션 / 41 패밀리**, 워커는 **20 라우트** (변동 없음). 쓰기는 계속 `GraphWriter` 하나다.

- **Qwen 3.8 27B 4-bit 추가 및 멀티엔진 라우팅.** `mlx-community/Qwen3.8-27B-4bit` (`16.1GB`, `qwen3_5`)를 대형 티어에 추가하고, `ggml-org/Qwen3.8-27B-GGUF:Q4_K_M` 및 `Qwen/Qwen3.8-27B` 경로를 5개 로컬 엔진(`local_mlx`·`ollama`·`vllm`·`lmstudio`·`llamacpp`)과 클라우드 공급자(`together`·`openrouter`), Rust `CATALOG_HINTS`에 연결했다.
- **Gemma 4 26B A4B QAT & QAT-OptiQ 4-bit 추가.** Google 공식 QAT 기반의 `mlx-community/gemma-4-26B-A4B-it-qat-4bit` (`15.6GB`, `google/gemma-4-26B-A4B-it-qat-q4_0-gguf:Q4_0` 라우팅)와 민감도 기반 혼합 4/8-bit `mlx-optiq` 빌드인 `mlx-community/gemma-4-26B-A4B-it-qat-OptiQ-4bit` (`21.9GB`)를 추가했다.
- **Python · npm 의존성 보안 감사 해소 및 런타임 보강.** `pillow>=13,<14` 상향으로 `Pillow 12.3.0`의 39개 CVE를 해결하고, 루트 및 `vscode-extension`의 npm audit 취약점을 0건으로 정리했으며, `model_engines`의 HTTP(S) 스킴 검증, `lattice-platform` `admin_log_retention`의 주입형 시계(`state.auth.clock()`) 연동, `frontend/src/styles.css`의 `@import "tailwindcss" source("..");` 스캔 범위 제한을 적용했다.

릴리스 노트는 [docs/releases/RELEASE_NOTES_v12.4.0.md](../releases/RELEASE_NOTES_v12.4.0.md).

빌드 산출물은 `dist/ltcai-12.4.0-py3-none-any.whl`, `dist/ltcai-12.4.0.tar.gz`, `ltcai-12.4.0.tgz`, `dist/ltcai-12.4.0.vsix`, `src-tauri/target/release/bundle/dmg/Lattice AI_12.4.0_aarch64.dmg` 입니다.

## v12.3.0 — Live Wire (2026-10-19)

Hugging Face 실저장소 양자화 태그·GGUF 라우팅·클라우드 공급자 기본값을 전수 정렬하고, 작은 로컬 모델의 툴콜/MCP 인자 복구와 HTML 생성·점검·다운로드를 실모델(`mlx-community/gemma-4-e2b-it-4bit`)로 끝까지 검증한 릴리스. 문은 **422 오퍼레이션 / 41 패밀리**, 워커는 **20 라우트** (변동 없음). 쓰기는 계속 `GraphWriter` 하나다.

- **Hugging Face GGUF 태그 및 라우팅 정합성.** `ggml-org/gemma-4-*-GGUF`는 `Q4_0`, `ggml-org/gpt-oss-20b-GGUF`는 `MXFP4` 태그를 가리키며, `Qwen3.5-9B-MLX-4bit` (`unsloth/Qwen3.5-9B-GGUF`)와 `Qwen3.6-35B-A3B-4bit` (`ggml-org/Qwen3.6-35B-A3B-GGUF`) GGUF 경로가 `ollama` / `lmstudio` / `llamacpp`에 연결됐다.
- **클라우드·로컬 공급자 기본값 동기화.** `xai` 기본값을 `grok-4.6`, `together`를 `Qwen/Qwen3.6-27B`, `openrouter`를 `qwen/qwen3.6-35b-a3b`·`x-ai/grok-4.6`으로 맞추고, 존재하지 않는 `mlx-community/bge-m3`를 로컬 MLX 목록에서 제거했다.
- **보조 GGUF 필터링 · `--mmproj` 동반 로드 · 샤드 검증.** `mmproj-`/`mtp-`/`dflash-`/`eagle3-`/`imatrix` GGUF를 주 모델 선택에서 제외하고, `llama-server` 기동 시 동반 `mmproj-*.gguf`를 다운로드해 `--mmproj`로 전달하며, `model.safetensors.index.json`의 샤드 완결성을 검증한다.
- **작은 모델 툴콜·MCP 호출 보강.** `KNOWN_ACTIONS` 확장, 문자열화된 `arguments` 복구, OpenAI `tool_calls` 배열 언래핑, 코드블록 HTML 추출(`fenced_content_block`), `perform_action` 내 `adopt_named_args`(`html`/`code`/`source`/`data` → `content`) 적용.
- **HTML 구조 점검 및 RFC 5987 UTF-8 다운로드.** `POST /tools/inspect_html`이 `title`·`links`·`scripts`·`stylesheets`·`images`·`forms`·`headings`를 추출하고, `GET /tools/download`가 UTF-8 경로 디코딩과 `filename*=UTF-8''...` 헤더를 제공한다.

릴리스 노트는 [docs/releases/RELEASE_NOTES_v12.3.0.md](../releases/RELEASE_NOTES_v12.3.0.md).

빌드 산출물은 `dist/ltcai-12.3.0-py3-none-any.whl`, `dist/ltcai-12.3.0.tar.gz`, `ltcai-12.3.0.tgz`, `dist/ltcai-12.3.0.vsix`, `src-tauri/target/release/bundle/dmg/Lattice AI_12.3.0_aarch64.dmg` 입니다.

## v12.2.1 — True Count (2026-08-29)

검색·스킵·워치·작은 모델 답이 *있는 것만* 세는 패치. 문은 **422 오퍼레이션 / 41 패밀리**, 워커는 **20 라우트** (변동 없음). 쓰기는 계속 `GraphWriter` 하나다.

- **HNSW 사이드카 COUNT+델타.** 따뜻한 쿼리는 벡터 테이블을 통째로 읽지 않는다. 늘어난 행만 불러 붙인다.
- **SkipByHash가 스탬프를 다시 찍는다.** 같은 바이트의 `touch`가 다음 스캔에서 스탬프 스킵이 된다.
- **워치에서 사라진 파일은 그래프에서 지운다.** 디스크는 건드리지 않는다. 폴더 인제스트는 여전히 보고만 한다.
- **얇은 요약은 읽은 파일에서 채운다.** 근거가 없으면 검토 대기.
- **콤팩트 9행에 MCP 코어가 남는다.** `api_key`는 과금 없이 `GET /models`로 살아 있는지 확인하고, 안 되면 닫는다.
- **해시 검색은 해시라고 말한다.** 첫 실행·라이브러리가 의미 모델 설치를 가리킨다.

릴리스 노트는 [docs/releases/](../releases/)에 모아 두었다. 이 버전: [docs/releases/RELEASE_NOTES_v12.2.1.md](../releases/RELEASE_NOTES_v12.2.1.md).

빌드 산출물은 `dist/ltcai-12.2.1-py3-none-any.whl`, `dist/ltcai-12.2.1.tar.gz`, `ltcai-12.2.1.tgz`, `dist/ltcai-12.2.1.vsix`, `src-tauri/target/release/bundle/dmg/Lattice AI_12.2.1_aarch64.dmg` 입니다.

정직한 고지: 해시 임베딩은 여전히 다운로드 없는 기본값(이제 정직하게 표시), DMG는 Apple Developer ID가 없으면 ad-hoc, Marketplace는 게시 직후 몇 분 검증이 걸릴 수 있다.

## v12.2.0 — Small Voice (2026-08-29)

2B급 로컬 모델이 MCP·스킬·도구를 고를 수 있게, 그리고 그 일을 홈 화면에서 바로 고를 수 있게 만든 릴리스. 문은 **422 오퍼레이션 / 41 패밀리**, 워커는 **20 라우트** (변동 없음).

- **툴콜 봉투.** `name`/`arguments`, Qwen `<function=…>` XML, `Action: mcp.grep`이 같은 호출이 된다. 콤팩트 액션 목록은 9행.
- **스킬 when.** 요청이 스킬의 "use when" 문구와 맞으면 이름을 안 해도 안내문을 먼저 읽는다.
- **요청이 이미 말한 인자.** 따옴표 친 검색어가 빠진 `pattern`을 채운다.
- **홈 작곡기.** 폴더 찾기 · 파일 읽고 요약 · 기억 찾기 · 스킬 검토 칩. 설정에 같은 설명이 한 카드로 있다.

12.1.0 Fast Path 인제스트 겹치기는 그대로다. 자세한 목록은 [docs/releases/RELEASE_NOTES_v12.2.0.md](../releases/RELEASE_NOTES_v12.2.0.md).

빌드 산출물은 `dist/ltcai-12.2.0-py3-none-any.whl`, `dist/ltcai-12.2.0.tar.gz`, `ltcai-12.2.0.tgz`, `dist/ltcai-12.2.0.vsix`, `src-tauri/target/release/bundle/dmg/Lattice AI_12.2.0_aarch64.dmg` 입니다.

## v12.1.0 — Fast Path (2026-08-29)

디스크에서 회상까지 줄을 세우던 경로를 겹친 릴리스. 쓰기 엔진은 그대로 하나고, 느렸던 것은 그 앞의 시임과 그 안의 락이다. 문은 **422 오퍼레이션 / 41 패밀리**, 워커는 **20 라우트** (변동 없음).

- **폴더·watch 인제스트 파이프라인.** 최대 4개 파일이 parse / extract / embed를 겹친다. 스탬프 스킵은 풀에 들어가지 않는다. 쓰기는 여전히 `GraphWriter` 하나다.
- **노트당 `/worker/embed` 한 방.** 문서 벡터와 청크 벡터가 한 `texts` 바디. extract는 그 앞에 서지 않고 옆에 선다. `write_vectors_with`가 그 벡터를 파일해서 해시 모델이 같은 텍스트를 다시 돌리지 않는다.
- **rebuild가 임베드를 쓰기 락 밖에서.** 12.0.0 drain과 같은 모양.
- **검색 auto-HNSW.** env 기본은 여전히 `brute`. 512행 이상이고 워커 시임이 묶여 있으면 `hnsw+rescore`를 먼저 시도하고, 실패하면 원래 brute 블록으로 정확 스캔한다. 골든은 바이트 동일.
- **CI.** 주간 pip-audit가 깨끗한 런에서 리포트 파일 부재로 실패하던 것, 주간 Postgres 잡이 삭제된 테스트 파일을 찾다 실패하던 것을 고쳤다. `ltcai[postgres]`는 빈 extra 별칭.

정직한 고지: 작은 모델 내용 품질, `api_key` 모의 검증, ad-hoc DMG, watch 자동 삭제 없음, 실임베딩 기본 롤아웃은 아직. 자세한 목록은 [docs/releases/RELEASE_NOTES_v12.1.0.md](../releases/RELEASE_NOTES_v12.1.0.md).

빌드 산출물은 `dist/ltcai-12.1.0-py3-none-any.whl`, `dist/ltcai-12.1.0.tar.gz`, `ltcai-12.1.0.tgz`, `dist/ltcai-12.1.0.vsix`, `src-tauri/target/release/bundle/dmg/Lattice AI_12.1.0_aarch64.dmg` 입니다. 와일드카드 업로드는 사용하지 않습니다.

## v12.0.0 — Open House (2026-08-18)

집을 정리해 손님을 들이는 릴리스. 가장 큰 두 크레이트를 도메인으로 나누고(전부 `git mv`, 동작 변화 0), 처음 온 사람이 읽을 문서를 다시 썼으며, 기존에 정직하게 적어 둔 갭 네 개를 닫았다. 문은 네이티브 **422 오퍼레이션 / 41 패밀리**(`POST /mcp` + 폴더 정리 라우트 가산), 워커는 **20 라우트**(`POST /worker/vector/query` 가산).

- **복잡도 관리(소유자 1순위)**: `lattice-agent` 43파일이 `kernel`/`parse`/`content`/`tools`/`surface`/`prompts` 여섯 그룹으로, `lattice-platform` 31개 평면 모듈이 `workspaceos`/`toolsurface`/`governance`/`adminops`/`knowledge`/`modelops`/`shell` 일곱 도메인으로(100건 `git mv`). 두 크레이트 모두 크레이트 로컬 `ARCHITECTURE.md`를 싣고, 각 그룹 `mod.rs`가 무엇이 속하고 무엇이 절대 들어가면 안 되는지와 불변식을 적으며, 각 `src/lib.rs`가 호환 맵으로 끝나 기존 임포트 경로가 전부 그대로 해석된다. `docs/DEVELOPMENT.md`는 기여자 온보딩(10분 퀵스타트 + 어디에-무엇을 표 + 게이트 안내)으로 재작성, `docs/ROADMAP.md` 신설(우선순위 있는 갭 목록).
- **정직한 갭 4종 마감**: 복원이 스토어 세대(generation) 에폭으로 인프로세스 즉시 반영(재시작 불필요) · `/setup/install`이 **서버가 도출한 allowlist**의 항목에 한해 명시적 동의로 brew/pip/uv를 실행(기본은 여전히 수동) · `POST /mcp`가 OpenAPI 계약 안으로(단일 JSON-RPC 봉투 오퍼레이션, 네이티브 마운트이므로 워커로 프록시되지 않음) · 포인터 도구가 `pip install "ltcai[pointer]"`로 선언됨.
- **그래프 RAG 품질**: 한국어 2단계 조사 스트리핑 + 근거 게이트, 포함관계 중복 제거, 방향 있는 타입드 엣지(`PART_OF`·`CONTRADICTS`를 실제로 생산, 근거 분류), 섹션 트리(`Document ←PART_OF— Section —HAS_CHUNK→ Chunk`, 트리플 555개 중 549개가 섹션 출처 보유), 임베딩 자동 감지(실모델 있으면 채택, 해시는 폴백이라고 표기, 벡터 정체성 `(model,dim)` 필터 검증).
- **그래프 RAG 속도**: 무변경 재인덱스 33s → **0.26s**(낭비율 1.00 → 0.00, 핑거프린트는 `ingestion_provenance`에 정착) · 첫 인덱싱 25.8s → 7.2s · 드레인 ~66 → ~1,300 items/s(임베드를 트랜잭션 앞으로) · 백로그 991건 40분 → 15.3초(적응형 스케줄러) · HNSW 증분 append + 검색 실사용(`LATTICEAI_VECTOR_INDEX=hnsw` → 워커 사이드카 후보 + Rust 정확 재스코어 `hnsw+rescore`, 실패 시 사유를 실은 폴백; **기본은 여전히 brute**) · vault-watch diff 스킵 · 삭제 파일 정리(`POST /api/ingestion/folder/prune` dry-run/confirm, 「삭제된 파일 정리 (N)」 카드, `delete_document_tree`로 댕글링 0).
- **범용 소형모델 하네스(모델 불문, per-model 핵 금지)**: GUIDED 모드가 JSON 요구 자체를 제거(번호 메뉴 → 인자 한 개씩 → 하네스가 액션 조립, 꼬리는 모든 모드와 동일한 `perform_action`) · 측정 기반 프로브가 standard/compact/guided를 고르고 모델 id + 크레이트 버전으로 캐시 · 미드런 하향 자기강등(위로는 절대 안 감) · 통합 카탈로그(native + `mcp.*` + `skill.*` 한 메뉴, mcp는 `POST /mcp`와 같은 거버넌스) · 시임 prefix 강제 + stop. Qwen **0.5B가 guided로 DONE**(3.9초, 실파일). 모델 매트릭스 최종 수치는 릴리스 시점 `MATRIX_TABLE` 기준.
- **프론트**: ErrorBoundary 전면(라우트 + 헤비 패널, 「다시 시도」), Act/Brain 서브라우트 lazy 분할, 프리뷰 패턴 확장(권한 모드 diff 프리뷰, 위험 기능 토글 ack), 번들 **104.2 / 150 KiB**.
- **고쳐진 기존 버그**: RAG 인용 지시가 에이전트 프롬프트로 새던 것, 모델 로딩 이름 로스터 게이트(Qwen AWQ 로드 불가였음), 베낄 수 있는 워크드 예제(`COPIED_EXAMPLE` fail-closed), `<|channel>` 한-파이프 제어 프레임 누수.
- **정직한 고지**: 작은 모델의 *내용* 품질은 fail-closed 게이트가 잡는다(기계적 실행은 안정) · `api_key` 클라우드 경로는 여전히 모의 검증만(과금 0 정책) · DMG는 ad-hoc 서명 · 검색 기본은 brute(hnsw는 opt-in) · watch는 자동 삭제하지 않음 · 크레이트 재구조는 이동이지 결합 해소가 아님.

빌드 산출물은 `dist/ltcai-12.0.0-py3-none-any.whl`, `dist/ltcai-12.0.0.tar.gz`, `ltcai-12.0.0.tgz`, `dist/ltcai-12.0.0.vsix`, `src-tauri/target/release/bundle/dmg/Lattice AI_12.0.0_aarch64.dmg` 입니다. 와일드카드 업로드는 사용하지 않습니다.

상세: [docs/releases/RELEASE_NOTES_v12.0.0.md](../releases/RELEASE_NOTES_v12.0.0.md)
