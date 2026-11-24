[![English](https://img.shields.io/badge/English-Switch-475569?style=flat-square)](../../releases/RELEASE_NOTES_v13.7.1.md) [![한국어](https://img.shields.io/badge/한국어-현재_언어-2563eb?style=flat-square)](./RELEASE_NOTES_v13.7.1.md)

# Lattice AI v13.7.1 — Living Synthesis (2026-11-24)

`lattice-ingest`, `lattice-platform`, `lattice-retrieval`에 남아 있던 모든 죽은 `forward()` 폴백과 레거시 호환 스캐폴딩을 제거해 `GraphWriter` 미연결 시 모든 인제스트 엔드포인트가 일관되게 HTTP `503 Service Unavailable`을 반환하도록 정리하고, `local_files_replay.rs`에 임시 `git init` 저장소 기반 `source: "git"` 통합 테스트 및 503 계약 테스트를 추가했으며, 모든 벤치마크 문서를 보존한 채 `13.7.0` 이전 리뷰·계획·레거시 호환 문서를 일괄 정리했습니다.

## 주요 변경 사항

- **레거시 `forward()` 폴백 및 호환 스텁 완전 제거 (`rust/lattice-ingest`, `rust/lattice-platform`, `rust/lattice-retrieval`):**
  - `POST /api/ingestion/jobs/:job_id/resume`, `POST /api/ingestion/folder`, `POST /knowledge-graph/local/index`, `POST /api/browser/read-url`의 죽은 `forward()` 폴백을 제거하고 `read-url`과 `local/index`를 `GraphWriter`/`NoteIngestor` 네이티브 경로로 전환했으며, `GraphWriter` 미연결 시 일관되게 HTTP `503`을 반환합니다.
  - 미사용 `local_files_api::forward`, `browser_api::forward`, `local_files_api::require_seam`, `local_files_api::http::seam_error`, `search_api::require_seam`, `lattice_platform::crate_ready()`를 모두 삭제했습니다.
- **Git 연동 및 503 계약 통합 테스트 (`rust/lattice-ingest/tests/local_files_replay.rs`):**
  - 임시 `git init` 저장소에 샘플 파일과 커밋을 생성해 `source: "git"`의 `dry_run: true` 및 실인제스트를 검증하는 통합 테스트를 추가했습니다.
  - `unwired_graph_writer_returns_503_consistently` 테스트로 4개 인제스트 엔드포인트의 503 반환을 고정했습니다.
- **`13.7.0` 이전 리뷰·계획·레거시 문서 정리 (`docs/`, `docs/ko/`):**
  - `CODE_REVIEW_2026-11-20.md`, `reviews/CODEBASE_REVIEW_AND_IMPROVEMENT_ROADMAP.md`, 구형 `v11.x` 계획 문서, `LEGACY_COMPATIBILITY.md`(및 한국어 미러)를 삭제하고, 모든 벤치마크 문서(`ADAPTIVE_HARNESS_BENCHMARK.md`, `BENCHMARKS.md`, `PERFORMANCE.md`)는 그대로 보존했습니다.
  - 모든 정본 EN/KO 문서와 정확 버전 산출물 참조를 `13.7.1`로 동기화했습니다.

## 정확한 빌드 산출물

- `dist/ltcai-13.7.1-py3-none-any.whl`
- `dist/ltcai-13.7.1.tar.gz`
- `ltcai-13.7.1.tgz`
- `dist/ltcai-13.7.1.vsix`
- `src-tauri/target/release/bundle/dmg/Lattice AI_13.7.1_aarch64.dmg`
