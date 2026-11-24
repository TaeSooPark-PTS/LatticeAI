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

## v14.0.0 — Living Synthesis (2026-11-25)

풀스택 보안·동시성·패키징·CI/CD 고도화를 완료했습니다: `lattice-auth`를 버전화 `$scrypt$` `Result` 기반 해싱, 상한 메모리 레이트 리미팅 및 통합 HTTP `429` + `Retry-After` 응답, 영문 표준 CSRF 거부 메시지로 강화하고, `lattice-core`에서 `unsafe` `libc` 시계 호출을 순수 Rust UTC 달력 연산으로 완전 제거했습니다. `lattice-ingest`/`lattice-chat`의 블로킹 파일 I/O를 `spawn_blocking`으로 오프로딩하고, `cloud/budget`·`NativeHnswIndex` 상한 캐시, `lattice-jobs` 부분 드레인 보존 및 `/host/jobs` 루프백/Bearer 가드, fail-closed `lattice-host` 프록시 클라이언트와 `EnvOverlayProbe`를 적용했습니다. Python 워커의 Apple Silicon MLX extra 플랫폼 가드와 Ruff 보안 규칙을 복원하고, TypeScript 7 엄격 플래그 및 `AppRouteView.tsx` 분리(Vitest 100%), 직렬화된 Tauri 게이트웨이 생명주기 및 `127.0.0.1:4825` 고정 CSP, VS Code·브라우저 확장 보안 강화를 마쳤으며, CI에 실제 `windows-latest` Rust 워크스페이스 테스트·`macos-latest` Tauri 테스트·다이제스트 고정 멀티스테이지 `Dockerfile` 스모크 검증을 추가하고 아키텍처 문서(`ARCHITECTURE.md`, `docs/architecture.md`, 한국어 미러 및 크레이트 맵)를 `14.0.0` 기준으로 최신화했습니다.

릴리스 노트는 [docs/ko/releases/RELEASE_NOTES_v14.0.0.md](./releases/RELEASE_NOTES_v14.0.0.md).

빌드 산출물은 `dist/ltcai-14.0.0-py3-none-any.whl`, `dist/ltcai-14.0.0.tar.gz`, `ltcai-14.0.0.tgz`, `dist/ltcai-14.0.0.vsix`, `src-tauri/target/release/bundle/dmg/Lattice AI_14.0.0_aarch64.dmg` 입니다.

## v13.9.0 — Living Synthesis (2026-11-24)

PR #69 보안·무결성 지적 6건(워크스페이스 범위 무결성 검사·복구 및 관리자 전용 전역 모드, `Store::write_conn` 단일 라이터 풀 강제, 모든 노드/엣지/교정/병합 대상의 워크스페이스 소유권 403 검증, Windows 핸들 고정 TOCTOU 방지 `safe_io`, 실모델 RAG 평가 주장 근거·인용 검증 게이트, `/host/diagnostics` 포스처 게이트)을 모두 해결하고, 16개 디지털 브레인 확장 라우트를 `brain_api::MOUNTED`와 `memory_brain.json`에 통합하며 5개 대화형 React 브레인 거버넌스 패널과 야간 실모델 RAG 평가 CI(`.github/workflows/nightly-rag-eval.yml`)를 추가했습니다.

릴리스 노트는 [docs/ko/releases/RELEASE_NOTES_v13.9.0.md](./releases/RELEASE_NOTES_v13.9.0.md).

빌드 산출물은 `dist/ltcai-13.9.0-py3-none-any.whl`, `dist/ltcai-13.9.0.tar.gz`, `ltcai-13.9.0.tgz`, `dist/ltcai-13.9.0.vsix`, `src-tauri/target/release/bundle/dmg/Lattice AI_13.9.0_aarch64.dmg` 입니다.

## v13.8.0 — Living Synthesis (2026-11-24)

