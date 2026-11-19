[![English](https://img.shields.io/badge/English-Switch-475569?style=flat-square)](../../releases/RELEASE_NOTES_v12.2.1.md) [![한국어](https://img.shields.io/badge/한국어-현재_언어-2563eb?style=flat-square)](./RELEASE_NOTES_v12.2.1.md)

# Lattice AI 12.2.1 — True Count

> **상태: historical** — 해당 시점의 보존된 릴리스 노트입니다.

2026-08-29

12.2.0 Small Voice가 소형 모델에 *도구*를 쥐여 주었다면, 12.2.1은 남아 있던 숫자를 정직하게 셉니다. 벡터 쿼리는 먼저 `COUNT`를 실행해 누락된 행만 읽어 들이고, 바이트 변화 없이 터치된 파일은 스탬프를 갱신하며, 감시(watch) 중 사라진 파일은 그래프에서 정리되고, 빈약한 요약은 이미 읽은 파일의 문장으로 보완하거나 검토 큐로 보냅니다. 쓰기는 여전히 단일 `GraphWriter`를 통합니다.

진입점은 **422개 오퍼레이션 / 41개 패밀리**, Python 워커는 **20개 라우트**를 유지합니다.

## 주요 반영 사항

- **HNSW 사이드카 COUNT+델타 동기화.** 웜 쿼리는 `vector_embeddings`에 `COUNT(*)`만 먼저 수행합니다. 크기가 같으면 블롭 덤프 없이 즉시 검색하고, 증가했으면 ID 조회 후 누락된 ID의 블롭만 가져와 `add_items`로 추가합니다(디스크에서 로드된 그래프 포함). 감소했으면 남은 항목 기준으로 전체 재구축합니다.
- **`SkipByHash` 스탬프 재기록.** SHA-256은 같고 크기/mtime만 바뀐 경우 `GraphWriter::update_ingestion_metadata`를 통해 기존 출처 행을 갱신하여 다음 스캔에서는 스탬프만으로 건너뜁니다.
- **감시 폴더 자동 정리(Watch prune).** `scan_watches`가 사라진 파일에 대해 `prune_deleted(..., confirm=true)`를 호출합니다. 디스크 원본 파일은 절대 삭제하지 않으며, 일회성 폴더 수집은 기존처럼 누락 보고만 하고 노드를 유지합니다.
- **요약 복구(Summary salvage).** `complete_a_summary`가 트랜스크립트에 이미 존재하는 `read_file` / `mcp.read_file` 내용으로 "요약했습니다" 수준의 빈약한 답변을 채워 넣습니다. 근거가 부족하면 거짓 DONE 대신 `NEEDS_REVIEW`로 분기합니다.
- **`compact` CORE에 MCP 포함.** `mcp.grep`, `mcp.list_dir`, `mcp.read_file`, `mcp.knowledge_search`가 네이티브 파일 도구와 함께 9개 상한 목록에 유지됩니다. 요청에 `mcp.grep`을 명시하면 여전히 최우선 순위로 배치됩니다.
- **`api_key` 라이브 `/models` 프로브.** 완결(completion) 호출이나 과금 없이 연결만 확인하며, 거절되거나 도달 불가한 키는 `GET /api/cloud/status`를 fail-closed로 처리합니다.
- **해시 검색의 정직한 명칭 표기.** 첫 실행 화면과 모델 라이브러리에서 해시 검색임을 명확히 알리고 시맨틱 모델 설치를 안내합니다. `lattice-core` 내 두 번째 임베더는 아직 없습니다.
- **릴리스 노트의 `docs/releases/` 통합 관리.** 저장소 루트는 최신 색인인 `RELEASE.md`와 `RELEASE_NOTES.md`를 유지합니다.

## 남은 과제 (Honest leftovers)

- 해시 임베딩은 무다운로드 기본값으로 유지되며 시맨틱이 아닌 `fallback`으로 표기됩니다.
- macOS DMG는 빌드 머신에 Apple Developer ID가 없는 한 ad-hoc 서명됩니다. 이 저장소는 Apple 인증서를 포함하지 않습니다.
- 마켓플레이스 게시 후 목록 반영 검증에 수 분이 걸릴 수 있습니다.
- 벡터 검색 환경 기본값은 여전히 `brute`이며, 512건 이상에서의 자동 `hnsw+rescore` 동작은 12.1.0과 동일합니다.
- `GraphWriter::delete_node`는 여전히 `PART_OF` 엣지를 남기며, prune 경로는 이를 사용하지 않습니다.

## 배포 아티팩트

- `dist/ltcai-12.2.1-py3-none-any.whl`
- `dist/ltcai-12.2.1.tar.gz`
- `ltcai-12.2.1.tgz`
- `dist/ltcai-12.2.1.vsix`
- `src-tauri/target/release/bundle/dmg/Lattice AI_12.2.1_aarch64.dmg`
