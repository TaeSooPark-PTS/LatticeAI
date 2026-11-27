[![English](https://img.shields.io/badge/English-Switch-475569?style=flat-square)](../../releases/RELEASE_NOTES_v14.3.0.md) [![한국어](https://img.shields.io/badge/한국어-현재_언어-2563eb?style=flat-square)](./RELEASE_NOTES_v14.3.0.md)

# Lattice AI v14.3.0 — Temporal Synthesis

릴리스 날짜: **2026-11-27**

`v14.3.0`은 첫 답변 후 데스크톱 채팅이 접히는 치명적 결함을 포함한 4건의 인터랙티브 UX 문제를 해결하고, 채팅 경로 RAG 검색·시간 정확도를 CI 래칫 게이트와 함께 대폭 개선하며, 6건의 다중 사용자 개인정보·무결성·성능 결함을 수정합니다.

## 주요 변경 사항

### 1. 프론트엔드 UX 수정

- **첫 답변 후 데스크톱 채팅 접힘 해결:** `BrainMemoryAutomation`과 저장 피드백 영역을 스크롤 가능한 `.brain-stream` 내부로 이동해 `1024×640`, `1280×720`, `1440×900` 해상도에서도 메시지와 입력창이 뷰포트 안에 유지되도록 수정했습니다(Playwright 시각 회귀 테스트 추가).
- **`Cmd/Ctrl+K` "오늘의 브리핑" 화면 이동 열기:** `appStore`의 대기 플래그를 통해 어느 화면에서 선택하든 브레인 화면으로 이동해 브리핑 드로어를 열고 펼친 뒤 스크롤합니다.
- **2D 기억 그래프 "구버전 표시" 토글 연동:** `buildExplorerModel` 단계에서 구버전 필터링을 적용해 3D·2D(WebGL 폴백) 화면과 노드 개수가 모두 토글을 반영합니다.
- **커맨드 팔레트 키보드 탐색 스크롤:** `activeIndex` 변경 시 활성 항목에 `scrollIntoView({ block: "nearest" })`를 호출해 방향키 선택이 화면 밖으로 벗어나지 않습니다.

### 2. 채팅 검색 및 시간 RAG 정확도

- **채팅 IDF/BM25F 관련도 랭킹:** 규칙 개념과 폴백 질의 토큰을 합집합으로 결합하고 이진 매칭 대신 IDF/BM25F 관련도 점수를 사용합니다.
- **`as_of` 시점 유효기간 의미론:** `TimeIntent.as_of`, 연도 단독 파싱, 유효기간 필터링, `as_of` 질의의 관련도 정렬 및 `as_of` 시점 상태 평가를 추가하고 `ContextRequest.as_of`로 연결했습니다.
- **엄격한 Self-RAG 보류:** 근거 토크나이저와 커버리지 토크나이저를 일치시키고 커버리지 검증을 강화해 엔티티만 겹치는 비근거 질의가 오탐 통과되지 않도록 수정했습니다.
- **채팅 경로 CI RAG 래칫(`rust/lattice-retrieval/src/rag_eval_chat.rs`):** `datasets/rag_eval_v1.json`의 응답 가능 질의 248건과 워크스페이스 경계 질의 32건을 상수·`valid_from` 타임스탬프 모드 모두에서 검증합니다.

#### RAG 평가 지표 (`datasets/rag_eval_v1.json`, hash 임베더, n=248)

| 지표 | v14.2.0 기준선 (채팅 경로) | v14.3.0 CI 하한 (Constant / ValidFrom) | 상태 |
| --- | :---: | :---: | :---: |
| 전체 Recall@5 | 0.9194 | >= 0.9800 / >= 0.9800 | 통과 |
| 전체 MRR | 0.6122 | >= 0.8500 / >= 0.8300 | 통과 |
| 전체 nDCG@10 | 0.6826 | >= 0.8700 / >= 0.8600 | 통과 |
| `temporal_ordering` Recall@5 | 0.6000 | >= 0.9900 / >= 0.9900 | 통과 |
| `temporal_ordering` MRR | 0.2670 | >= 0.7900 / >= 0.7900 | 통과 |
| 워크스페이스 경계 누출 (n=32) | 0 | 0 | 통과 |
| 구버전(superseded) 제외율 | 1.0000 | 1.0000 | 통과 |

### 3. 다중 사용자 개인정보·무결성·성능 강화

- **공유 번들 내보내기 뷰어 필터:** `build_subgraph_bundle`이 호출자 신원에서 뷰어를 도출해 타 사용자 대화 파생 노드(이웃·청크 포함)를 제거하고, `nodes_v2.workspace_id`를 사용하며 인증 활성화 시 `workspace_id`를 필수로 요구합니다.
- **공유 번들 가져오기 강화(`rust/lattice-core/src/graph_write/share_import.rs`):** 수락 시 단순 병합 대신 기존 로컬 ID 덮어쓰기 방지, 수락 워크스페이스·소유자 고정, 허용 노드 타입 제한(대화 타입 제외), 메타데이터 정제, `knowledge_sources` 제외를 적용합니다.
- **시간 사전 필터 범위 제한:** `TemporalPlan::resolve`, `window_node_ids`, `version_anchor`에 `Scope`를 전달해 `nodes_v2` 기준 `workspace_membership_sql` + `owner_visible_sql`을 강제합니다.
- **`LIMIT` 이전 범위 필터 적용:** `graph_view`, `graph_slice`의 SQL `WHERE`에 `LIMIT` 이전에 `workspace_membership_sql`을 적용하고 `/api/graph/search`에서 `keyword::search`에 `Scope`를 전달합니다.
- **연대기 뷰어 필터 fail-closed:** `chronicle_api/store.rs`의 `retain_viewer`가 SQL 오류 시 원본 행 대신 빈 목록을 반환합니다.
- **덮어쓰기(supersede) 식별자 인덱싱:** `idx_nodes_source_uri`, `idx_nodes_filename` 표현식 인덱스와 리터럴 JSON 경로 조건을 추가했습니다.

## 배포 아티팩트

- `dist/ltcai-14.3.0-py3-none-any.whl`
- `dist/ltcai-14.3.0.tar.gz`
- `ltcai-14.3.0.tgz`
- `dist/ltcai-14.3.0.vsix`
- `src-tauri/target/release/bundle/dmg/Lattice AI_14.3.0_aarch64.dmg`
