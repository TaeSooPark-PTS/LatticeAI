[![English](https://img.shields.io/badge/English-Switch-475569?style=flat-square)](../../FEATURE_STATUS.md) [![한국어](https://img.shields.io/badge/한국어-현재_언어-2563eb?style=flat-square)](./FEATURE_STATUS.md)

# Lattice AI 기능 현황 (v13.5.0)

> **상태: 표준 문서(Canonical)** — 현재 릴리스 기준의 실제 기능 상태와 한계를 기록합니다.

현재 릴리스: **14.0.0 — Living Synthesis**.

이 문서는 Lattice AI의 현재 기능 상태와 알려진 한계를 정직하게 설명합니다(대화형 지식 그래프 이웃 합성 및 모순 필터링, 원클릭 모순 제안/해결, 대화 인사이트 브레인 저장 및 인라인 변경 제안 승인, 실시간 임베딩 드레인 제어, 협조적 모델 다운로드 취소, 2단계 청크 증분 동기화, 주장 단위 근거 검증 텔레메트리, 개체 통합/이중 시간 요약 포함).
공개 변경 이력은 [`CHANGELOG.md`](./CHANGELOG.md)와 [`RELEASE.md`](../../RELEASE.md)에서 `v12.0.0` 이후 버전만 유지합니다.

---

## 제품 포지션

Lattice AI는 **로컬 우선(Local-first) 디지털 브레인**입니다. 영속적인 핵심 자산은 모델이 아니라 사용자의 브레인(대화, 문서, 의사결정, 기억, 출처, 지식 그래프 구조)이며, AI 모델은 그 브레인 위에서 언제든 교체할 수 있는 런타임입니다.

- **첫 화면은 대시보드가 아니라 조용한 작업 공간입니다**: 살아 있는 Brain과 컴포저가 첫 뷰포트에 함께 자리하고, 수집(문서 · 이미지 · 파일 · 폴더 · 노트 · 웹)은 컴포저 안에 통합됩니다. 일상 목적지(대화 · 자료 · 기억)와 관리 목적지(작업 · AI 모델 · 설정)가 명확히 분리되어 있습니다.
- **단일 관문(One Door) Rust 제품 서버**: `lattice-host`가 41개 패밀리, 422개 네이티브 오퍼레이션을 직접 서빙하고, Python AI 워커는 20개 연산 전용 라우트만 처리합니다. 모든 그래프 쓰기는 Rust `GraphWriter`가 단독으로 소유합니다.
- **정직한 근거와 제안 우선 거버넌스**: 검색은 문맥 품질과 주장 단위 근거 검증을 투명하게 노출하며, 기존 파일 수정·삭제, 자기모델 갱신, 모순 해결, 폴더 정리, 클라우드 지식 반영은 모두 검토함(Review Center) 제안을 거칩니다.
- **명시적 자율성과 네트워크 경계**: `설정 → 혼자 해도 되는 일`(`strict` / `trusted` / `bypass`)과 `설정 → 내 지식이 나가는 범위`(`local_only` / `cloud_allowed`)가 서로 독립적으로 동작하며, 서킷 브레이커와 민감 정보 필터는 어떤 모드에서도 해제되지 않습니다.

---

## 현재 기능 현황

| 영역 | 상태 | 설명 |
| --- | --- | --- |
| **단일 관문 Rust 제품 서버** | 현재 제공 | 9개 Rust 크레이트(`lattice-host` 포함)가 41개 패밀리, **422개 네이티브 오퍼레이션**을 서빙합니다. Python AI 워커는 추론·임베딩·추출·파싱·문서 렌더링·ASR·HNSW 사이드카 등 **20개 허용 라우트**만 처리하며, 허용 목록 밖 경로는 `404`를 반환합니다. 모든 그래프 쓰기는 `lattice-core::graph_write` 단일 쓰기 주체가 소유합니다. |
| **크레이트 도메인 구조** | 현재 제공 | `lattice-agent`는 6개 그룹(`kernel`/`parse`/`content`/`tools`/`surface`/`prompts`), `lattice-platform`은 7개 도메인(`workspaceos`/`toolsurface`/`governance`/`adminops`/`knowledge`/`modelops`/`shell`)으로 구조화되어 있으며 정적 경계 테스트와 호환성 맵을 유지합니다. |
| **Brain Home** | 현재 제공 | 데스크톱(1280×800)과 모바일(390×780) 첫 뷰포트에 살아 있는 Brain과 컴포저를 배치하며, 기억이 쌓일수록 성장 링이 표현됩니다. 하단 연속성 바(지난 대화 · 현황 · 기억 지도 · 기능)와 컴포저 내부 자료 추가 도크를 제공합니다. |
| **대화 및 파일 생성** | 현재 제공 | 로드된 모델이 없을 때 가짜 출력을 만들지 않으며, 문맥이 있을 때 출처 근거와 **주장 단위 근거 검증 스트립(`ClaimVerificationStrip`)**을 표시합니다. 대화 답변을 원클릭으로 **브레인에 저장**하거나, 인라인 변경 제안(`InlineStagedProposals`)을 바로 승인/거절할 수 있습니다. 파일 생성 요청(HTML, DOCX, PDF 등)은 거버넌스된 작업 공간 경로로 라우팅됩니다. |
| **지식 그래프 & 2-Hop 이웃 합성** | 현재 제공 | 대화형 그래프 탐색기(`DigitalBrainExplorer`), 노드 인스펙터(`GraphNodeInspector`), 2-Hop 추론 체인 및 이웃 합성(`neighborhood_synthesis.rs`, `MultiHopSynthesisView`), 모순 전용 필터링, `/`·`Escape` 단축키, `⌘K` 노드 포커스 이동을 지원합니다. |
| **이중 시간(Bi-temporal) 지식 & 연대기** | 현재 제공 | 노드와 엣지가 `valid_from`/`valid_to`/`superseded_by` 및 `recorded_at`/`superseded_at` 이중 시간 정보를 유지합니다(`temporal.rs`). 시간 경과에 따른 사실 변화를 `TemporalEvolution`, `ComplementaryRefinement`, `ActiveContradiction`으로 분류하며, **연대기(`#/chronicle`)** 화면에서 성장 곡선, 활동 히트맵, 과거 시점 되감기("그때 중요했던 개념")를 조회할 수 있습니다. |
| **비파괴 개체 통합 (Entity Resolution)** | 현재 제공 | `entity_resolution.rs`가 `EntityAliasBook`과 한국어 조사 제거를 통해 중복 개체를 탐지하고 비파괴 `SAME_AS` 엣지로 연결하며, `rollback_entity_consolidation`으로 안전하게 되돌릴 수 있습니다. 브레인 지능 패널의 `ConsolidationEpistemicSummary`에서 통합 현황과 시간 모순 배지를 확인할 수 있습니다. |
| **주장 단위 근거 검증 & RAG 평가** | 현재 제공 | `grounding.rs`가 답변의 각 주장을 인용 청크와 대조해 지지율(%), 인용 정밀도(%), 수치·극성 불일치, 대체된(`superseded`) 출처 유출을 검증하고, 근거가 부족할 때 기권(abstention)을 권고합니다. `rag_eval_grounded.rs`로 검색/생성 분리 벤치마크를 제공합니다. |
| **3중 하이브리드 검색 & GraphRAG** | 현재 제공 | BM25F 사이드카(`bm25_node_stats`, 2단계 한국어 조사 제거, CJK 바이그램) + 벡터 검색 + 1~2-Hop 지식 그래프 구조 확장을 RRF로 결합합니다. 포함관계 중복 제거와 근거 게이트를 통해 단일 어휘 일치만으로 근거가 있는 것처럼 오인하지 않습니다. |
| **섹션 트리 & 구조화 수집** | 현재 제공 | 문서의 제목 계층을 `Document ←PART_OF— Section —HAS_CHUNK→ Chunk` 트리로 저장해 인용 시 정확한 섹션 출처를 표시합니다. `document_structure.rs`로 표·페이지·슬라이드·시트·캡션 및 선택적 로컬 OCR/비전 오버레이 수집을 지원합니다. |
| **폴더 수집 · 디바운스 감시 · 청크 증분 동기화** | 현재 제공 | `.latticeignore` 필터링, 파일 지문(크기+mtime+SHA-256) 기반 미변경 스킵(재인덱싱 33s → 0.26s), 비차단 디바운스 파일 감시(`incremental_watch.rs`), 그리고 **2단계 청크 증분 동기화(`incremental_sync.rs`, `watch_deliver.rs`)**로 편집된 청크만 비동기 재임베딩하고 미변경 섹션 벡터를 재사용합니다. |
| **삭제된 파일 정리** | 현재 제공(동의 필요) | 폴더 수집은 사라진 파일을 자동 삭제하지 않고 보고합니다. `POST /api/ingestion/folder/prune`으로 드라이런 후 확인 시 `delete_document_tree`로 그래프 서브트리만 정리합니다(디스크 파일은 절대 삭제하지 않음). |
| **벡터 인덱스 백엔드 & 임베딩 계약** | 현재 제공 | `EmbeddingIndexContract`로 Python과 Rust 간 모델·차원·정규화 계약을 통일합니다. `LATTICEAI_VECTOR_INDEX`로 `brute`(기본, 정확 스캔), `quantized`, `hnsw`(`NativeHnswIndex` / 워커 `hnsw+rescore`)를 선택할 수 있으며, 512개 이상 벡터와 워커 연결 시 `hnsw+rescore`를 자동 시도합니다. |
| **백그라운드 임베딩 & 실시간 드레인 제어** | 현재 제공 | 실패한 인라인 벡터 동기화는 내구성 `vector_jobs` 큐에 적재되어 `lattice-jobs` 적응형 스케줄러가 처리합니다. UI의 `VectorFreshnessNotice`에서 `임베딩 완료 / 전체` 진행률 확인과 원클릭 **지금 인덱싱(`POST /api/index/drain`)**을 지원합니다. |
| **능동적 합성 & 모순 해결** | 현재 제공 | 모순되는 기억, 이름 없는 반복 주제, 자주 함께 등장하지만 연결되지 않은 쌍, 오래된 단편 기억을 감지해 검토함에 제안(`kg_change_digest`)합니다. `ContradictionInspector`에서 원클릭 모순 제안 및 해결(유지 / 대체 / 시간 범위 공존)이 가능합니다. |
| **자기모델 (Self-Model)** | 현재 제공 | 소유자에 관한 선호·습관·결정·관계(`self:root` 하위)를 별도 거버넌스로 관리합니다. 브레인은 제안만 하고 승인을 거쳐 기록하며, 사용자는 Brain의 `SelfModelPanel`에서 직접 조회·승인·수정·삭제할 수 있습니다. |
| **내구성 DAG 워크플로우 & 자동화** | 현재 제공 | `DurableWorkflowStore`(`kernel/workflow.rs`)가 단계별 원자적 체크포인트, SHA-256 멱등성 재생, `WaitingApproval` 일시 중지/재개, 실패 시 파일 스냅샷 복구, `USED_BY_AGENT`/`GENERATED_BY` 출처 엣지를 제공합니다. `/api/automation`은 반복 질문을 결정적으로 마이닝해 워크플로우 초안을 제안합니다. |
| **신뢰형 에이전트 루프 & 소형 모델 하네스** | 현재 제공 | `kernel/probe.rs`가 모델 로드 시 파싱 능력을 실측해 `standard` / `compact` / `guided` 프로파일을 자동 선택합니다. `guided` 모드는 JSON 없이 번호 메뉴와 인자별 턴으로 0.5B~2B 모델도 안전하게 파일을 작성하며, 실패 시 단방향 자가 강등(`demote_to_guided`)과 fail-closed 검증(`NEEDS_REVIEW`)이 동작합니다. |
| **변경 거버넌스 & 되돌리기 가능한 폴더 정리** | 현재 제공 | 기존 파일 수정/삭제는 기본 콘텐츠 해시 충돌 검사를 포함한 `change_proposal`로 검토함에 스테이징됩니다. "이 프로젝트를 정리해줘" 요청은 `topics/<주제>/` 이동 계획만 제안하며(삭제 경로 없음), 승인 시 1-클릭 되돌리기(Undo) 제안이 자동 생성됩니다. |
| **권한 모드 (PermissionMode)** | 현재 제공 | `strict`(먼저 물어보기, 기본) / `trusted`(웬만하면 알아서) / `bypass`(거의 다 알아서)를 사용자·워크스페이스 단위로 설정합니다(`설정 → 혼자 해도 되는 일`). 변경 전 diff 미리보기와 위험 확인 절차를 거치며, 파괴적 명령·루트/홈 경로·바이너리 덮어쓰기 서킷 브레이커는 모드와 무관하게 차단됩니다. |
| **네트워크 경계 & 하이브리드 클라우드 채팅** | 현재 제공(옵트인) | `local_only`(기본) / `cloud_allowed`를 `설정 → 내 지식이 나가는 범위`에서 제어합니다. 질문 전송 전 어떤 기억이 나가는지 토큰 추정과 함께 미리볼 수 있으며, 비밀 파일(`.env`, `.pem`, `.key` 등)과 민감 태그는 양쪽 모드 모두 차단됩니다. `api_key`(`GET /models` 무과금 프로브)와 `cli_oauth`(`agy`/`grok`)를 지원합니다. |
| **로컬 모델 · 임베딩 추천 · 다운로드 취소** | 현재 제공 | RAM/하드웨어 기반 로컬 LLM(Gemma 4, Qwen 3.5/3.6/3.8, LFM2.5, GPT-OSS) 및 Google EmbeddingGemma 2 추천을 제공합니다. 실시간 SSE 다운로드 진행률, 협조적 다운로드 취소(`AbortController` + `499`), 다운로드 완료 후 시맨틱 임베딩 자동 전환을 지원합니다. |
| **도구 레지스트리 & MCP 서버** | 현재 제공 | `POST /mcp`가 OpenAPI 제품 계약에 포함된 네이티브 스트리밍 HTTP JSON-RPC 서버로 동작하며(`initialize` / `tools/list` / `tools/call`), 그래프 인식 스킬과 안전한 도구 세트를 동일한 거버넌스 게이트로 노출합니다. |
| **기능 토글 (Feature Toggles)** | 현재 제공 | 홈 도크의 **기능** 서랍(`GET/POST /api/features`)에서 멀티모달, 비디오 수집, 볼트 감시, 브레인 네트워크, 자동 합성, 인라인 벡터 동기화, RRF 융합, 그래프 확장, 벡터 백엔드를 재시작 없이 제어합니다(우선순위: 사용자 → 환경 변수 → 기본값). |
| **로컬 상호운용 브리지** | 현재 제공(옵트인) | 외부 API 토큰 없이 로컬 파일만으로 Obsidian 볼트(수동 + 감시), Notion 내보내기(`.zip`/폴더), 로컬 Git 저장소 이력, `.eml`/`.ics` 파일을 단일 네이티브 수집 관문으로 가져옵니다. |
| **암호화 `.latticebrain` 아카이브 & 서브그래프 공유** | 현재 제공 | `scrypt` 암호문구 또는 `X25519` + `HKDF-SHA256` 공개키 암호화, `SHA256-CTR` + `HMAC-SHA256` 검증, ZIP 경로 탈출 차단을 제공합니다. 선택적 서브그래프 공유(`LATTICEAI_BRAIN_NETWORK`, 기본 꺼짐)는 Ed25519 서명과 검토함 제안 기반 수신을 사용합니다. |
| **VS Code & 브라우저 확장 프로그램** | 현재 제공 | VS Code 확장은 근거 판정, 인라인 변경 제안 검토, 실시간 `agent_step` 타임라인, 근거 기반 후속 액션을 지원합니다. 브라우저 확장은 `127.0.0.1` 전용 캡처·리콜·대기 중 승인 알림 및 서버 언어 동기화를 제공합니다(승인 *결정*은 설계상 브라우저 확장에서 제외). |
| **멀티모달(이미지·오디오·비디오)** | 옵트인(기본 꺼짐) | `allow_multimodal` 활성화 시 이미지(`Image`/`ImageText`), 오디오(`Audio`), 비디오(`Video` + `ffmpeg` 키프레임 + `.srt`/`.vtt`)를 지원합니다. OCR(`pytesseract`), 캡션(VLM), 비전 임베딩, 음성 전사 모델은 번들되지 않으며 미설치 시 가짜 텍스트를 만들지 않고 `unavailable`로 정직하게 표기합니다. |
| **사용자별 `AGENT_ROOT` 격리 & 실시간 SSE** | 현재 제공 | 다중 사용자/스코프 에이전트 실행 시 서버가 도출한 격리 루트(`IsolatedRootSpec`)를 강제하고, 인증된 Workspace OS 실시간 SSE 스트림과 워크스페이스 전환 시 라이브 쿼리 무효화를 제공합니다. |
| **PostgreSQL 백엔드** | 계획됨(Planned) | 공식 로컬 라이브 저장소는 SQLite입니다. PostgreSQL/pgvector는 드라이런 계획 및 옵트인 CI 검증만 존재하는 계획 단계입니다. |
| **텔레그램 브리지 & 텔레그램 검토** | **11.6.0에서 제거됨** | 제품 서버가 Rust로 전환되면서 기존 Python 플랫폼 코드에 의존하던 텔레그램 브리지가 제거되었습니다. 웹과 VS Code의 검토함(`/api/proposals`)은 그대로 유지됩니다. |
| **SSO / OIDC 로그인 콜백** | **11.6.0에서 제거됨** | Python 워커가 `/auth/*`를 마운트하지 않으면서 OIDC 로그인/콜백 흐름이 제거되었습니다. SSO 설정 화면과 `lattice-auth` 네이티브 비밀번호 로그인·세션·CSRF는 유지됩니다. |
| **릴리스 아티팩트** | 현재 제공 | Python wheel/sdist, npm tarball, VS Code VSIX, macOS Apple Silicon DMG(`Lattice AI_14.0.0_aarch64.dmg`) 및 `SHA256SUMS`가 정확한 버전 파일명으로 빌드·검증·배포됩니다. |

---

## 알려진 한계 (솔직한 경계)

- **소형 모델의 내용 품질**: `guided` 모드가 기계적 실행(파일 쓰기, `DONE` 도달)을 안정화했지만, 0.5B~2B 모델의 요약·내용 품질이 부족하면 fail-closed 검증기가 거짓 성공 대신 `NEEDS_REVIEW`로 차단합니다.
- **클라우드 `api_key` 라이브 과금 정책**: `api_key` 경로는 무과금 `GET /models` 프로브로 연결성을 검증하며, 실제 채팅 E2E는 무과금 `cli_oauth`(`agy`/`grok`) 기준으로 검증되었습니다.
- **macOS DMG 서명**: `APPLE_CERTIFICATE` 시크릿이 설정되면 Developer ID로 서명하며, 미설정 시 ad-hoc 서명(최초 실행 시 Gatekeeper 허용 필요)으로 폴백합니다.
- **벡터 검색 환경 변수 기본값**: `LATTICEAI_VECTOR_INDEX` 기본값은 여전히 `brute`입니다(512개 이상 벡터 + 워커 연결 시 `hnsw+rescore` 자동 시도). `quantized` 백엔드는 메모리 이점이 아직 충분히 나타나지 않아 기본 권장 대상이 아닙니다.
- **감시 정리는 그래프에만 적용**: 폴더 감시 중 삭제된 파일은 그래프에서만 정리되며 디스크 파일은 건드리지 않습니다. 단발성 폴더 수집에서는 명시적 `folder/prune` 확인이 필요합니다.
- **비전·음성 모델 및 `ffmpeg` 미번들**: OCR(`pytesseract`), VLM 캡션, 비전 임베딩, 음성 전사기, `ffmpeg`는 제품에 내장되지 않으며, 부재 시 가짜 값을 생성하지 않고 `unavailable`로 보고합니다. 이미지·비디오 멀티모달 추출 함수는 현재 HTTP 심이 제거되어 Brain Core 단위 테스트로만 유지됩니다.
- **이미지 근거 썸네일**: 증거 패널은 디스크 원본 경로를 노출하지 않기 위해 수집 시 생성된 96px 인라인 `data:image/...` 썸네일(24 KB 상한)만 표시합니다.
- **외부 상호운용 범위**: Notion API, IMAP, macOS 캘린더/메일 직접 연동은 의도적으로 범위 밖이며, 사용자가 직접 내보낸 로컬 파일만 읽습니다.
- **포인터 도구(`pyautogui`)**: 6개 포인터 도구는 네이티브 액추에이터가 아닌 워커의 `POST /agent/tool`에서 실행되며, `pip install "ltcai[pointer]"` 옵트인 설치가 필요합니다.
- **Hugging Face 런타임 다운로드 리비전 고정 백로그**: 정적 레지스트리 검증 스크립트(`verify_hf_model_registry.py`)는 동작하지만, 명시적 `model_revision`/SHA-256 매니페스트가 지정되지 않은 런타임 다운로드에는 기본 리비전 고정이 추가로 필요합니다.
- **Python 커버리지 게이트 하한선**: 11.8.0 이후 Python CI 커버리지 게이트는 라인 90%(분기 측정 비활성화)로 설정되어 있으며, 프론트엔드는 4대 지표 100%를 유지합니다.
- **로컬 데이터 보안 책임**: 기기 수준 데이터 보호는 OS 계정 보안, 디스크 암호화, 외부 백업 정책에 의존합니다.

---

## Git에 유지되는 릴리스 이력 범위 (`v12.0.0+`)

이 저장소는 현대화된 Rust 호스트 및 도메인 그룹 아키텍처 시대(`12.0.0` 이후)의 릴리스 이력을 유지합니다:

- `13.4.0` · `13.3.1` · `13.3.0` · `13.2.1` · `13.2.0` · `13.1.0` · `13.0.0`
- `12.10.0` · `12.9.0` · `12.8.0` · `12.7.0` · `12.6.0` · `12.5.0` · `12.4.0` · `12.3.0` · `12.2.1` · `12.2.0` · `12.1.0` · `12.0.0`

상세 변경 사항은 [`CHANGELOG.md`](./CHANGELOG.md), 아키텍처 상세는 [`ARCHITECTURE.md`](./ARCHITECTURE.md), 보안 지원 범위는 [`SECURITY.md`](./SECURITY.md)를 참고하세요.
