# Lattice AI — 명세(PPT) vs 구현(repo) 매핑

`lattice_ai_full_spec.pptx` 의 사양과 현재 리포지토리 구현 사이의 정렬 상태를 한 장으로 정리한다.
각 항목은 **목표(PPT) → 현재(repo) → 갭 → 보강 위치** 순으로 본다.

---

## 0. 세 가지 약속

| 약속 | 목표 (PPT) | 현재 (repo) | 갭 |
|------|-----------|-------------|----|
| Cross-Platform Parity | Win·macOS·Linux·iOS·Android, 같은 디자인 토큰·컴포넌트 | Web(static) + VSCode ext + Telegram (브라우저 어디서나) | 네이티브 데스크탑/모바일 셸 없음 (PWA 부분 지원) |
| Zero-Config Auto Setup | PROBE → RECOMMEND → INSTALL → VERIFY → PRESET, 90초 내 | `LTCAI doctor` (의존성만 체크 = PROBE의 일부) | GPU/RAM 프로빙, 추천, 설치, 벤치마크, 프리셋 미구현 |
| Everything is a Graph | 10 노드 타입 / 12 엣지 타입 / 임베딩 / 신뢰도+증거 | nodes·edges·chunks 테이블 + 한글 동사 엣지(EDGE_VERB) | 명시 enum·embedding·confidence/evidence·owner 결손 |

---

## 1. 크로스플랫폼

PPT 명세는 "한 코드·다섯 화면" — Shared Core(Design Tokens, UI Components, Business Logic, AI/Graph Core) 위에서 Tauri(데스크탑) / Capacitor·RN(모바일) 렌더러가 같은 결과를 낸다.

**현재 구현**
- `static/chat.html`, `static/graph.html`, `static/admin.html`, `static/account.html` 4개 HTML — 각자 자체 CSS 변수 보유
- `vscode-extension/` — TypeScript VSCode 통합
- `static/manifest.json` + `static/sw.js` — PWA 부분 지원 (iOS/Android 홈 화면 추가는 됨)
- `telegram_bot.py` — Telegram 미러

**갭**
- 데스크탑 네이티브 셸 (Tauri) 미구현
- 모바일 네이티브 (Capacitor / RN) 미구현
- 4개 HTML이 각자 다른 색 토큰 사용 → 같은 화면이 같게 안 보임
- 다국어(i18n) 시스템화 안 됨 (HTML에 한글 하드코딩)

**보강 결과물**
- `static/css/tokens.css` (이번 PR에서 추가) — 4개 HTML이 공유할 단일 진실 토큰
- 로드맵: `apps/desktop/` (Tauri 셸) · `apps/mobile/` (Capacitor 셸) 차후 단계

---

## 2. 자동 환경 세팅

PPT 명세 5단계:

| 단계 | 의미 | 현재 |
|------|------|------|
| ① PROBE | OS · CPU · GPU · RAM · 디스크 · 권한 감지 | `LTCAI doctor` 가 의존성만 체크 |
| ② RECOMMEND | 사양 점수 → 최적 모델 자동 선택 | 없음 |
| ③ INSTALL | OS별 패키지 매니저로 런타임 설치 | 없음 |
| ④ VERIFY | 토큰/초 측정, 첫 응답 지연 검증 | 없음 |
| ⑤ PRESET | 기본/고급 모드 분기 + 단축키/MCP/테마 | 없음 |

**보강 결과물**
- `auto_setup.py` (이번 PR) — 위 5단계를 단일 모듈로 구현
- `LTCAI setup` 서브커맨드 추가 (`ltcai_cli.py` 마이너 패치 또는 별도 진입점)

---

## 3. 지식 그래프

PPT 명세 (점 = 노드, 선 = 엣지):

```
NODE  { id, type∈10종, label, embedding[1024], attrs, createdAt, updatedAt,
        ownerId, visibility }
EDGE  { id, source, target, type∈12종, weight, confidence, evidence[],
        createdBy, createdAt }
```

