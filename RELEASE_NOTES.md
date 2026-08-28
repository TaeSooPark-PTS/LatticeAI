# [v12.1.0 - Fast Path] (2026-08-29)

폴더 인제스트와 vault-watch가 파일을 겹쳐 돌리고, 노트당
`/worker/embed`는 한 방이며, rebuild는 쓰기 락 밖에서 해시하고, 512행
이상 Brain은 묶인 워커 시임이 있으면 `hnsw+rescore`를 먼저 시도합니다.
문은 422 / 41, 워커는 20. 주간 pip-audit와 Postgres CI가 깨끗한 런에서
빨개지던 것을 고쳤습니다.

See [RELEASE_NOTES_v12.1.0.md](RELEASE_NOTES_v12.1.0.md).

# [v12.0.0 - Open House] (2026-08-18)

가장 큰 두 크레이트를 도메인으로 나눴습니다 — `lattice-agent` 43파일이
`kernel`/`parse`/`content`/`tools`/`surface`/`prompts` 여섯 그룹으로,
`lattice-platform` 31개 평면 모듈이 일곱 도메인으로(100건 `git mv`,
동작 변화 0). 두 크레이트 모두 크레이트 로컬 `ARCHITECTURE.md`를 싣고,
`docs/DEVELOPMENT.md`는 기여자 온보딩으로 재작성됐으며
`docs/ROADMAP.md`가 새로 생겼습니다. 11.9.0이 적어 둔 갭 네 개를
닫았습니다: 복원이 스토어 에폭으로 즉시 반영(재시작 불필요),
`/setup/install`이 서버 도출 allowlist 항목에 한해 동의 기반 실행,
`POST /mcp`가 OpenAPI 계약 안으로(네이티브 마운트), 포인터 도구가
`ltcai[pointer]`로 선언. 그래프 RAG는 한국어 조사 인지·섹션 트리·
타입드 엣지 생산과 함께, 무변경 재인덱스 33s → 0.26s·드레인 ~66 →
~1,300 items/s·백로그 991건 15.3초를 얻었고 HNSW가 `hnsw+rescore`로
실제 검색에 쓰입니다(기본은 여전히 brute). GUIDED 모드는 작은 모델에게
JSON을 요구하지 않으며 Qwen 0.5B가 3.9초에 실파일로 DONE에 닿았습니다.
닫지 않은 것은 노트에 그대로 적었습니다.

See [RELEASE_NOTES_v12.0.0.md](RELEASE_NOTES_v12.0.0.md).

# [v11.9.0 - Working Order] (2026-08-17)

문서에만 있던 13개 Current 스텁을 실동작으로 올리고, 라이브 감사에서
깨진 N1–N9와 이전에 고장난 22항목을 다시 통과시켰습니다. 하이브리드
클라우드는 ReviewSink·EgressAudit이 프로덕션에 묶이고, `api_key`(모의
검증만)와 `cli_oauth`(`agy`/`grok`, 과금 0원 E2E) 이중 자격증명과
`auto`/`manual`/`always` 에스컬레이션을 갖습니다. `POST /mcp`가
streamable-HTTP JSON-RPC 실서버이고, 2B(gemma-4-e2b)가 compact
프로파일로 돌아가며, 채팅 파일 생성(v9.2.0 헤드라인)이 복원됐습니다.
닫지 않은 여섯 가지는 노트에 그대로 적었습니다.

See [RELEASE_NOTES_v11.9.0.md](RELEASE_NOTES_v11.9.0.md).

# [v11.8.0 - Travel Light] (2026-08-16)

