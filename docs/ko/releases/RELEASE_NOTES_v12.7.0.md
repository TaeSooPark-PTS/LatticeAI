[![English](https://img.shields.io/badge/English-Switch-475569?style=flat-square)](../../releases/RELEASE_NOTES_v12.7.0.md) [![한국어](https://img.shields.io/badge/한국어-현재_언어-2563eb?style=flat-square)](./RELEASE_NOTES_v12.7.0.md)

# Lattice AI 12.7.0 — Fast Index

> **상태: historical** — 해당 시점의 보존된 릴리스 노트입니다.

2026-11-16

12.6.0 Hybrid Graph가 3-Way BM25F + 벡터 + 지식 그래프 RRF 하이브리드 검색을 도입했다면, **12.7.0 Fast Index**는 **사전 토큰화 BM25F 사이드카**, **SQLite `bm25_node_stats` 영속화**, **역색인 포스팅 리스트 및 GraphRAG 인접 캐시**로 쿼리 시점 재토큰화와 그래프 링크 재파싱을 제거했습니다. 동시에 **제품 중심 `README.md`** 개편과 깔끔한 **`v12.0.0+` 릴리스 히스토리 하한선** 정리를 완료했습니다.

진입점은 **422개 오퍼레이션 / 41개 패밀리**, Python 워커는 **20개 라우트**를 유지하며, 쓰기는 여전히 단일 `GraphWriter`를 통합니다.

## 주요 반영 사항

- **사전 토큰화 BM25F 사이드카 및 SQLite `bm25_node_stats` 영속화.**
  - `rust/lattice-core/src/bm25_stats.rs`, `db/tables.rs`, `graph_write/{mod,primitives,documents,curation,provenance}.rs`: `TableOwnership`에 `bm25_node_stats`를 등록하고 `GraphWriter::open`에서 `persist_bm25_sidecar`를 활성화해 `upsert_node_with_vector`와 `set_node_sensitivity`가 사전 토큰화된 필드 길이(`dl_title`, `dl_tags`, `dl_body`, `dl_meta`)와 용어 빈도(`tf_json`)를 저장하며, 삭제·큐레이션·교체 임포트 시 자동 정리되도록 했습니다. `bm25_node_stats`가 없는 읽기 전용 또는 동결 골든 데이터베이스는 스키마 변경 없이 사이드카 하이드레이션을 안전하게 건너뜁니다.
  - `rust/lattice-retrieval/src/bm25/{sidecar,sidecar_cache,scoring,graph_rag,fusion,tokenizer,mod,tests}.rs`, `service_hybrid.rs`: O(1) `field_lengths`, `term_freqs`, `doc_freq`, `avgdl`, 역색인 `postings`(`term -> Vec<PostingEntry>`) 후보 평가, 증분 `(node_id, updated_at, content_hash)` 동기화, `expand_knowledge_graph_with_sidecar` 및 `score_and_expand_with_sidecar`를 통한 캐시된 GraphRAG 인접 구조(`title_to_doc_idxs`, `wikilink_to_doc_idxs`, `tag_to_doc_idxs`, `edge_adj`)를 제공하는 `Bm25SidecarIndex`를 추가했습니다.
- **제품 중심 `README.md` 개편 및 `v12.0.0+` 릴리스 히스토리 정비.**
  - 최종 사용자 제품 가치, 데스크톱/CLI/확장 빠른 시작, 8패널 제품 둘러보기 스크린샷 그리드, 문서 및 아키텍처 허브, 정직한 경계, `v12.0.0+` 릴리스 히스토리를 중심으로 `README.md`를 전면 개편했습니다.
  - `docs/releases/`, `docs/reviews/`, `RELEASE.md`, `RELEASE_NOTES.md`, `docs/CHANGELOG.md`, `FEATURE_STATUS.md`, `SECURITY.md`, `vscode-extension/README.md` 및 GitHub Releases에서 v12 이전 릴리스 노트(`v9.*`–`v11.*`)와 구형 리뷰/계획 스냅샷을 정리하고, `scripts/check_current_release_docs.mjs`와 `tests/unit/test_server_app_decomposition.py`가 `v12.0.0+` 하한선을 강제하도록 업데이트했습니다.

## 배포 아티팩트

- `dist/ltcai-12.7.0-py3-none-any.whl`
- `dist/ltcai-12.7.0.tar.gz`
- `ltcai-12.7.0.tgz`
- `dist/ltcai-12.7.0.vsix`
- `src-tauri/target/release/bundle/dmg/Lattice AI_12.7.0_aarch64.dmg`