디지털 브레인 플랫폼 통합 고도화 로드맵(P0–P3)을 완수했습니다: 결정론적 픽스처와 실제 로컬/클라우드 모델 평가를 분리하고 320개 질의 버전 데이터셋(`datasets/rag_eval_v1.json`)을 추가한 진정성 있는 RAG 평가 프레임워크, 장애 주입 검증 및 자동 백업·안전 복구를 갖춘 지식 그래프 무결성 검사기(`lattice-core::integrity`), 타입별 동음이의어 차단·퍼지 점수·선택적 롤백을 지원하는 엔티티 해석(`entity_resolution_ext`), OS 수준 `O_NOFOLLOW` FD 샌드박스(`safe_io.rs`) 및 클라우드 이그레스 PII/비밀 마스킹, 섀도우 재색인·컷오버·롤백을 지원하는 임베딩 모델 레지스트리, 최소 권한 5역할 멀티에이전트 협업 엔진(`multi_agent.rs`), 지식 학습 루프(`learning_loop.rs`), OS 감시 파일 이동·일시중지, `국가 → 제조사 → 실행 위치` 모델 출처 UX, 워커 워치독 및 마스킹 `/host/diagnostics`, 토픽 클러스터/지식 공백/비디오 모달리티/감사 보존 정책/오프보딩 취소 영수증을 모두 통합했습니다.

릴리스 노트는 [docs/ko/releases/RELEASE_NOTES_v13.8.0.md](./releases/RELEASE_NOTES_v13.8.0.md).

빌드 산출물은 `dist/ltcai-13.8.0-py3-none-any.whl`, `dist/ltcai-13.8.0.tar.gz`, `ltcai-13.8.0.tgz`, `dist/ltcai-13.8.0.vsix`, `src-tauri/target/release/bundle/dmg/Lattice AI_13.8.0_aarch64.dmg` 입니다.

## v13.7.2 — Living Synthesis (2026-11-24)

Rust 워크스페이스 전체(`lattice-host`, `lattice-chat`, `lattice-platform`, `lattice-agent`, `lattice-retrieval`, `src-tauri` 및 모든 통합 테스트)의 임포트를 정본 도메인 경로(`lattice_platform::<domain>::*`, `lattice_agent::<group>::*`)로 이전하고, `lattice-agent`·`lattice-platform`의 구형 평탄 호환성 `pub use` 맵과 마지막 미사용 `lattice_chat::crate_ready()` 스텁을 제거했습니다.

릴리스 노트는 [docs/ko/releases/RELEASE_NOTES_v13.7.2.md](./releases/RELEASE_NOTES_v13.7.2.md).

빌드 산출물은 `dist/ltcai-13.7.2-py3-none-any.whl`, `dist/ltcai-13.7.2.tar.gz`, `ltcai-13.7.2.tgz`, `dist/ltcai-13.7.2.vsix`, `src-tauri/target/release/bundle/dmg/Lattice AI_13.7.2_aarch64.dmg` 입니다.

## v13.7.1 — Living Synthesis (2026-11-24)

`lattice-ingest`, `lattice-platform`, `lattice-retrieval`의 잔여 레거시 `forward()` 폴백과 미사용 호환 스텁을 모두 제거해 `GraphWriter` 미연결 시 일관된 HTTP `503`을 반환하도록 정리하고, `local_files_replay.rs`에 `git init` 기반 `source: "git"` 및 503 통합 테스트를 추가했으며, 모든 벤치마크 문서를 보존한 채 `13.7.0` 이전 리뷰·계획·레거시 문서를 정리했습니다.

릴리스 노트는 [docs/ko/releases/RELEASE_NOTES_v13.7.1.md](./releases/RELEASE_NOTES_v13.7.1.md).

빌드 산출물은 `dist/ltcai-13.7.1-py3-none-any.whl`, `dist/ltcai-13.7.1.tar.gz`, `ltcai-13.7.1.tgz`, `dist/ltcai-13.7.1.vsix`, `src-tauri/target/release/bundle/dmg/Lattice AI_13.7.1_aarch64.dmg` 입니다.

