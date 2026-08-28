# Lattice AI v12.1.0 — Fast Path (2026-08-29)

스캔 → 파싱 → 임베드 → 인덱싱 → 검색이 한 파일씩 줄을 서던 경로를
겹치게 만든 릴리스. 쓰기 엔진은 그대로 하나(`GraphWriter`)이고, 느렸던
것은 그 앞의 시임과 그 안의 락이다.

| | |
| --- | --- |
| 테마 | **Fast Path** — 디스크에서 회상까지 한 줄로 |
| 문 | 네이티브 422 오퍼레이션 / 41 패밀리 (변동 없음) |
| 워커 | 20 라우트 (변동 없음) |

## Added

- 폴더 인제스트와 vault-watch가 파일을 `INGEST_INFLIGHT=4`로 겹친다.
  스탬프 스킵은 풀에 들어가지 않는다. 쓰기는 여전히 SQLite 단일 라이터다.
- 노트 인제스트가 문서 벡터와 청크 벡터를 **한 번의** `POST /worker/embed`
  로 보낸다. extract는 embed와 `tokio::join!`으로 겹친다.
- `write_vectors_with`가 그 시임 벡터를 그대로 파일한다. 해시 모델이
  같은 텍스트를 다시 돌리지 않는다.
- 기본 brute 검색은 스토어가 512행 이상이고 워커 시임이 묶여 있으면
  `hnsw+rescore`를 먼저 시도한다. 실패하면 원래 `brute` 인덱스 블록으로
  정확 스캔한다. 골든(작은 픽스처, 워커 없음)은 바이트 동일.

## Changed

- `rebuild_vector_index`가 임베드를 쓰기 트랜잭션 **밖**에서 한다.
  12.0.0이 drain에 적용한 것과 같은 모양이다. 골든은 FrozenClock 아래
  동일하다.
- 주간 `pip-audit`가 취약점 없는 런에서 마크다운 파일을 쓰지 않아
  `cat`이 실패하던 것을 고쳤다.
- 주간 Postgres 라이브 잡은 빠진 테스트 파일 때문에 실패하고 있었다.
  파이썬 그래프 라이터는 11.6.0에 떠났고, 스위트는 그 사실을 skip으로
  말한다. `ltcai[postgres]`는 빈 extra 별칭이다.

## Fixed

- Dependency Audit (schedule) — 깨끗한 pip-audit가 리포트 파일 부재로
  빨개지던 것.
- Postgres Integration (schedule) — `tests/integration/test_postgres_migration_live.py`
  가 트리에 없어서 exit 4.

## Known Issues

- 작은 모델의 *내용* 품질은 여전히 fail-closed다.
- `api_key` 클라우드 경로는 모의 검증만.
- DMG는 ad-hoc 서명.
- 기본 검색 env는 여전히 `brute`. 512행 이상 + 묶인 워커에서만 자동
  `hnsw+rescore`이고, 실패하면 정확 스캔이다. 명시적
  `LATTICEAI_VECTOR_INDEX=hnsw`는 이전과 같다.
- watch는 자동 삭제하지 않는다.
- 실임베딩 기본 롤아웃은 아직 닫히지 않았다. 해시 모델은 다운로드된
  임베더가 없으면 폴백이다.

## Follow-up

- 인제스트 시각에 실임베더가 묶여 있으면 `/worker/embed` 한 방이
  기본 경로다. 해시 폴백은 그대로다.
- HNSW를 env 기본으로 뒤집으려면 연속 인제스트 Brain에서 recall@10을
  다시 재야 한다.
- 폴더 패스의 extract는 여전히 파일당 한 번이다. 겹치기만 했고,
  백로그로 미루지는 않았다.
