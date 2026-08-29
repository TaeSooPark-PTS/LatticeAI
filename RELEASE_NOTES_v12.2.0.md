# Lattice AI v12.2.0 — Small Voice (2026-08-29)

2B급 로컬 모델이 MCP·스킬·도구를 고르지 못하던 구멍을 막은 릴리스.
쓰기 엔진은 그대로 하나(`GraphWriter`)이고, 홈 화면은 그 일을 평범한
말로 제안한다.

| | |
| --- | --- |
| 테마 | **Small Voice** — 작은 모델도 도구를 말한다 |
| 문 | 네이티브 422 오퍼레이션 / 41 패밀리 (변동 없음) |
| 워커 | 20 라우트 (변동 없음) |

## Added

- 실행 파서가 함수 호출 봉투를 읽는다. `{"name", "arguments"}`,
  중첩 `function`, Qwen `<function=read_file><parameter=path>…`, 
  `Action: mcp.grep`. 수리는 `alias_keys` / `args_string` / `xml_call`
  / `labeled`로 트레이스에 남는다.
- 콤팩트 실행 목록이 가이드 메뉴와 같은 9행으로 잘린다. 요청이 이름 붙인
  행은 앞에 남고 `final`은 마지막이다.
- 스킬의 `when` 문구가 요청과 맞으면 카탈로그 이름을 안 해도
  `SKILL.md`를 먼저 읽는다.
- 따옴표 친 검색어가 빠진 `pattern`/`query`를 채운다.
- 홈 작곡기 아래 칩: 폴더에서 찾기, 파일 읽고 요약, 기억에서 찾기,
  스킬로 검토. 설정에 같은 설명이 한 카드. MCP/스킬 타임라인은
  점 찍힌 이름 앞에 평범한 말을 붙인다.

## Changed

- 콤팩트 프롬프트가 `name`/`arguments` 봉투를 허용한다고 한 줄로 말한다.
- 12.1.0 Fast Path 인제스트 겹치기·한 방 embed·auto-HNSW는 그대로다.

## Fixed

- labeled 샐비지가 `mcp.` / `skill.` 점 찍힌 이름을 잡담으로 거절하던 것.

## Known Issues

- 작은 모델의 *내용* 품질은 여전히 fail-closed다. 형식은 살렸고, 얇은
  요약은 비평가가 막는다.
- `api_key` 클라우드 경로는 모의 검증만.
- DMG는 ad-hoc 서명.
- 기본 검색 env는 여전히 `brute`.
- watch는 자동 삭제하지 않는다.
- 실임베딩 기본 롤아웃은 아직 닫히지 않았다.

## Follow-up

- HNSW를 env 기본으로 뒤집으려면 연속 인제스트 Brain에서 recall@10을
  다시 재야 한다.
- 사이드카 쿼리가 벡터 테이블 전체를 읽는 점, SkipByHash가 스탬프를
  다시 안 찍는 점은 다음 컷.