**현재 구현 (`knowledge_graph.py`)**
```
nodes  ( id, type, title, summary, metadata_json, raw_json, created_at, updated_at )
edges  ( id, from_node, to_node, type, weight, metadata_json, created_at,
         UNIQUE(from_node, to_node, type) )
chunks ( id, source_node, text, metadata_json, created_at )
```

**갭**
- 노드 타입이 enum이 아닌 자유 문자열 (`Code`, `Person`, `Concept`, `Feature`, `Error`, `Message`, `AIResponse` 산발)
- `embedding` 컬럼 부재 → semantic similarity 검색 불가, `SIMILAR_TO` 엣지 추론 불가
- 엣지 타입이 한글 동사 14종 (`언급함`, `포함함` …) — PPT 영문 SCREAMING_CASE 12종과 불일치
- `confidence` / `evidence` 가 metadata_json 안에 비공식적으로 섞임
- `ownerId` / `visibility` 부재 → multi-tenant 권한 정책 불가
- `createdBy` (추출기 출처) 부재 → 디버깅·재추출 안 됨

**보강 결과물**
- `kg_schema.py` (이번 PR) — `NodeType`, `EdgeType` Enum + Pydantic 모델 + 마이그레이션 가이드
- `docs/kg-schema.md` — JSON 예시, 매핑 표
- 기존 코드와의 호환: 신규 모델은 기존 SQLite 와 공존 (별도 v2 테이블), 점진 마이그레이션

---

## 4. 디자인 일관성

| 파일 | 현재 --bg | 현재 --accent |
|------|-----------|---------------|
| `chat.html`   | `#182332` 다크 블루그린 | `#22d3a0` 민트 |
| `graph.html`  | `#282a36` 다크 그레이   | `#a77cff` 라일락 |
| `admin.html`  | `#282a36`               | `#a77cff` |
| `account.html`| `#282a36` + `#f7f3ff` 혼재 | `#a77cff` |
| `lattice-reference.css` | (라이트, PPT) | `#6f42e8` Lattice 보라 |
| **PPT 명세** | `#FFFFFF` 또는 `#0B0B16` | `#6E4AE6` Lattice 보라 |

**보강 결과물**
- `static/css/tokens.css` — 단일 토큰 (PPT 명세 그대로)
- 4개 HTML 의 `:root {}` 블록을 `@import` 한 줄로 대체 가능하도록 토큰 명 호환

---

## 5. SSO·다국어

PPT 화면 1, 13 (login, security) 에 한국어 / Microsoft Entra ID / Okta SSO 가 있음.

**현재**
- `server.py` 의 `/auth/sso` 엔드포인트 존재 (architecture.md 언급) — Entra/Okta 둘 다 명시되어 있는지 확인 필요
- 다국어 — HTML 하드코딩 (`lang="ko"`)

**갭 / 다음 단계**
- i18n 사전 (`static/i18n/{ko,en,ja}.json`) 추출 → PPT 명세 그대로 토큰화

---

## 6. 보강 우선순위 요약

| 순위 | 파일 | 무엇 |
|------|------|------|
| 1 | `docs/kg-schema.md`, `kg_schema.py` | KG 스키마 정식화 (10 노드 · 12 엣지 · embedding · confidence) |
| 2 | `static/css/tokens.css` | 디자인 토큰 통합 (PPT 색팔레트) |
| 3 | `auto_setup.py` | OS 프로빙 + 모델 추천 + 설치 어댑터 |
| 4 | `docs/architecture.md` 보강 | 위 변경 반영 |
| 5 | (차후) `apps/desktop`, `apps/mobile` 스캐폴딩 | Tauri/Capacitor |

각 항목은 이번 PR 에 함께 들어간다 (1~3은 코드, 4는 문서, 5는 청사진만).
