# v11.1.0 — Product Intelligence (2026-08-10)

> **Status: historical** — point-in-time release note.

v9–v11.0이 기초를 굳혔다면(제안-우선 신뢰, 정직 신호, 라인·분기 100%
플로어), 11.1.0은 그 위에 **지능 레이어**를 올립니다. 계획 문서
[docs/v11.1.0_PRODUCT_INTELLIGENCE_PLAN.md](../v11.1.0_PRODUCT_INTELLIGENCE_PLAN.md)의
5개 트랙을 한 릴리스에서 완성했습니다: 스케일에서 빠르고, 스스로
관찰·제안하고, 사진과 녹음을 기억하고, 나를 알고, 기존 도구와 연결됩니다.

## Track 1 — 고성능 벡터 검색 + 증분 인덱싱

- **플러그형 벡터 인덱스 레이어** `lattice_brain/graph/vector_index/`:
  BruteForce(기본, 기존 동작 그대로 이전) · int8 Quantized ·
  HNSW(`pip install "ltcai[hnsw]"` 옵트인, `.hnsw` 사이드카는 파생물 —
  지워져도 재구축). `LATTICEAI_VECTOR_INDEX`로 선택, 미설치 백엔드는
  사유와 함께 brute로 정직 폴백, 근사 결과는 `approx: true` 표기.
- **실측** (Apple Silicon, docs/PERFORMANCE.md):

  | 벡터 수 | brute 하이브리드 p50 | HNSW 하이브리드 p50 | recall@10 |
  | --- | --- | --- | --- |
  | 10,000 | 299 ms | **10.1 ms** | 0.953 |
  | 50,000 | 1,515 ms | **43.9 ms** | 0.987 |

  계획의 "10k에서 p50 < 50ms" 목표를 50k에서도 충족. 정직 기록 2건:
  최초 50k recall 0.18은 베이스라인 절단을 잰 것이었고(교정), quantized는
  이 구조에선 RAM 이득이 없어 ~2.2× 느리다고 그대로 적었습니다.
- **영속 배경 임베딩 큐**(`vector_jobs` SQLite): 인라인 동기화 실패가
  조용히 사라지지 않고 재시도·터미널 `failed`까지 가시화.
  `vector_freshness_breakdown()` = embedded/pending/missing/stale/queued.
- **RRF 융합·그래프 이웃 후보 확장**(기본 꺼짐 — 기존 랭킹 단언 전부
  유지), context_quality에 벡터 신선도/approx 주의 표기.

## Track 2 — 능동 합성 + 모순 + 시간 모델

- **Temporal 모델**: `valid_from`/`valid_to`/`superseded_by` 추가 컬럼
  (기존 Brain은 제자리 멱등 승급, NULL 규약 — 11.0.1의 COALESCE 함정
  재현 없음), `as_of(timestamp)`로 "그 시점의 그래프" 슬라이스.
- **모순 → 제안 루프**: 감지는 읽기 전용 그대로, 발견 시 리뷰 큐에 평문
  요약과 해결 옵션(유지/교체/기간 표시)을 담은 제안 생성. **승인 시에만**
  temporal 스탬프 적용 — 제안-우선을 테스트가 프록시로 단언.
- **이벤트 기반 합성**: 성공 인제스트 25개(조정 가능)마다 상위 개념·누락
  엣지·proactive Brain Brief 제안. 인제스트 파이프라인에 격리 배선 —
  트리거 실패가 인제스트를 실패시키지 않음.
- **중요도/정리**: 빈도+나이 감쇠 점수, 하위 에피소딕의 통합 제안,
  "Brain이 정리 중" 정직 신호.
- 새 API: `/api/brain/proactive-brief`, `/api/brain/importance`,
  `/api/brain/synthesize`, `/api/brain/contradictions/propose|resolve`.

## Track 3 — 멀티모달 1등 시민

- `allow_multimodal`(`LATTICEAI_ALLOW_MULTIMODAL`, **기본 꺼짐 — 꺼져
  있으면 바이트 동일**, 양 모드 테스트 고정): MIME/확장자 라우팅으로
  이미지는 1등 `Image` 노드(content-addressed, OCR 자식, 청크), 녹음은
  1등 `Audio` 노드(전사 없으면 "아무도 듣지 못했다"는 정직한 본문).
- **캡션 조작 제거**: 파일명으로 캡션을 합성하던 VisionStub을 삭제 —
  캡션은 비전 모델이 실제로 만들었을 때만 존재(`caption_status`).
- 비전 임베딩은 **별도 이미지 공간 + late fusion**(차원 충돌 회피),
  가드 임포트(mlx_clip 등) + 해시 폴백 없음(해시는 그림이 아니므로).