11.7.0이 백로그를 비운 뒤 남아 있던 무게를 덜어냈습니다. 호출자가 없던 아홉
라우트를 end-to-end로 지워 워커 표면은 **28 → 19**가 되었고(`pypdfium2`
의존성도 함께 빠짐), 약 191개 파일을 덮고 있던 Rust blanket `#![allow]`
헤더를 걷어 드러난 약 650건의 진단을 원인에서 고쳤습니다(워크스페이스 허용
0건 추가, 지역 억제 8개). 702행짜리 판정 그리드 둘은 이름 붙은 단위 테스트로
대체하고 남은 둘은 171 대표행 + 드리프트 가드로 줄였으며, 테스트 바이너리는
98 → 56, 로컬 lint 체인은 13 → 10, `agent-smoke.yml`은 이중 fail-open이라
삭제했습니다. 그 과정에서 실제 버그 하나 — 워커가 기동 이후의 로그인을
보지 못하던 `SessionStore` — 를 고쳤고, Brain Chat Home을 자라는 Brain과
히어로 컴포저로 다시 그렸습니다. 커버리지 강제 바닥이 100 → 90(라인)으로
내려간 것을 포함해, 닫지 않은 것은 노트에 그대로 적었습니다.

See [RELEASE_NOTES_v11.8.0.md](RELEASE_NOTES_v11.8.0.md).

# [v11.7.0 - Clean Sweep] (2026-08-15)

11.6.0이 공개로 남긴 구멍을 닫았습니다. command-search knowledge가
결과를 돌려주고, 스누즈는 offset-aware를 받으며(잘못된 until은 422),
이중 거절은 409입니다. 바이너리 업로드는 `/worker/parse`, 청크 벡터는
네 문, 사용자 훅이 네이티브 도구에서 발화하고, `sanitize_write_content`가
쓰기 경로에 걸리며, 리뷰 이벤트가 모든 변이 경로에서 기록되고,
`workspace_os.json`은 writer가 하나입니다. One Door가 몰랐던 회귀 —
Self-Model 쓰기 전면 정지, xlsx 502, chat/vault-watch 좌초 시임 — 을
고쳤고, chronicle·briefing·insights 시계 시한폭탄을 해체했으며, 표면을
elevation 언어로 다시 그렸습니다(유리 없음, ~103 KiB). 남은 구멍은
노트에 그대로 적었습니다.

See [RELEASE_NOTES_v11.7.0.md](RELEASE_NOTES_v11.7.0.md).

# [v11.6.0 - One Door] (2026-08-15)

제품 서버가 Rust 하나가 되었습니다: `lattice-host`가 **네이티브 420
오퍼레이션 / 41 라우트 패밀리**를 원래 경로에 서빙하고, Python은 웹
애플리케이션이 아니라 **28 라우트의 AI 워커**(LLM·스트림, embed, extract,
parse, render×4, ASR, multimodal-describe, models/engines, sysinfo, health)로
남습니다 — allowlist 밖은 프록시가 아니라 404, 드리프트 게이트가 지킵니다.
모든 쓰기가 네이티브 KG write 엔진으로 넘어갔고(32단계 행 단위 패리티 ·
`sqlite_master` 67객체 스키마 대조 · 그래프 테이블 17개 소유권 이전),
녹화된 **HTTP 골든 1,487 케이스**를 재생해 표면을 대조합니다. Python
**298 파일 / 73,617줄** 삭제, 남은 127 파일은 문·분기 **100.00%**.
포트가 찾은 결함(KG write 4 · 리댁션 2 · 폴더 재구성 제안 승인 불가)은
고쳤고, **Telegram 브리지와 SSO OIDC 로그인/콜백 제거**, 그대로 이식한
오라클 버그 3건과 남은 구멍은 노트에 전부 적었습니다.

See [RELEASE_NOTES_v11.6.0.md](RELEASE_NOTES_v11.6.0.md).

# [v11.5.2 - Tight Ship] (2026-08-12)

정착된 11.5.1 트리 3중 감사(Rust↔Python 중복 지도 · 아크 단위 죽은 코드 ·
라이브 front-door 패리티 192 엔드포인트)의 결과만 실행: 약 1,100줄 삭제
(shim 6·미배선 시임·호출자 0 심볼 27·Electron 셸)와 재발 금지 가드,
임베더/워크스페이스 선택자/SSE·sha256 헬퍼/Rust 사본 7건 단일화,
현관문 7종 결함 수정(리다이렉트 Set-Cookie 보존·네이티브 레인 fail-closed·
X-Forwarded-* 홉·CORS 주입), 도달 불가였던 `POST /api/search/graph`와
멀티모달 상태 라우트 배선, `recent_chat` 골든이 잡아낸 실제 발산 수정.
플로어: 7,022+1,761 테스트 · 100.00% · Rust 760 · 골든 251.