## v13.7.0 — Living Synthesis (2026-11-24)

`lattice-ingest`에 `GraphWriter` 기반 네이티브 Rust Obsidian 볼트(`[[wikilink]]` `LINKS_TO` 엣지) 및 `notion`/`git`/`mail` 연동 브릿지를 구현하고, `vite.config.ts`/`vitest.config.ts`를 네이티브 ESM `.mts`로 전환해 Vite 8 경고를 제거했으며, `v13.0.0` 미만 릴리스 노트를 정리하고 `ARCHITECTURE.md` 및 `docs/ko/ARCHITECTURE.md`를 `13.7.0` 기준으로 최신화했습니다.

릴리스 노트는 [docs/ko/releases/RELEASE_NOTES_v13.7.0.md](./releases/RELEASE_NOTES_v13.7.0.md).

빌드 산출물은 `dist/ltcai-13.7.0-py3-none-any.whl`, `dist/ltcai-13.7.0.tar.gz`, `ltcai-13.7.0.tgz`, `dist/ltcai-13.7.0.vsix`, `src-tauri/target/release/bundle/dmg/Lattice AI_13.7.0_aarch64.dmg` 입니다.

## v13.6.0 — Living Synthesis (2026-11-24)

`openapi-typescript@7.13.0` 및 `openapi-ts5` 별칭을 무의존성 네이티브 TypeScript 7 (`tsgo`) OpenAPI 타입 생성기(`scripts/generate_openapi_types.mjs`)로 완전 대체해 `package-lock.json`에서 TypeScript 5 패키지를 모두 제거하고, 5개의 대형 `tests/unit/test_cov_p1b_remaining*.py` 커버리지 묶음 파일을 공용 `tests/unit/_runtime_test_fakes.py` 기반 9개 도메인별 소형 단위 테스트 모듈로 분할했습니다.

릴리스 노트는 [docs/ko/releases/RELEASE_NOTES_v13.6.0.md](./releases/RELEASE_NOTES_v13.6.0.md).

빌드 산출물은 `dist/ltcai-13.6.0-py3-none-any.whl`, `dist/ltcai-13.6.0.tar.gz`, `ltcai-13.6.0.tgz`, `dist/ltcai-13.6.0.vsix`, `src-tauri/target/release/bundle/dmg/Lattice AI_13.6.0_aarch64.dmg` 입니다.

## v13.5.0 — Living Synthesis (2026-11-24)

**macOS**(`omlx` Homebrew 및 무-Homebrew universal2 휠 폴백, `local_mlx`), **Windows**(`winget` Ollama / llama.cpp / LM Studio 및 `pip` `llama-cpp-python[server]` 폴백), **Linux**(공식 Ollama 설치 스크립트 및 `pip` / `vllm`) 전 환경 원클릭 **Qwen3.8 27B 4-bit** 설치·다운로드·실행, 실시간 가용 메모리 압박 자동 튜닝(`host_available_ram_gb`, `omlx_serve_args`, `llamacpp_serve_args`), **TypeScript 7.0.2 (`tsgo`)** 및 `@babel/parser` i18n AST 전환, `engine_adapters.py` / `model_loading.py` 모듈화 및 데드코드·불필요 테스트 정리를 적용했습니다.

릴리스 노트는 [docs/ko/releases/RELEASE_NOTES_v13.5.0.md](./releases/RELEASE_NOTES_v13.5.0.md).

빌드 산출물은 `dist/ltcai-13.5.0-py3-none-any.whl`, `dist/ltcai-13.5.0.tar.gz`, `ltcai-13.5.0.tgz`, `dist/ltcai-13.5.0.vsix`, `src-tauri/target/release/bundle/dmg/Lattice AI_13.5.0_aarch64.dmg` 입니다.

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
