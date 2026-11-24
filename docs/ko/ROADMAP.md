[![English](https://img.shields.io/badge/English-Switch-475569?style=flat-square)](../ROADMAP.md) [![한국어](https://img.shields.io/badge/한국어-현재_언어-2563eb?style=flat-square)](./ROADMAP.md)

# Lattice AI 갭 로드맵

> **상태: 참고 문서** — **v14.1.0** (2026-11-25) 기준으로 갱신된 시점별 갭 인벤토리입니다. 현재 릴리스 정본 문서가 아니므로 버전 게이트의 구속을 받지 않습니다.

기준 시점: **2026-11-25**, **14.1.0 — Living Synthesis** 트리 기준. 이 문서는 [`FEATURE_STATUS.md`](./FEATURE_STATUS.md), [`docs/SECURITY_AUDIT.md`](../SECURITY_AUDIT.md), 그리고 [`docs/ko/releases/README.md`](./releases/README.md)의 `v13.0.0+` 릴리스 노트에서 추려낸 실제 잔여 엔지니어링 과제를 추적합니다. 크레이트 소유권 지도는 [`rust/lattice-agent/ARCHITECTURE.md`](../../rust/lattice-agent/ARCHITECTURE.md)와 [`rust/lattice-platform/ARCHITECTURE.md`](../../rust/lattice-platform/ARCHITECTURE.md), 기여자 가이드는 [`docs/ko/DEVELOPMENT.md`](./DEVELOPMENT.md)를 참고하세요.

난이도: **S** 집중 변경 1건 · **M** 수직 슬라이스 · **L** 복합 프로그램(자격 증명, 모델 하네스, 인덱스 포맷 또는 플랫폼 포트).

---

## 최근 완료 항목 (`v13.0.0` – `v14.1.0`) — 다시 열지 말 것

| 항목 | 출시 버전 | 해결 내용 |
| --- | --- | --- |
| Self-Model 및 브레인 거버넌스 UI 패널 | `13.1.0` / `13.9.0` | `SelfModelPanel`과 통합 `brain_api` OpenAPI 타입에 연결된 5개 지연 로드 브레인 거버넌스 패널(`IntegrityRepairPanel`, `EntityMergePanel`, `KnowledgeCorrectionsPanel`, `EmbeddingRegistryPanel`, `ClustersGapsPanel`)을 추가했습니다. |
| 협조적 모델 다운로드 취소 | `13.1.0` | `InstallScreen.tsx`와 `ModelsPanel.tsx`에 종단간 `AbortController` + HTTP `499` 취소와 바이트/파일명 SSE 진행률을 추가했습니다. |
| 서버 측 i18n 래칫 및 Rust 카탈로그 | `13.0.0` | 6/6 워커 라우터를 모두 잠그고 146개 Rust 서버 메시지를 `lattice_core::messages`로 통합해 `scripts/check_server_i18n.mjs`로 검증합니다. |
| 원클릭 되돌리기 가능한 폴더 재구성 | `13.0.0` | `reversible_reorganization.rs`와 `ReviewCard.tsx`가 폴더 정리 승인 시 원클릭 Undo 제안을 자동 생성합니다. |
| 단일 트랜잭션 검토 저장소 변경 & 영속 문서 세션 | `13.0.0` / `14.0.0` | 검토 상태 변경을 단일 트랜잭션으로 통합하고 `chat_document_sessions.json`을 논블로킹 `spawn_blocking`으로 영속화했습니다. |
| 브라우저 확장 서버 언어 동기화 | `13.1.0` | 브라우저 로캘에만 의존하지 않고 서버 언어 설정과 동기화하도록 개선했습니다. |
| 상한 HNSW·클라우드 예산 캐시 & `NativeHnswIndex` | `13.8.0` / `14.0.0` | 포이즈닝 복구를 갖춘 순수 Rust `NativeHnswIndex` LRU 캐시(32개 그래프)와 상한 `cloud/budget.rs`(4,096 스코프)를 도입했습니다. |
| 크로스 도메인 결합 제거 & 구형 호환성 맵 삭제 | `13.0.0` / `13.7.2` | `lattice-agent`와 `lattice-platform`의 평탄 크레이트 루트 `pub use` 호환성 맵을 삭제하고 `domain_boundaries.rs`로 정본 도메인 임포트를 강제했습니다. |
| 범용 적응형 에이전트 하네스(0.5B–7B 안정성) | `13.4.0` | 모델별 분기 없이 `Qwen2.5-0.5B-Instruct-Q4_K_M` 25개 태스크 실측 통과율을 `28.0% → 84.0%`로 끌어올렸습니다(`docs/ko/ADAPTIVE_HARNESS_BENCHMARK.md`). |
| 실제 Windows Rust CI, macOS Tauri CI, Docker 스모크 & PR 시각 스모크 | `14.0.0` / `14.1.0` | `.github/workflows/ci.yml`에 `rust-windows`(`windows-latest`), `tauri-check`(`macos-latest`), 다이제스트 고정 `docker-smoke`, PR + `main` Playwright `visual` 스모크를 추가했습니다. |
| Rust ↔ Python UTC 편차 & Tauri 프로덕션 포트 이탈 | `14.1.0` | 다중 타임존에서 오프셋 없는 UTC(`YYYY-MM-DDTHH:MM:SS`)로 타임스탬프를 통일하고 `PortPolicy::PinnedProduction`으로 프로덕션 Tauri 포트를 `4825`에 고정했습니다. |
| 풀스택 정적 분석 강화(`BLE`, McCabe <= 20, TS `noUncheckedIndexedAccess`) | `14.1.0` | Python 전역 포괄적 `except Exception`을 제거하고 McCabe 상한을 `20`으로 낮추며 457건의 `noUncheckedIndexedAccess` TypeScript 오류를 Vitest 100%로 해결했습니다. |

---

## P1 — 핵심 품질 및 공급망 갭

| 항목 | 남은 이유 | 난이도 | 우선순위 | 제안 경로 |
| --- | --- | --- | --- | --- |
| **소형 모델(0.5B–2B) 개방형 과제 내용 품질** | 기계적 도구 호출과 그라운딩은 해결되었고(0.5B 기준 `84.0%`) fail-closed 검증기가 취약한 출력을 `NEEDS_REVIEW`로 격리하지만, 복합 다중 파일 요약에서는 여전히 유창하지만 불완전한 서술이 나올 수 있습니다. | L | **P1** | 모델별 분기 없이 `probe.rs`와 `rag_eval_v1.json`으로 지속 실측하며 구조화 근거 스캐폴딩을 고도화합니다. |
| **Hugging Face 런타임 커밋 `revision=` 고정 (Bandit `B615`)** | `scripts/verify_hf_model_registry.py`가 Hub 메타데이터를 정적 검증하고 `EmbeddingIndexContract`가 `model_revision`을 기록하지만, `model_runtime/download.py`와 `embedding_providers/text.py`는 미지정 시 기본 리비전 고정을 아직 강제하지 않습니다. | M | **P1** | 카탈로그 매니페스트에 검증된 Hub 커밋 SHA를 저장하고 기본으로 `revision=` + SHA-256 검증을 적용하되 사용자 지정 리포용 옵트인 오버라이드를 유지합니다. |

---

## P2 — 주요 플랫폼 및 검증 갭

| 항목 | 남은 이유 | 난이도 | 우선순위 | 제안 경로 |
| --- | --- | --- | --- | --- |
| **기본 서명·공증 macOS DMG** | `.github/workflows/release.yml`은 `APPLE_CERTIFICATE` 시크릿 구성 시 Developer ID 서명과 공증을 수행하지만, 미구성 시 ad-hoc 서명(최초 실행 시 Gatekeeper 허용 필요)으로 폴백합니다. | M | **P2** | 저장소에 Apple Developer ID 및 공증 시크릿을 구성하고 `release:validate`에 선택적 `--require-signed` 검사를 추가합니다. |
| **과금 `api_key` 라이브 완료 계약 테스트** | `api_key` 모드는 무과금 `GET /models` 라이브 프로브로 검증하고 실패 시 fail-closed 하며, 실제 채팅 E2E는 무과금 `cli_oauth`(`agy`/`grok`)와 목 계약 테스트로 커버됩니다. | S | **P2** | 전용 CI API 예산 확보 시 수동/예약 라이브 완료 스모크 1건을 추가하고 요청 단위 `local_only` 우선을 유지합니다. |
| **`LATTICEAI_VECTOR_INDEX` 기본값 전환 (`brute` → `hnsw`)** | 512개 이상 벡터 + 워커 연결 시 `hnsw+rescore`가 자동 시도되고 `NativeHnswIndex`도 LRU 상한을 갖추었지만, 장시간 연속 수집·삭제 환경의 recall@10 재검증 전까지 환경 변수 기본값은 `brute`로 유지합니다. | M | **P2** | `rag_eval_v1.json` 기반 연속 수집·삭제 recall@10 벤치마크 검증 후 기본값을 전환합니다. |
| **야간 실모델 RAG 평가 전용 러너** | `.github/workflows/nightly-rag-eval.yml`이 구성되어 엔드포인트 미설정 시 정직하게 `skipped`/`unavailable`을 보고하지만, 호스티드 GitHub 러너에는 Apple Silicon MLX/로컬 GPU가 없습니다. | L | **P2** | 셀프 호스티드 GPU/Apple Silicon 러너 또는 관리형 평가 엔드포인트를 연결해 야간 실모델 점수를 자동 발행합니다. |

---

## P3 — 정직한 개선 과제 및 선택 확장

| 항목 | 남은 이유 | 난이도 | 우선순위 | 제안 경로 |
| --- | --- | --- | --- | --- |
| **승인 게이트 기반 원본 이미지 뷰어** | `Image` 인용은 동의 없는 디스크 노출을 피하기 위해 96px 인라인 `data:image/` 썸네일(24 KB 상한)만 표시합니다. | M | P3 | 무승인 미리보기로 인라인 썸네일을 유지하면서 `/local/serve` 기반 명시적 승인 원본 미리보기 모달을 검토합니다. |
| **Self-Model 추출용 선택적 LLM `refiner`** | 현재 한/영 구문 테이블 추출은 모든 후보가 검토함을 거치므로 안전하지만, 비정형 표현을 놓칠 수 있습니다. | M | P3 | 기능 플래그 뒤 선택적 로컬 모델 `refiner`를 연결하되 제안 우선 승인을 유지합니다. |
| **Self-Model `open_keys`의 `snoozed` 정합성** | 현재 `pending` 제안만 중복 체크하므로 보류(snoozed) 항목과의 관계를 명시 결정해야 합니다. | S | P3 | 회귀 테스트와 함께 `snoozed`가 중복 제안을 억제할지 명시적으로 결정합니다. |
| **전용 Brain Network 공유 UI** | X25519/Ed25519 선택 서브그래프 공유는 `LATTICEAI_BRAIN_NETWORK=1` 아래 검토함으로 동작하며 독립 피어 관리 화면은 아직 없습니다. | M | P3 | 다중 기기 공유가 프로토타입 단계를 벗어날 때 Brain 내 전용 피어/공유 서랍을 추가합니다. |
| **PostgreSQL / pgvector 라이브 마이그레이션** | SQLite WAL이 정본 단일 라이터이며 Postgres 내보내기/상태와 `postgres-integration.yml`은 `Planned` 상태입니다. | L | P3 | 다중 노드 조직 배포 수요가 발생할 때 구현하되, 무설정 로컬 기본값으로 SQLite를 유지합니다. |
| **네이티브 OS 포인터 액추에이터** | 6개 포인터 도구는 옵트인 `pip install "ltcai[pointer]"`(`pyautogui`)로 Python 워커에서 실행됩니다. | L | P3 | 크로스플랫폼 네이티브 Rust 접근성/입력 계층이 우선순위가 될 때까지 옵트인 워커 실행을 유지합니다. |
| **양자화 int8 벡터 백엔드 효율** | 10k 행 기준 SQLite 행 페치가 RSS 대부분을 차지해 메모리 절감 대비 `brute`보다 약 2.2배 느립니다. | L | P3 | 향후 디스크 상주 인덱스용 표현으로만 보류하고 `brute`/`hnsw`보다 권장하지 않습니다. |

---

## 설계 경계 — 닫아야 할 버그가 아님

아래 항목은 미해결 결함이 아니라 의도적인 제품 경계입니다:

- **텔레그램 브리지 및 SSO/OIDC 로그인 콜백**: `11.6.0`에서 제품 서버가 Rust(`lattice-host`)로 이전되며 은퇴했습니다. 비밀번호 로그인, 세션, 레이트 리미트, CSRF, SSO 설정 표면은 `lattice-auth`에 네이티브로 유지됩니다.
- **로컬 내보내기 전용 상호운용 브리지**: Notion 내보내기, 로컬 Git 로그, 로컬 `.eml`/`.ics` 파일만 처리하여 외부 OAuth 토큰 저장이나 백그라운드 벤더 동기화 데몬을 피합니다.
- **`POST /knowledge-graph/ingest` 텍스트 전용 계약**: 바이너리 문서는 `/upload/document` → `/worker/parse` 경로를 사용합니다.
- **비전·음성 모델 및 `ffmpeg` 미번들**: 선택적 디코더가 없을 때 설치 용량을 키우거나 출력을 꾸며내지 않고 정직하게 `unavailable`로 보고합니다.
- **Python 90% 라인 커버리지 하한 vs. 프론트엔드 100%**: [`docs/ko/DEVELOPMENT.md`](./DEVELOPMENT.md)에 명시된 의도적 비대칭입니다.
- **동결된 패리티 골든 (`rust/fixtures/**/FROZEN.md`)**: 바이트 단위 호환성 보호를 위해 원본 그대로 유지하며 의도된 신규 동작은 새 키나 새 테스트로 검증합니다.

---

## 로드맵 갱신 규칙

코드와 테스트가 실제로 머지된 후에만 항목을 **최근 완료 항목**으로 옮기고 해당 릴리스 노트에 기록합니다. 작업 전 미리 이 문서를 고치지 않습니다.
