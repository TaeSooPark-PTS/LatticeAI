[![English](https://img.shields.io/badge/English-Switch-475569?style=flat-square)](../../vscode-extension/README.md) [![한국어](https://img.shields.io/badge/한국어-현재_언어-2563eb?style=flat-square)](./VSCODE_EXTENSION.md)

# Lattice AI — VS Code 확장

**어떤 AI 모델로 교체해도 지식을 영속적으로 유지하는 로컬 우선 Digital Brain용 에디터 확장 프로그램입니다.**

[![VS Code Marketplace](https://img.shields.io/badge/VS%20Code%20Marketplace-Install-blue?logo=visualstudiocode)](https://marketplace.visualstudio.com/items?itemName=parktaesoo.ltcai)
[![Open VSX](https://img.shields.io/open-vsx/v/parktaesoo/ltcai?label=Open%20VSX)](https://open-vsx.org/extension/parktaesoo/ltcai)
[![License](https://img.shields.io/badge/license-MIT-green)](../../LICENSE)

Lattice AI 확장은 VS Code, Cursor, VSCodium을 로컬 우선 Digital Brain에 연결합니다. 에디터에서 바로 파일을 영속 Brain 컨텍스트로 보내고, Brain 기반 채팅과 모델 워크플로를 실행하며, 코딩 작업을 수행할 수 있습니다.

## 현재 릴리스

**13.4.0 — Living Synthesis**는 모델별 분기 없는 **범용 적응형 에이전트 하네스**(`probe.rs` 3턴 마이크로 프로브, 관찰 우선 순위화·한/영 도메인 분류, 결정론적 UI 중앙 좌표 해석 `ui_resolve.rs`, JSON 구문 복구·ChatML 프리필, 활성/폐기 증거 분리 `rag_grounding.rs`), 모듈화된 네이티브 웹 탐색 및 컴퓨터 유즈 브리지 정합성, 25개 태스크 실증 벤치마크(`Qwen2.5-0.5B-Instruct-Q4_K_M` `28.0% → 84.0%`), **100% 한/영 대등 문서화**를 비롯해 13.1.0~13.3.1의 2단계 비동기 시맨틱 증분 청크 동기화(`watch_deliver.rs`), `AnswerProof.tsx` 주장 검증 텔레메트리 및 `BrainIntelligencePanel.tsx` 개체 통합·시간축 카드, 인터랙티브 지식 그래프 이웃·2-Hop 추론 합성(`neighborhood_synthesis.rs`, `GraphNodeInspector.tsx`, `MultiHopSynthesisView.tsx`), 상충 필터 및 해결(`ContradictionInspector.tsx`), 채팅 **Brain에 기억하기** 및 인라인 변경 제안 승인(`InlineStagedProposals.tsx`), 임베딩 드레인 진행률 및 **지금 인덱싱**(`POST /api/index/drain`), 협력적 다운로드 취소·바이트 지표, 커맨드 팔레트 지식 노드 포커스, 반응형 작업공간 전환을 모두 제공합니다. 상세 내역은 [docs/ko/releases/RELEASE_NOTES_v13.4.0.md](./releases/RELEASE_NOTES_v13.4.0.md)에서 확인할 수 있습니다.

**13.0.0 — Sovereign Synthesis**는 크로스 도메인 결합 제거(`lattice_core::redact`, `lattice_agent::seam`, `domain_boundaries.rs`)와 통합 Rust 서버 i18n(`lattice_core::messages`), 2-Hop 지식 그래프 추론 체인·매개 개념·상충 합성(`reasoning_paths.rs`, `MultiHopSynthesisView.tsx`), 단일 트랜잭션 리뷰 스토어 변경, 원클릭 폴더 재구성 되돌리기(`ReviewCard.tsx`), 재시작 후 유지되는 대화 문서 세션, `frontend/src/api/client.ts` 및 `src-tauri/src/backend.rs` 모듈 분할(Vitest 100%)을 도입했습니다. 상세 내역은 [docs/releases/RELEASE_NOTES_v13.0.0.md](../releases/RELEASE_NOTES_v13.0.0.md)를 참고하세요.

**12.10.0 — Grounded Self**는 결정론적 에이전트 트랜스크립트 근거 검증 및 인터랙티브 인용 증빙 배지, 인라인 제안 승인/거절이 가능한 Brain 자기 모델 패널, `edges`·`edges_v2` 간 `PART_OF` 엣지 연쇄 삭제 정합성, 협력적 `499` 모델 다운로드 취소 및 다운로드 직후 시맨틱 임베딩 자동 전환·리인덱싱 드레인, `http_reply.rs` 전송 계층 분리, Python 워커 서버 i18n 100% 잠금(`6/6` 라우터), 병렬 `cargo nextest`, `release.yml` → `publish.yml` 자동 CD 체이닝을 추가했습니다. 상세 내역은 [docs/releases/RELEASE_NOTES_v12.10.0.md](../releases/RELEASE_NOTES_v12.10.0.md)를 참고하세요.

**12.9.0 — Semantic Guard**는 검색/회상/채팅 전반의 쿼리 시점 시맨틱 임베딩을 활성 `EmbeddingIndexContract`에 정렬하고, 순수 Rust `NativeHnswIndex` 및 워크스페이스/모델 스코프 Python HNSW 사이드카, fail-closed `local_only`/`cloud_allowed` 네트워크 경계 및 클라우드 컨텍스트 가드, Tauri 데스크톱/`LTCAI` CLI/호스트 슈퍼바이저/멀티스테이지 Docker 수명주기 하드닝, 실시간 Workspace OS SSE(`/api/realtime/stream`), v2 `.latticebrain` 아카이브 KDF 및 CI 게이트 강화를 도입했습니다. 상세 내역은 [docs/releases/RELEASE_NOTES_v12.9.0.md](../releases/RELEASE_NOTES_v12.9.0.md)를 참고하세요.

**12.8.0 — Trusted Brain**은 `EmbeddingIndexContract`와 지식 라이프사이클 추적을 통합하고, SQL `LIMIT` 이전 워크스페이스/라이프사이클 필터링·정렬된 벡터 검색·바운드 사이드카 캐시, 게이트웨이 라우트 전반의 fail-closed 멀티유저/워크스페이스/테넌트 격리, AEAD 암호화 `.latticebrain` 아카이브 및 정직한 PostgreSQL `planned` 상태 보고, `Library`/`System`/`Capture` UI 모듈화, Apple Silicon DMG CI/CD 자동화를 제공합니다. 상세 내역은 [docs/releases/RELEASE_NOTES_v12.8.0.md](../releases/RELEASE_NOTES_v12.8.0.md)를 참고하세요.

**12.7.0 — Fast Index**는 사전 토큰화 BM25F 사이드카(`Bm25SidecarIndex`), SQLite `bm25_node_stats` 영속화, 쿼리 시 재토큰화 없이 O(1) 조회를 수행하는 역색인 포스팅 리스트·GraphRAG 인접 캐시, 제품 중심 `README.md` 및 `v12.0.0+` 릴리스 이력 정리를 추가했습니다. 상세 내역은 [docs/releases/RELEASE_NOTES_v12.7.0.md](../releases/RELEASE_NOTES_v12.7.0.md)를 참고하세요.

**12.6.0 — Hybrid Graph**는 3-way BM25F + Vector + Knowledge Graph RRF 하이브리드 인덱싱·검색과 그래프 인식 에이전트 스킬, 멀티스케일 서브워드 `FallbackTextEmbedder`, 비인증 EmbeddingGemma 2 공개 미러 및 GGUF/MLX/ONNX 폴백, 온보딩·모델 라이브러리의 실시간 SSE 다운로드 속도(`MB/s`)·ETA 스트리밍, Pillow 13+ 정리, Ubuntu 24.04/26.04 `t64` CI 복원력, 모듈화 `scripts/bump_version/` CLI를 추가했습니다. 상세 내역은 [docs/releases/RELEASE_NOTES_v12.6.0.md](../releases/RELEASE_NOTES_v12.6.0.md)를 참고하세요.

**12.5.0 — Deep Vector**는 네이티브 Google EmbeddingGemma 2 지원, 하드웨어 맞춤 PC 임베딩 추천, Pillow 13+ flattened data 호환, Node 24 네이티브 CI/CD, OpenAPI 명세 및 픽스처 자동 동기화를 추가했습니다. 상세 내역은 [docs/releases/RELEASE_NOTES_v12.5.0.md](../releases/RELEASE_NOTES_v12.5.0.md)를 참고하세요.

**12.4.0 — Sharp Weight**는 One Door 토폴로지(422 오퍼레이션 / 41 패밀리, 20개 라우트 워커)를 유지하면서 검증된 `mlx-community/Qwen3.8-27B-4bit`, `mlx-community/gemma-4-26B-A4B-it-qat-4bit`, `mlx-community/gemma-4-26B-A4B-it-qat-OptiQ-4bit` 로컬/클라우드 라우팅을 추가하고 Python(`pillow>=13,<14`) 및 npm 보안 권고를 모두 해소했습니다. 상세 내역은 [docs/releases/RELEASE_NOTES_v12.4.0.md](../releases/RELEASE_NOTES_v12.4.0.md)를 참고하세요.

**12.3.0 — Live Wire**는 One Door 토폴로지를 유지하며 Hugging Face GGUF 양자화 태그(`Q4_0`, `MXFP4`), `Qwen3.5-9B`/`Qwen3.6-35B-A3B` GGUF 라우팅, 보조 GGUF 필터링 및 동반 `--mmproj` 다운로드, 소형 모델 툴/MCP 인자 복구, 네이티브 HTML 구조 점검(`/tools/inspect_html`) 및 RFC 5987 UTF-8 다운로드(`/tools/download`)를 검증했습니다. 상세 내역은 [docs/releases/RELEASE_NOTES_v12.3.0.md](../releases/RELEASE_NOTES_v12.3.0.md)를 참고하세요.

**12.2.1 — True Count**는 One Door 토폴로지를 유지하면서 검색·스킵·워치·소형 모델 요약이 실제로 존재하는 항목만 정확히 집계하도록 개선했습니다. 상세 내역은 [docs/releases/RELEASE_NOTES_v12.2.1.md](../releases/RELEASE_NOTES_v12.2.1.md)를 참고하세요.

**12.0.0 — Open House**는 두 대형 크레이트를 목적별로 재구성(`lattice-agent` 6개 그룹, `lattice-platform` 7개 도메인, 전량 `git mv`)하고 재시작 없는 즉시 복원, 항목별 동의 기반 `/setup/install`, OpenAPI 계약 내 `POST /mcp`, `pip install "ltcai[pointer]"` 포인터 도구를 완성했습니다. 상세 내역은 [docs/releases/RELEASE_NOTES_v12.0.0.md](../releases/RELEASE_NOTES_v12.0.0.md)를 참고하세요.

**확장 프로그램의 통신 표면은 버전 외 변동이 없습니다.** 동기화, 상태, 회상, 임시 제안, `POST /agent`(`stream:true`), `strict`/`trusted`/`bypass` 모드는 이전과 동일하게 동작하며, 클라우드 에스컬레이션과 MCP는 에디터가 아닌 호스트에서 실행됩니다.

이 확장은 에디터를 데스크톱·웹 앱과 동일한 로컬 우선 Digital Brain, 명시적 동의 게이트, 교체 가능한 모델 워크플로(`MLX-VLM` / `llama.cpp` / `Ollama` / `LM Studio` / 선택적 클라우드), 제안 우선 Review Center에 연결합니다.

## 빠른 시작

Lattice AI 워크스페이스를 설치하고 실행합니다.

```bash
pip install ltcai
LTCAI
```

Apple Silicon 로컬 모델 지원을 함께 설치하려면:

```bash
pip install "ltcai[local]"
```

그다음 확장을 설치합니다.

- [VS Code Marketplace: parktaesoo.ltcai](https://marketplace.visualstudio.com/items?itemName=parktaesoo.ltcai)
- [Open VSX: parktaesoo.ltcai](https://open-vsx.org/extension/parktaesoo/ltcai)

확장은 기본적으로 `http://localhost:4825`에 자동 연결됩니다.

## 주요 기능

| 영역 | 설명 |
| --- | --- |
| 채팅 패널 | 워크스페이스 및 그래프 컨텍스트를 활용해 로컬 또는 클라우드 모델과 대화 |
| 현재 파일 작업 | 활성 파일에서 질문, 편집, 설명, 리팩터링, 테스트 생성 |
| Send To Lattice | 현재 파일이나 선택 영역을 워크스페이스 워크플로로 전송 |
| 동기화 상태 | 에디터 연결, 인덱싱, 동기화 완료, 오프라인 상태 확인 |
| 모델 워크플로 | Lattice AI가 관리하는 모델 로드, 전환, 사용 |
| 지식 그래프 | 파일, 노트, 스크린샷, 대화 기반 그래프 컨텍스트 활용 |
| 멀티 에이전트 워크플로 | 플래너/실행기/리뷰어 방식 코딩 워크플로 시작 |
| 로컬 우선 워크스페이스 | 개인 작업은 로컬에 보관하면서 조직 워크스페이스 패턴도 지원 |
| 네이티브 v4 앱 | `/app`을 주 제품 셸로 사용 |

## 명령어

| 명령어 | 단축키 | 설명 |
| --- | --- | --- |
| Lattice AI: Open Chat | `Cmd+Shift+A` | 워크스페이스 채팅 패널 열기 |
| Lattice AI: Edit Selection | `Cmd+Shift+E` | 선택한 코드 다시 작성 |
| Lattice AI: Load Model | `Cmd+Shift+M` | 로컬 또는 클라우드 모델 선택 |
| Lattice AI: Explain Selection | 우클릭 메뉴 | 선택한 코드 설명 |
| Lattice AI: Refactor Selection | 명령 팔레트 | 선택한 코드 리팩터링 |
| Lattice AI: Generate Tests | 명령 팔레트 | 집중 테스트 코드 생성 |
| Lattice AI: Send To Lattice | 명령 팔레트 | 파일 컨텍스트를 Lattice AI에 기록 |
| Lattice AI: Ask About Current File | 명령 팔레트 | 현재 파일 컨텍스트로 질문 |
| Lattice AI: Show Sync Status | 명령 팔레트 / 상태 표시줄 | 에디터-앱 브리지 상태 확인 |
| Lattice AI: Generate Terminal Command | 우클릭 메뉴 | 셸 명령어 생성 |
| Lattice AI: Save to Knowledge Garden | 우클릭 메뉴 | 스니펫이나 노트 저장 |

## 모델 워크플로

Lattice AI는 로컬 및 클라우드 모델을 모두 지원합니다.

- Apple Silicon에서는 현세대 멀티모달 로컬 모델에 `MLX-VLM`을 사용하며, `MLX-LM`은 표준 Gemma 4 메타데이터용 텍스트 폴백으로만 유지합니다.
- LM Studio, vLLM, llama.cpp, Ollama 호환 로컬 경로를 지원합니다.
- OpenAI, OpenRouter, Groq, Together AI 및 OpenAI 호환 엔드포인트를 지원합니다.
- 모델 카드는 제작 국가, 제작 회사, 실행 방식, 인터넷 사용 여부, 모델명, HF 검증 상태, 다운로드/로드 전략, 하드웨어 적합도를 공개합니다.

## 설정

| 설정 키 | 기본값 | 설명 |
| --- | --- | --- |
| `ltcai.serverUrl` | `http://localhost:4825` | Lattice AI 워크스페이스 URL |
| `ltcai.autoLoadModel` | `false` | 확장 활성화 시 기본 모델 자동 로드 |
| `ltcai.defaultModel` | 비어 있음 | 선택적 기본 모델 ID |

원격 또는 터널 서버 연결 예시:

```json
{
  "ltcai.serverUrl": "https://your-server.example"
}
```

## 링크

- [프로젝트 README](https://github.com/TaeSooPark-PTS/LatticeAI)
- [PyPI](https://pypi.org/project/ltcai/)
- [npm](https://www.npmjs.com/package/ltcai)
- [GitHub Releases](https://github.com/TaeSooPark-PTS/LatticeAI/releases)
- [변경 이력 (Changelog)](https://github.com/TaeSooPark-PTS/LatticeAI/blob/main/docs/CHANGELOG.md)
- [보안 정책 (Security Policy)](https://github.com/TaeSooPark-PTS/LatticeAI/blob/main/SECURITY.md)

## 라이선스

MIT — [TaeSoo Park](https://github.com/TaeSooPark-PTS)