- Evidence 패널 썸네일: 96px 인라인 `data:` URI(24KB 상한) — 새 정적
  라우트 없이, 로컬 파일 승인 게이트를 우회하지 않는 방식.
  비디오는 저장하지 않고 정직하게 거부(`VIDEO_OUT_OF_SCOPE`).

## Track 4 — Self-Model + 에이전트 워크스페이스

- **Self-Model 서브그래프**(Self/Preference/Decision/Habit/Relationship):
  결정적 추출 → **제안으로만** 생성, 사용자 직접 편집·삭제는 즉시.
  `self_model_summary`가 답변 컨텍스트에 예산 규율(블록이 예산 절반
  초과 금지) 아래 주입 — 요약이 비면 무주입, 기존 컨텍스트 계약 불변.
- **폴더 재구성 제안**: Brain이 근거를 대는 파일만 `topics/<주제>/`로
  이동 제안, 나머지는 이유와 함께 unplaced — **삭제 경로 자체가 없음**
  (구조적 보장). 승인 시 Review Center에서 실제 적용.
- 약한 로컬 모델용 실행 프롬프트에 Self-Model 요약·구조 힌트(compact
  프로파일 존중).

## Track 5 — Obsidian 브릿지 + 선택적 공유

- **Obsidian vault 인제스트**(`POST /api/ingestion/obsidian`, 로컬 접근
  승인 게이트): 모든 노트가 단일 IngestionPipeline 게이트 통과,
  `[[위키링크]]`→REFERENCES 엣지, frontmatter 태그→Topic. 해석 불가
  링크는 추측 없이 unresolved 리포트. 재실행 멱등(재실행 후 카운트
  바이트 동일 검증).
- **선택적 서브그래프 공유**(프로토타입, `LATTICEAI_BRAIN_NETWORK`
  기본 꺼짐 — 꺼져 있으면 403): 선택 노드 + provenance만 담은 서명된
  `.latticebrain` 부분 아카이브(Ed25519 — 변조 시 검증 실패 fail-closed),
  수신은 **전부 리뷰 제안으로**. 프라이버시 기본값: provenance 마스킹
  on, 이웃 확장은 Person/Source 거부, 로컬 경로(knowledge_sources)는
  절대 동봉 안 함.

## 검증

| gate | result |
| --- | --- |
| pytest (라인+분기 100 플로어) | **6,261 passed · 100.00%** (37,590문 · 10,658분기) — 3회 연속 |
| linux python:3.14 컨테이너 | 6,261 passed · 100.00% |
| fresh python 3.11 venv | 6,257 passed (unit) |
| vitest | 1,653 passed · 100% 4지표 |
| mypy | 291 / 291 modules, 0 errors |
| ruff · OpenAPI drift · docs/i18n 게이트 | 전부 클린 |
| 성능 벤치 | scripts/bench_vector_index.py + docs/PERFORMANCE.md 갱신 |

플랫폼 함정 1건을 출하 전에 잡았습니다: `.mid` modality 판정이 시스템
mime 테이블에 좌우되던 것(맥은 apache mime.types로 audio, slim linux는
테이블 없음)을 모듈 자체 확장자 표로 결정화.

## 정직한 한계 (FEATURE_STATUS.md에 전체 목록)

- Notion/이메일/캘린더/Git 브릿지, 수신자 공개키 암호화, 비디오 인제스트,
  vault 감시 모드는 이번 릴리스 범위 밖(로드맵 명시).
- 텍스트 질의→이미지 벡터 late fusion은 API 전용(UI가 이미지 벡터를
  아직 공급하지 않음). 이미지 검색은 OCR/캡션 텍스트로 동작.
- Self-Model 요약은 컨텍스트 경로에 주입되며, 에이전트 루프의 고정
  실행 프롬프트에는 새 포트가 필요(한계 명시).
- 공유 수신은 노드 단위 승인(일괄 승인 UI 없음).
- HNSW 상시 비용 중 신선도 COUNT에 커버링 인덱스가 없어 50k에서 7→36ms
  가산 — 다음 스키마 정리 후보로 기록.

## Artifacts (exact filenames)

- `dist/ltcai-11.1.0-py3-none-any.whl`
- `dist/ltcai-11.1.0.tar.gz`
- `ltcai-11.1.0.tgz`
- `dist/ltcai-11.1.0.vsix`
- `src-tauri/target/release/bundle/dmg/Lattice AI_11.1.0_aarch64.dmg`

와일드카드 업로드는 사용하지 않습니다. 패키지 스토어 배포는 owner-run입니다.
