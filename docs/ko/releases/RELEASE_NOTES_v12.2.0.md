[![English](https://img.shields.io/badge/English-Switch-475569?style=flat-square)](../../releases/RELEASE_NOTES_v12.2.0.md) [![한국어](https://img.shields.io/badge/한국어-현재_언어-2563eb?style=flat-square)](./RELEASE_NOTES_v12.2.0.md)

# Lattice AI v12.2.0 — Small Voice (2026-08-29)

> **상태: historical** — 해당 시점의 보존된 릴리스 노트입니다.

2B급 소형 로컬 모델이 MCP 도구, 스킬, 네이티브 액션을 선택하지 못하던 공백을 해소한 릴리스입니다. 쓰기 엔진은 단일 `GraphWriter`를 유지하며, 홈 화면에서도 이러한 워크플로를 쉬운 언어로 제안합니다.

| | |
| --- | --- |
| 테마 | **Small Voice** — 작은 모델도 도구를 말할 수 있도록 |
| 진입점(Door) | 네이티브 422개 오퍼레이션 / 41개 패밀리 (변경 없음) |
| 워커(Worker) | 20개 라우트 (변경 없음) |

## 추가된 기능

- 액션 파서가 다양한 함수 호출 엔벨로프를 이해합니다: `{"name", "arguments"}`, 중첩 `function`, Qwen `<function=read_file><parameter=path>...`, 그리고 `Action: mcp.grep`. 복구 내역은 트레이스에 `alias_keys` / `args_string` / `xml_call` / `labeled`로 기록됩니다.
- `compact` 액션 목록 상한을 `guided` 메뉴와 동일한 9개로 통일했습니다. 요청에 명시된 액션은 맨 앞에 배치되고 `final`은 맨 끝에 유지됩니다.
- 스킬의 `when` 문구가 요청과 일치하면 사용자가 카탈로그 이름을 직접 언급하지 않았더라도 에이전트가 `SKILL.md`를 먼저 읽습니다.
- 따옴표로 감싼 검색어가 누락된 `pattern`/`query` 인자를 자동 보완합니다.
- 홈 컴포저 추천 칩(폴더에서 찾기, 파일 읽고 요약하기, 기억 검색, 스킬로 검토)과 설정 화면의 대응 설명 카드를 추가했습니다. MCP/스킬 타임라인에서는 점(`.`)으로 구분된 도구 이름 앞에 쉬운 언어 라벨을 붙입니다.

## 변경된 사항

- `compact` 프롬프트에 `name`/`arguments` 엔벨로프가 허용된다는 안내 한 줄을 명시했습니다.
- 12.1.0 Fast Path의 중첩 수집, 단일 호출 임베딩, 자동 HNSW 동작은 그대로 유지됩니다.

## 수정된 버그

- 라벨 기반 복구(labeled salvage)가 점(`.`)이 포함된 `mcp.` / `skill.` 액션 이름을 잡담(chatter)으로 오인해 거부하던 문제를 수정했습니다.

## 알려진 이슈

- 소형 모델의 *콘텐츠* 품질은 여전히 fail-closed로 동작합니다: 구조는 복구되지만 빈약한 요약은 여전히 비평가(critic)가 차단합니다.
- `api_key` 클라우드 경로는 목(mock) 검증만 수행합니다.
- macOS DMG는 ad-hoc 서명 상태입니다.
- 벡터 검색 환경변수 기본값은 여전히 `brute`입니다.
- `watch`는 파일을 자동 삭제하지 않습니다.
- 실제 시맨틱 임베딩 기본 전환은 아직 완료되지 않았습니다.

## 후속 과제

- `hnsw`를 환경 기본값으로 전환하려면 지속적으로 수집된 브레인에서 `recall@10`을 재측정해야 합니다.
- 사이드카 쿼리가 전체 벡터 테이블을 읽던 동작과 `SkipByHash`가 스탬프를 갱신하지 않던 문제는 다음 릴리스로 예약되었습니다.