See [RELEASE_NOTES_v11.5.2.md](RELEASE_NOTES_v11.5.2.md).

# [v11.5.1 - Rust Full Loop] (2026-08-12)

11.5.0의 명시적 잔여 완결: 에이전트 루프 오케스트레이터 Rust 이식(실
런타임 대본 재생 궤적 10종 byte-identical + 실워커 라이브 스모크),
AI Worker 시임 3종(불변 가드 유지), 문서 생성 컨텍스트 네이티브(골든
총 247). 다이어그램의 모든 Rust 박스 구현 — Python은 설계된 AI Worker로.
플로어: 7,006+1,761 테스트 · 100.00% · Rust 739.

See [RELEASE_NOTES_v11.5.1.md](RELEASE_NOTES_v11.5.1.md).

# [v11.5.0 - Rust Complete] (2026-08-11)

Rust 로드맵 Phase 2·3·4 완결: 데스크톱 front-door 기본화(CSRF env 주입
라이브 증명), lattice-ingest(청킹 골든 332·뮤테이션 26/26)·lattice-jobs
(드레인 스케줄러, 문서화된 갭 해소)·lattice-agent(판정 2,452 완전 일치
+ 읽기 전용 네이티브 실행)·retrieval 확장(패리티 191/191). Python은 AI
Worker로 수렴(파서·임베딩 생산·LLM·변이·그래프 쓰기 소유). 플로어:
6,861+1,761 테스트 · 100.00% · Rust 534.

See [RELEASE_NOTES_v11.5.0.md](RELEASE_NOTES_v11.5.0.md).

# [v11.4.0 - Rust Foundation] (2026-08-11)

Rust 전환 Phase 1: `rust/` workspace 3크레이트 — 같은 SQLite를 읽는
lattice-core(임베더 bit-for-bit), 네이티브 검색 lattice-retrieval(패리티
75/75 완전 일치 + Python 계약 80테스트 양방향 잠금), 수퍼바이저+게이트웨이
lattice-host(HTTP 헬스·자동 재시작·포트 통일·SSE 프록시, 옵트인). Tauri
셸이 수퍼바이저에 올라탐(451→149줄, IPC 계약 보존). CI rust 잡 신설.
플로어 유지: 6,643+1,761 테스트 · 100.00% · Rust 194.

See [RELEASE_NOTES_v11.4.0.md](RELEASE_NOTES_v11.4.0.md).

# [v11.3.0 - Time Remembers] (2026-08-11)

기억의 연대기(Brain Chronicle) — 성장 곡선·활동 히트맵·그날의 이야기·
`as_of` 되감기를 갖춘 7번째 주 화면으로 11.1.0 temporal 데이터의 첫 UI.
동시에 1,000줄 초과 파일 28개 전부 분해(AST 동등성·CSS byte-identical·
i18n 키맵 동일 증명, 동작 변화 0) + 라인 상한 lint 게이트. 플로어 유지:
6,560+1,761 테스트 · 40,488문·11,052분기 100.00% · 프론트 4지표 100%.

See [RELEASE_NOTES_v11.3.0.md](RELEASE_NOTES_v11.3.0.md).

# [v11.2.0 - All Systems On] (2026-08-11)

모델 카탈로그 전수 최신화(HF API 무부하 검증 — 가중치 다운로드 0·실로드 0,
사라진/게이트/구세대 제거, 추천 10종 전부 2025–2026 세대), 홈 dock "기능"
서랍의 opt-in 10종 라이브 토글(사용자>env>기본), 스코프 아웃 전면 해소
(Notion/Git/메일·캘린더 브릿지 · X25519 수신자 암호화 · 비디오 · 볼트 감시
· 일괄 승인 등), 58행 증거 감사로 죽은 기능·미배선 7건 수정. 플로어 유지:
6,490 테스트 · 39,054문·11,014분기 100.00%.

See [RELEASE_NOTES_v11.2.0.md](RELEASE_NOTES_v11.2.0.md).

# [v11.1.0 - Product Intelligence] (2026-08-10)

