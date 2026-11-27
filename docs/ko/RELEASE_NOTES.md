[![English](https://img.shields.io/badge/English-Switch-475569?style=flat-square)](../../RELEASE_NOTES.md) [![한국어](https://img.shields.io/badge/한국어-현재_언어-2563eb?style=flat-square)](./RELEASE_NOTES.md)

# [v14.5.0 - Living Automations] (2026-11-27)

`workflow_designer/triggers/`와 `GraphWriter` 수집 후크 기반의 실제 예약(`interval`) 및 브레인 이벤트(`brain_event`) 자동화, 검토 센터 초안 스테이징, 실제 `GET /workflows/api/triggers` `armed` 응답을 출시했습니다. 출처·신뢰 등급 기반 "최신" 랭킹과 인용 피드백 점수 조정을 도입하고, `POST /api/portability/vault` Markdown/Obsidian 볼트 내보내기(`431` 경로 / `474` 오퍼레이션), 캡처 가져오기 소스 UI, 대화 라이브러리(검색·전체·Markdown 내보내기), 워크플로 실행 이력/리플레이, 내보내기·중지 결함 수정, 검토 일괄 처리, 검색 필터, 원클릭 출처 피드백을 추가했습니다.

상세 내용: [docs/ko/releases/RELEASE_NOTES_v14.5.0.md](./releases/RELEASE_NOTES_v14.5.0.md).

# [v14.4.0 - Measured Retrieval] (2026-11-27)

실제 EmbeddingGemma 2 및 `BAAI/bge-reranker-v2-m3` 가중치로 처음 측정했으며, "최신" 질문이 최신성 정렬로 답변을 잃던 문제, top-k 절단 전 워크스페이스 범위 적용, 워커 시작 시 재랭커 미리 적재, 동시 콜드 요청에서의 임베딩 모델 단일 적재를 고쳤습니다. `pip install "ltcai[rag]"`는 이제 `torchvision`을 설치합니다. 벡터 경로가 비활성인 상태로 측정되었던 v14.3.0 해시 게이트를 다시 측정해 하한선을 올렸고, 3D 살아있는 브레인의 라벨·WebGL 컨텍스트 손실·호버 피킹을 업데이트했습니다.

상세 내용: [docs/ko/releases/RELEASE_NOTES_v14.4.0.md](./releases/RELEASE_NOTES_v14.4.0.md).

# [v14.3.0 - Temporal Synthesis] (2026-11-27)

첫 답변 후 데스크톱 채팅 접힘, `Cmd/Ctrl+K` "오늘의 브리핑" 화면 이동 열기, 2D 기억 그래프 "구버전 표시" 토글, 커맨드 팔레트 키보드 스크롤을 수정했습니다. 채팅 검색에 IDF/BM25F 관련도 랭킹, `as_of` 유효기간 평가 및 연도 파싱, 엄격한 Self-RAG 커버리지 보류, 채팅 경로 CI 래칫 게이트(`R@5 0.9194 → >=0.98`, `MRR 0.6122 → >=0.85`, `nDCG@10 0.6826 → >=0.87`, `temporal_ordering` `R@5 0.600 → >=0.99`, `MRR 0.267 → >=0.79`)를 추가하고, 공유 번들 내보내기·가져오기, 시간 사전 필터, `LIMIT` 이전 범위 필터, 연대기 fail-closed 뷰어 필터, 덮어쓰기 식별자 인덱싱을 강화했습니다.

상세 릴리스 노트: [docs/ko/releases/RELEASE_NOTES_v14.3.0.md](./releases/RELEASE_NOTES_v14.3.0.md).

# [v14.2.0 - Temporal Synthesis] (2026-11-27)

검색을 시간 중심으로 재구성했습니다: 문맥 접두어를 갖춘 토큰·AST·parent-child 청킹, 새 `auto` 임베딩 기본값과 선택적 `rag` extra를 통한 EmbeddingGemma 2, RRF 대체를 갖춘 cross-encoder 재랭킹, `sort_by`·시간 의도 사전 필터·`SUPERSEDES` 처리를 포함한 2단계 시간 랭킹, 채팅의 대화 이력 반영 질의 재작성·MMR·Self-RAG 게이트, 3D 살아있는 Brain 화면·고정 뷰포트 데스크톱 셸·커맨드 팔레트, `ltcai search` CLI, 다중 사용자 대화 노출·업로드/폴더 소유권·공유 번들 가져오기 수정. 실제 EmbeddingGemma 2 및 reranker 모델 로딩은 빌드 환경에서 검증하지 못했습니다.

상세 릴리스 노트: [docs/ko/releases/RELEASE_NOTES_v14.2.0.md](./releases/RELEASE_NOTES_v14.2.0.md).

# [v14.1.2 - Living Synthesis] (2026-11-25)

모든 추적 소스 언어에 저장소 전역 무주석(zero-comments) 표준을 적용했습니다: 필수 셔뱅 및 `/// <reference ... />` 지시문을 제외한 모든 인라인 주석, 블록 주석, 독스트링, JSDoc, 억제 지시문을 제거하고 전역 설정 완화 없이 소스 코드를 직접 수정해 모든 Ruff, mypy, TypeScript, Rust Clippy 검증을 통과했습니다. `npm run check:no-comments` (`scripts/check_no_comments.mjs`)를 `npm run lint`와 `.github/workflows/ci.yml`에 연동하고 `AGENTS.md`, `CONTRIBUTING.md` 및 한국어 미러에 영구 정책을 명시했습니다.

상세 릴리스 노트: [docs/ko/releases/RELEASE_NOTES_v14.1.2.md](./releases/RELEASE_NOTES_v14.1.2.md).

# [v14.1.1 - Living Synthesis] (2026-11-25)

Python 포매팅, McCabe 복잡도 전역 강제, 서버 i18n 래칫, 컨테이너 위생에 대한 제로 기술 부채 패스를 완료했습니다: 전체 Python 파일 219개를 전용 커밋(`55ba57c381a433066fd94434c3a6250c4a1beb59`)으로 `ruff format .` 포매팅하고 `.git-blame-ignore-revs`에 기록하며 `npm run lint:python` 및 CI에 `ruff format --check .`를 강제했습니다. `create_models_router`와 `create_worker_compute_router`를 분해해 `pyproject.toml`의 `"C901"` 예외를 전량 제거(`max-complexity = 20` 예외 0건)하고 `/worker/extract` 테스트를 `tests/unit/test_worker_compute_extract.py`로 분리했으며, `worker_compute_render.py`와 `worker_resilience.py`를 `NON_ROUTER_HELPERS`에 명시 등록해 `scripts/check_server_i18n.mjs`를 `0 unclaimed` fail-closed 래칫으로 잠갔고, `Dockerfile`의 `lattice-host` 바이너리 복사에 `--chown=lattice:lattice --chmod=0755`를 명시했습니다.

상세 릴리스 노트: [docs/ko/releases/RELEASE_NOTES_v14.1.1.md](./releases/RELEASE_NOTES_v14.1.1.md).

# [v14.1.0 - Living Synthesis] (2026-11-25)

v14.0.0 치명적 회귀 2건을 수정하고 풀스택 정적 분석·문서 최신화를 완료했습니다: Rust ↔ Python 타임스탬프를 오프셋 없는 UTC(`YYYY-MM-DDTHH:MM:SS`)로 통일하고 다중 타임존 회귀 테스트를 추가했으며, Tauri 프로덕션 빌드를 `PortPolicy::PinnedProduction`으로 포트 `4825`에 고정해 충돌 시 즉시 실패(fail-fast)하도록 수정했습니다. Python 전역의 포괄적 `except Exception`을 구체적 예외로 좁히고 Ruff `"BLE"`·McCabe 복잡도 `<= 20`·최소 Bandit 예외를 적용했으며, `rust/lattice-auth`에 `messages.rs`와 `CsrfReason` 열거형을 도입하고 비실패 Tauri 커맨드가 `BackendStatus`를 직접 반환하도록 정리했습니다. 프론트엔드에 TypeScript 7 `"noUncheckedIndexedAccess": true`를 활성화해 68개 파일의 457건 오류를 해소(113개 테스트 파일 / 1,852개 테스트, Vitest 100%)했고, Playwright `visual` 스모크 잡을 PR에서도 실행되도록 확장했으며, 모든 영문·한국어 문서를 `v14.1.0` 기준으로 갱신했습니다.

상세 릴리스 노트: [docs/ko/releases/RELEASE_NOTES_v14.1.0.md](./releases/RELEASE_NOTES_v14.1.0.md).

# [v14.0.0 - Living Synthesis] (2026-11-25)

풀스택 보안·동시성·데스크톱/확장·CI/CD 고도화를 완료했습니다: `lattice-auth` 버전화 `$scrypt$` `Result` 기반 해싱, 상한 레이트 리미터 및 통합 `429` + `Retry-After` 응답과 영문 CSRF 거부; `lattice-core` 순수 Rust UTC 시계(`unsafe` `libc` 제거); `lattice-ingest`/`lattice-chat` 논블로킹 `spawn_blocking` I/O, 상한 HNSW/클라우드 예산 캐시, fail-closed 프록시 클라이언트, `EnvOverlayProbe`; Python 플랫폼 가드 MLX extra, `ContextVar` DI, Ruff 보안 규칙 복원; 엄격 TypeScript 7 플래그 및 `AppRouteView.tsx`(Vitest 100%); 직렬화 Tauri 게이트웨이 생명주기 및 `127.0.0.1:4825` 고정 CSP/권한; VS Code·브라우저 확장 강화; 실제 `windows-latest` Rust CI, `macos-latest` Tauri CI, 다이제스트 고정 멀티스테이지 Docker 스모크 CI; `14.0.0` 아키텍처 문서 최신화를 포함합니다.

상세 릴리스 노트: [docs/ko/releases/RELEASE_NOTES_v14.0.0.md](./releases/RELEASE_NOTES_v14.0.0.md).

# [v13.9.0 - Living Synthesis] (2026-11-24)

PR #69 보안·무결성 지적 6건을 모두 해결하고, 16개 디지털 브레인 확장 엔드포인트를 정본 `brain_api::MOUNTED`와 `memory_brain.json`에 통합하며 5개 대화형 React 브레인 거버넌스 패널과 `.github/workflows/nightly-rag-eval.yml`을 추가했습니다.

상세 릴리스 노트: [docs/ko/releases/RELEASE_NOTES_v13.9.0.md](./releases/RELEASE_NOTES_v13.9.0.md).

# [v13.8.0 - Living Synthesis] (2026-11-24)

디지털 브레인 플랫폼 통합 고도화 로드맵(P0–P3)을 완수했습니다: 결정론적 픽스처와 실제 로컬/클라우드 모델 평가를 분리하고 320개 질의 버전 데이터셋(`datasets/rag_eval_v1.json`)을 추가한 진정성 있는 RAG 평가, 자동 백업·드라이런·장애 주입 테스트를 갖춘 지식 그래프 무결성 검사/복구, 동음이의어 차단·퍼지 점수·선택적 롤백 엔티티 해석, OS 수준 `O_NOFOLLOW` FD 샌드박스 및 클라우드 이그레스 PII/비밀 마스킹, 섀도우 재색인·컷오버·롤백 임베딩 모델 레지스트리, 최소 권한 5역할 멀티에이전트 협업, 지식 학습 루프, OS 감시 파일 이동·일시중지, 모델 출처 계층 UX, 데스크톱 워치독 및 마스킹 `/host/diagnostics`, 토픽 클러스터/지식 공백/비디오 모달리티/감사 보존 정책/오프보딩 취소 영수증을 모두 반영했습니다.

상세 릴리스 노트: [docs/ko/releases/RELEASE_NOTES_v13.8.0.md](./releases/RELEASE_NOTES_v13.8.0.md).

# [v13.7.2 - Living Synthesis] (2026-11-24)

Rust 워크스페이스 전체의 임포트를 정본 도메인 모듈 경로(`lattice_platform::<domain>::*`, `lattice_agent::<group>::*`)로 전면 이전하고, `lattice-agent`·`lattice-platform`의 구형 평탄 호환성 `pub use` 맵과 마지막 미사용 `lattice_chat::crate_ready()` 스텁을 제거했습니다.

상세 릴리스 노트: [docs/ko/releases/RELEASE_NOTES_v13.7.2.md](./releases/RELEASE_NOTES_v13.7.2.md).

# [v13.7.1 - Living Synthesis] (2026-11-24)

`lattice-ingest`, `lattice-platform`, `lattice-retrieval`의 잔여 레거시 `forward()` 폴백과 미사용 호환 스텁을 모두 제거하고(`GraphWriter` 미연결 시 일관된 HTTP `503`), `local_files_replay.rs`에 `git init` 기반 `source: "git"` 및 503 통합 테스트를 추가했으며, 모든 벤치마크 문서를 보존한 채 `13.7.0` 이전 리뷰·계획·레거시 문서를 정리했습니다.

상세 릴리스 노트: [docs/ko/releases/RELEASE_NOTES_v13.7.1.md](./releases/RELEASE_NOTES_v13.7.1.md).

# [v13.7.0 - Living Synthesis] (2026-11-24)

`lattice-ingest`에 단일 라이터 `GraphWriter` 기반 네이티브 Rust Obsidian 볼트(`[[wikilink]]` `LINKS_TO` 엣지) 및 Notion/Git/Mail 연동 브릿지를 구현하고, Vite 8 / Vitest 5 설정을 네이티브 ESM `.mts`로 전환했으며, `v13.0.0` 미만 릴리스 노트를 정리하고 `ARCHITECTURE.md` / `docs/ko/ARCHITECTURE.md`를 `13.7.0` 기준으로 최신화했습니다.

상세 릴리스 노트: [docs/ko/releases/RELEASE_NOTES_v13.7.0.md](./releases/RELEASE_NOTES_v13.7.0.md).

# [v13.6.0 - Living Synthesis] (2026-11-24)

무의존성 네이티브 TypeScript 7 (`tsgo`) OpenAPI 타입 생성기(`scripts/generate_openapi_types.mjs`)를 도입해 남아 있던 TypeScript 5 의존성(`openapi-typescript@7.13.0`, `openapi-ts5`, `scripts/run_openapi_typescript.mjs`, `overrides`)을 완전히 제거하고, 5개의 대형 `tests/unit/test_cov_p1b_remaining*.py` 커버리지 묶음 파일을 `tests/unit/_runtime_test_fakes.py`와 9개의 도메인별 소형 단위 테스트 모듈로 분할했습니다.

상세 릴리스 노트: [docs/ko/releases/RELEASE_NOTES_v13.6.0.md](./releases/RELEASE_NOTES_v13.6.0.md).

# [v13.5.0 - Living Synthesis] (2026-11-24)

macOS(`omlx` Homebrew 및 무-Homebrew universal2 휠 폴백, `local_mlx`), Windows(`winget` Ollama / llama.cpp / LM Studio 및 `pip` 폴백), Linux(Ollama 설치 스크립트 및 `pip` / `vllm`) 전 환경 원클릭 **Qwen3.8 27B 4-bit** 설치·다운로드·실행과 실시간 가용 메모리 압박 자동 튜닝을 통합하고, 워크스페이스 타입체커를 **TypeScript 7.0.2 (`tsgo`)**로, `scripts/check_i18n_literals.mjs`를 `@babel/parser`로 전환했으며, `engine_adapters.py` 및 `model_loading.py` 모듈 리팩토링과 데드코드·불필요 테스트 정리를 완료했습니다.

상세 릴리스 노트: [docs/ko/releases/RELEASE_NOTES_v13.5.0.md](./releases/RELEASE_NOTES_v13.5.0.md).

# [v13.4.2 - Living Synthesis] (2026-11-24)

계층형 RAM/SSD KV 캐시를 지원하는 **oMLX (`jundot/omlx`)** 엔진을 통합하고, **`mlx-community/Qwen3.8-27B-4bit`** 모델을 32GB+ Apple Silicon Mac의 추천 및 다운로드 가능 카탈로그에 등록했습니다.

상세 릴리스 노트: [docs/ko/releases/RELEASE_NOTES_v13.4.2.md](./releases/RELEASE_NOTES_v13.4.2.md).

# [v13.4.1 - Living Synthesis] (2026-11-23)

프론트엔드(`typescript@7.0.2`, `vitest@5.1.1`, `jsdom@31`, `@testing-library/jest-dom@8`), Rust 워크스페이스(`axum@0.8`, `rusqlite@0.43`, `sha2@0.11`, `scrypt@0.12`, `fancy-regex@0.20`, `zip@9`, `reqwest@0.13`, `ed25519-dalek@3`, `notify@8`), Tauri 데스크톱(`tauri@2.11.6`, `plist@1.8.0`, `time@0.3.55`)의 메이저/브레이킹 의존성 업그레이드 호환성을 해결하고 Vitest 100% 커버리지 및 전체 워크스페이스 빌드를 복구했습니다.

상세 릴리스 노트: [docs/ko/releases/RELEASE_NOTES_v13.4.1.md](./releases/RELEASE_NOTES_v13.4.1.md).

# [v13.4.0 - Living Synthesis] (2026-11-20)

모델별 코드 분기 없는 범용 적응형 에이전트 하네스(`probe.rs` 3턴 마이크로 프로브, 관찰 우선 도구 순위화·한/영 도메인 분류, 결정론적 UI 바운딩 박스 중앙 좌표 해석 `ui_resolve.rs`, JSON 구문 복구·ChatML 프리필, 활성/폐기 증거 분리 `rag_grounding.rs`), 모듈화된 네이티브 웹 하네스·컴퓨터 유즈 브리지 정합성, 로컬 `Qwen2.5-0.5B-Instruct-Q4_K_M` 대상 25개 태스크 실측 통과율을 `28.0%`(Raw) / `24.0%`(Standard) → `84.0%`(`+56.0 pp` / 상대 `+200.0%`, 컴퓨터 유즈 `100.0%`, RAG 그라운딩 `100.0%`, 지연 시간 `-63.3%`)로 향상시킨 실증 벤치마크, 그리고 100% 한/영 양방향 GitHub 마크다운 대등성을 적용했습니다.

상세 릴리스 노트: [docs/ko/releases/RELEASE_NOTES_v13.4.0.md](./releases/RELEASE_NOTES_v13.4.0.md) · 벤치마크 보고서: [docs/ko/ADAPTIVE_HARNESS_BENCHMARK.md](./ADAPTIVE_HARNESS_BENCHMARK.md).

# [v13.3.1 - Living Synthesis] (2026-11-19)

시맨틱 임베딩 계약용 2단계 비동기 증분 청크 동기화(`plan_incremental_sync` + 비동기 `/worker/embed` + `commit_incremental_sync`, `watch_deliver.rs`), `AnswerProof.tsx` 주장 검증 텔레메트리(`claim_verification`) 및 `BrainIntelligencePanel.tsx` 개체 통합·시간축 요약 카드(한/영 i18n · Vitest 100%), 전 스택 주간 `.github/dependabot.yml` 자동 감시를 적용했습니다.

상세 릴리스 노트: [docs/ko/releases/RELEASE_NOTES_v13.3.1.md](./releases/RELEASE_NOTES_v13.3.1.md).

# [v13.3.0 - Living Synthesis] (2026-11-19)

v13.2.0 핵심 서브시스템(`post_idempotent_compute`, `verify_grounding_and_abstention`, `entity_resolution`·`temporal`, `DebouncedWatchEngine`·`sync_document_incremental`, `DurableWorkflowStore`)을 실제 HTTP·채팅·감시·워크플로 경로에 전면 연결하고, `worker_resilience.py` 동시성/취소/스트리밍 타임아웃 버그 및 `ingest.rs` 임시 파일 충돌·오버레이 오류 침묵을 수정했으며, Rust 비테스트 `.unwrap()` 0건 달성, 중복 `unsafe libc` 시간 블록 7개 단일화(`clock.rs`), 전 스택 800줄 초과 프로덕션 파일 0건 달성(Vitest 100% 유지)을 완료했습니다.

상세 릴리스 노트: [docs/releases/RELEASE_NOTES_v13.3.0.md](../releases/RELEASE_NOTES_v13.3.0.md).

# [v13.2.1 - Living Synthesis] (2026-11-19)

Rust 전 워크스페이스(`lattice-auth`, `lattice-core`, `lattice-ingest`, `lattice-retrieval`, `lattice-agent`, `lattice-platform`, `lattice-host`)와 Python(`model_capability_registry.py`, `worker_compute.py`), Frontend(`brainData.ts`) 대형 모듈 분할 리팩터링, 전 스택 미참조 데드 코드 및 구형 오비탈 Brain 레이어 제거(Vitest 100% 유지), Ubuntu `t64` 시각 회귀 CI 미러 페일오버 하드닝을 적용했습니다.

상세 릴리스 노트: [docs/releases/RELEASE_NOTES_v13.2.1.md](../releases/RELEASE_NOTES_v13.2.1.md).

# [v13.2.0 - Living Synthesis] (2026-11-19)

이중 시간축(Bi-temporal) 지식 그래프·시점 조회 및 비파괴 한/영 개체 통합(`temporal.rs`, `entity_resolution.rs`), 주장 단위 근거 검증·기권(`grounding.rs`) 및 검색/생성 분리형 모델 무관 RAG 평가(`rag_eval_grounded.rs`), OS 이벤트 디바운스 감시·청크 단위 증분 재임베딩·표/페이지/OCR 구조 보존 수집(`incremental_watch.rs`, `incremental_sync.rs`, `document_structure.rs`), 원자적 체크포인트·멱등 원장·재시작 승인 복구·롤백을 갖춘 내구성 DAG 에이전트 워크플로 엔진(`kernel/workflow.rs`), 워커 멱등 재시도·타임아웃 격리(`worker.rs`, `worker_resilience.py`)·클라우드 비밀 파일 차단(`boundary.rs`)·정직한 그래프 상태 프로브(`onedoor.rs`, `audit.rs`) 및 Section 16 종단간 제품 검증 스위트(`roadmap_section16_suite.rs`, 16.1–16.4)를 적용했습니다.

상세 릴리스 노트: [docs/releases/RELEASE_NOTES_v13.2.0.md](../releases/RELEASE_NOTES_v13.2.0.md).

# [v13.1.0 - Living Synthesis] (2026-11-18)

지식 그래프 인터랙티브 노드 인스펙터·단일 조회 2-Hop 이웃 추론 합성(`neighborhood_synthesis.rs`, `GraphNodeInspector.tsx`, `MultiHopSynthesisView.tsx`)·상충 필터 및 원클릭 상충 기억 제안/해결(`ContradictionInspector.tsx`), 채팅 답변 원클릭 **Brain에 기억하기** 승격·인라인 변경 제안 diff 승인/거절(`InlineStagedProposals.tsx`)·실시간 도구 단계 표시 및 출처 모달 그래프 점프(`AnswerProof.tsx`), 임베딩 진행률 바·즉시 인덱싱 드레인(`POST /api/index/drain`)·`stale_embedder` 보존(`health.rs`), 모델 다운로드 바이트/파일 표시 및 협력적 취소(`InstallScreen.tsx`, `ModelsPanel.tsx`), `⌘K` 지식 노드 포커스·작업공간 즉시 쿼리 무효화 및 한/영 무누수 로컬라이제이션(Vitest 100%)을 적용했습니다.

상세 릴리스 노트: [docs/releases/RELEASE_NOTES_v13.1.0.md](../releases/RELEASE_NOTES_v13.1.0.md).

# [v13.0.0 - Sovereign Synthesis] (2026-11-18)

크로스 도메인 결합 제거(`lattice_core::redact`, `lattice_agent::seam::worker`, `modelops`→`http_reply`, `domain_boundaries.rs`)와 통합 Rust 서버 i18n(`lattice-auth`→`lattice_core::messages`, `check_server_i18n.mjs` 146개 카탈로그 검증), 다단계(2-Hop) 지식 그래프 추론 체인·매개 개념·상충 감지 합성(`reasoning_paths.rs`, `MultiHopSynthesisView.tsx`), 단일 트랜잭션 리뷰 스토어·폴더 재구성 원클릭 되돌리기(`reversible_reorganization.rs`, `ReviewCard.tsx`) 및 대화 문서 세션 영속화(`chat_document_sessions.json`), 프론트엔드 API 모듈 분할(`streaming.ts`, `transfers.ts`, Vitest 100%) 및 Tauri 백엔드 모듈화를 적용했습니다.

상세 릴리스 노트: [docs/releases/RELEASE_NOTES_v13.0.0.md](../releases/RELEASE_NOTES_v13.0.0.md).

---

# 릴리스 노트 색인

이 저장소는 **13.0.0부터 14.4.0까지**의 공개 릴리스 이력을 유지합니다(최신 Rust 호스트 + 도메인 그룹 아키텍처 세대). `SECURITY.md`가 `14.x` 및 `13.x` 세대를 지원하며, 이 색인 역시 동일한 경계를 따릅니다.

## 릴리스 목록 (13.0.0+)

- [v14.4.0 - Measured Retrieval](./releases/RELEASE_NOTES_v14.4.0.md)
- [v14.3.0 - Temporal Synthesis](./releases/RELEASE_NOTES_v14.3.0.md)
- [v14.2.0 - Temporal Synthesis](./releases/RELEASE_NOTES_v14.2.0.md)
- [v14.1.2 - Living Synthesis](./releases/RELEASE_NOTES_v14.1.2.md)
- [v14.1.1 - Living Synthesis](./releases/RELEASE_NOTES_v14.1.1.md)
- [v14.1.0 - Living Synthesis](./releases/RELEASE_NOTES_v14.1.0.md)
- [v14.0.0 - Living Synthesis](./releases/RELEASE_NOTES_v14.0.0.md)
- [v13.9.0 - Living Synthesis](./releases/RELEASE_NOTES_v13.9.0.md)
- [v13.8.0 - Living Synthesis](./releases/RELEASE_NOTES_v13.8.0.md)
- [v13.7.2 - Living Synthesis](./releases/RELEASE_NOTES_v13.7.2.md)
- [v13.7.1 - Living Synthesis](./releases/RELEASE_NOTES_v13.7.1.md)
- [v13.7.0 - Living Synthesis](./releases/RELEASE_NOTES_v13.7.0.md)
- [v13.6.0 - Living Synthesis](./releases/RELEASE_NOTES_v13.6.0.md)
- [v13.5.0 - Living Synthesis](./releases/RELEASE_NOTES_v13.5.0.md)
- [v13.4.2 - Living Synthesis](./releases/RELEASE_NOTES_v13.4.2.md)
- [v13.4.1 - Living Synthesis](./releases/RELEASE_NOTES_v13.4.1.md)
- [v13.4.0 - Living Synthesis](./releases/RELEASE_NOTES_v13.4.0.md)
- [v13.3.1 - Living Synthesis](./releases/RELEASE_NOTES_v13.3.1.md)
- [v13.3.0 - Living Synthesis](../releases/RELEASE_NOTES_v13.3.0.md)
- [v13.2.1 - Living Synthesis](../releases/RELEASE_NOTES_v13.2.1.md)
- [v13.2.0 - Living Synthesis](../releases/RELEASE_NOTES_v13.2.0.md)
- [v13.1.0 - Living Synthesis](../releases/RELEASE_NOTES_v13.1.0.md)
- [v13.0.0 - Sovereign Synthesis](../releases/RELEASE_NOTES_v13.0.0.md)

## 정본 이력

버전별 정본 릴리스 이력은 아래 문서에서 관리됩니다.

- [RELEASE.md](./RELEASE.md)
- [docs/CHANGELOG.md](../CHANGELOG.md)
