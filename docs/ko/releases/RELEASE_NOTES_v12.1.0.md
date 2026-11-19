[![English](https://img.shields.io/badge/English-Switch-475569?style=flat-square)](../../releases/RELEASE_NOTES_v12.1.0.md) [![한국어](https://img.shields.io/badge/한국어-현재_언어-2563eb?style=flat-square)](./RELEASE_NOTES_v12.1.0.md)

# Lattice AI v12.1.0 — Fast Path (2026-08-29)

> **상태: historical** — 해당 시점의 보존된 릴리스 노트입니다.

기존에 단일 파일 순차 파이프라인(스캔 → 파싱 → 임베딩 → 인덱싱 → 검색)으로 동작하던 흐름을 중첩 실행(overlap)하도록 개선한 릴리스입니다. 쓰기 엔진은 여전히 단일 `GraphWriter`를 유지하며, 병목이던 그 앞의 심(seam)과 내부 락을 해소했습니다.

| | |
| --- | --- |
| 테마 | **Fast Path** — 디스크에서 회상까지 곧장 이어지는 고속 경로 |
| 진입점(Door) | 네이티브 422개 오퍼레이션 / 41개 패밀리 (변경 없음) |
| 워커(Worker) | 20개 라우트 (변경 없음) |

## 추가된 기능

- 폴더 수집과 `vault-watch`가 `INGEST_INFLIGHT=4`로 파일을 중첩 처리합니다. 스탬프 기준으로 건너뛴 파일은 워커 풀에 진입하지 않으며, 쓰기는 여전히 단일 SQLite 라이터를 통해 직렬화됩니다.
- 노트 수집이 문서 벡터와 청크 벡터를 **단일** `POST /worker/embed` 요청으로 묶어 전송하며, `tokio::join!`으로 추출과 임베딩을 중첩 실행합니다.
- `write_vectors_with`가 심에서 생성된 벡터를 그대로 영속화해 동일 텍스트에 해시 모델을 다시 돌리지 않습니다.
- 기본 `brute` 검색은 저장소에 512개 이상의 행이 있고 워커 사이드카가 연결되어 있으면 먼저 `hnsw+rescore`를 시도하며, 실패 시 정확한 `brute` 스캔으로 폴백합니다. 골든 테스트(소형 픽스처, 워커 없음)는 바이트 단위로 동일합니다.

## 변경된 사항

- `rebuild_vector_index`가 12.0.0의 드레인 패턴과 동일하게 임베딩을 쓰기 트랜잭션 **바깥**에서 계산합니다. `FrozenClock` 기준 골든 테스트는 동일하게 유지됩니다.
- 취약점이 없는 클린 실행 시 마크다운 리포트 파일이 생성되지 않아 `cat`에서 실패하던 주간 `pip-audit` 작업을 수정했습니다.
- 제거된 테스트 파일로 실패하던 주간 라이브 Postgres 작업을 수정했습니다. Python 그래프 라이터는 11.6.0에서 제거되었으며, 이제 스위트가 이를 skip으로 정직하게 보고하고 `ltcai[postgres]`는 빈 extra 별칭으로 유지됩니다.

## 수정된 버그

- Dependency Audit(정기 스케줄) — 클린 `pip-audit` 실행이 리포트 파일 부재로 실패 처리되던 문제.
- Postgres Integration(정기 스케줄) — 저장소에 더 이상 존재하지 않는 `tests/integration/test_postgres_migration_live.py`로 인해 exit 4가 발생하던 문제.

## 알려진 이슈

- 소형 모델의 *콘텐츠* 품질은 여전히 fail-closed로 처리됩니다.
- `api_key` 클라우드 경로는 목(mock) 검증만 수행합니다.
- macOS DMG는 ad-hoc 서명 상태입니다.
- 벡터 검색 환경변수 기본값은 여전히 `brute`입니다. 자동 `hnsw+rescore`는 512행 이상 + 워커 연결 시에만 활성화되며 실패 시 전수 스캔으로 폴백합니다. 명시적 `LATTICEAI_VECTOR_INDEX=hnsw`는 이전과 동일하게 동작합니다.
- `watch`는 파일을 자동 삭제하지 않습니다.
- 실제 시맨틱 임베딩의 기본 배포 전환은 아직 완료되지 않았으며, 다운로드된 임베더가 없을 때는 해시 모델이 폴백으로 유지됩니다.

## 후속 과제

- 수집 시점에 실제 임베더가 바인딩되어 있으면 단일 호출 `/worker/embed` 경로가 기본으로 사용되며, 해시 폴백은 그대로 유지됩니다.
- `hnsw`를 환경 기본값으로 전환하려면 지속적으로 수집된 브레인에서 `recall@10`을 재측정해야 합니다.
- 폴더 경로 추출은 여전히 파일당 1회 실행되며, 백로그로 미루는 대신 중첩 실행으로 처리합니다.