기초 위의 지능 레이어 — 5개 트랙 완성: 플러그형 벡터 인덱스로 하이브리드
p50 299ms → 10.1ms(10k), 모순→제안→temporal 스탬프(`as_of` 지원), 이미지·
녹음의 1등 노드화(기본 꺼짐, 캡션 조작 스텁 삭제), Self-Model 서브그래프
(제안-우선, 투명 소유), Obsidian 브릿지 + 서명된 서브그래프 공유 프로토
타입(기본 꺼짐). 플로어 유지: 6,261 테스트 · 37,590문·10,658분기 100.00%.

See [RELEASE_NOTES_v11.1.0.md](RELEASE_NOTES_v11.1.0.md).

# [v11.0.1 - Both Branches] (2026-08-10)

11.0.0이 기록한 결함 11건을 전부 수정하고(고정 테스트 반전 + 회귀 테스트),
증명된 죽은 코드를 제거하고, CI 플로어를 **라인 + 분기 100%**로 올렸습니다
(`branch = true`, 9,828아크, 테스트 5,798개). linux 컨테이너 검증이 드러낸
개발 머신 우연 커버 3아크도 밀폐 테스트로 교체. 화면 변경 없음.

See [RELEASE_NOTES_v11.0.1.md](RELEASE_NOTES_v11.0.1.md).

# [v11.0.0 - Full Measure] (2026-08-10)

출하되는 모든 Python 라인이 테스트 아래에서 실행됩니다 — 커버리지 72.80% →
**100.00%**, 테스트 2,269 → 5,426개(+3,157), `fail_under = 100` CI 게이트.
제외는 사유가 명시된 `pragma: no cover` 8줄이 전부이고, 플랫폼 잠금 분기도
페이크 모듈로 ubuntu CI에서 실행됩니다. 커버리지 작업이 드러낸 실제
결함들은 고치지 않고 기록했습니다 — 검증 릴리스는 동작 변경을 싣지
않습니다. 화면 변경 없음.

See [RELEASE_NOTES_v11.0.0.md](RELEASE_NOTES_v11.0.0.md).

---

# Release Notes

This repository keeps public release history from **11.0.0 through 12.1.0**.
11.6.0 rebuilt the product server in Rust and reduced the Python package to an AI
worker, so `SECURITY.md` supports only that era — 11.x and 12.x — and this index
follows the same boundary. Sub-11 note files remain in the tree as history; they
are no longer listed here as supported releases.

## Current Release

- [v12.1.0 - Fast Path](RELEASE_NOTES_v12.1.0.md)
- [v12.0.0 - Open House](RELEASE_NOTES_v12.0.0.md)

## Recent Release Notes

- [v11.9.0 - Working Order](RELEASE_NOTES_v11.9.0.md)
- [v11.8.0 - Travel Light](RELEASE_NOTES_v11.8.0.md)
- [v11.7.0 - Clean Sweep](RELEASE_NOTES_v11.7.0.md)
- [v11.6.0 - One Door](RELEASE_NOTES_v11.6.0.md)
- [v11.5.2 - Tight Ship](RELEASE_NOTES_v11.5.2.md)
- [v11.5.1 - Rust Full Loop](RELEASE_NOTES_v11.5.1.md)
- [v11.5.0 - Rust Complete](RELEASE_NOTES_v11.5.0.md)
- [v11.4.0 - Rust Foundation](RELEASE_NOTES_v11.4.0.md)
- [v11.3.0 - Time Remembers](RELEASE_NOTES_v11.3.0.md)
- [v11.2.0 - All Systems On](RELEASE_NOTES_v11.2.0.md)
- [v11.1.0 - Product Intelligence](RELEASE_NOTES_v11.1.0.md)
- [v11.0.1 - Both Branches](RELEASE_NOTES_v11.0.1.md)
- [v11.0.0 - Full Measure](RELEASE_NOTES_v11.0.0.md)

## Canonical History

The canonical per-release history is maintained in:

- [RELEASE.md](RELEASE.md)
- [docs/CHANGELOG.md](docs/CHANGELOG.md)

Note files for releases older than 11.0.0 are still present in the tree, but they
describe a product whose server was a Python application. They are history, not
supported versions.
