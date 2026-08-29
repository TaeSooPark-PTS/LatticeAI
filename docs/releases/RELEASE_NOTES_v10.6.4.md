# Lattice AI 10.6.4 — Loud Limits

> **Status: historical** — point-in-time release note.

릴리스일: 2026-08-04

## 한 줄 요약

**10.6.3이 연 Loud Limits 라인을 닫는 패치입니다.** 동작 계약은 그대로 두고,
패키지 메타데이터·현재 릴리스 문서·릴리스 증거를 **10.6.4** 한 줄로 맞춥니다.
버전 문자열이 어긋난 채 게이트만 초록이던 상태를 없앱니다.

## 왜 이 릴리스인가

버전 범프만 먼저 들어가면 `test_product_is_release_complete`가 바로 붉어집니다.
packaging / trust-docs / ecosystem-path 세 게이트는 README의 exact artifact
이름, `RELEASE_NOTES_v10.6.4.md`, CHANGELOG 섹션, 커뮤니티 문서의 현재 버전
표기를 디스크에서 찾습니다. 이 릴리스는 그 증거를 채웁니다.

## 무엇이 바뀌었나

### 문서와 패키징 정렬

- README 현재 릴리스 문장과 `dist/ltcai-10.6.4-*` / `ltcai-10.6.4.tgz` 아티팩트
  목록
- `docs/CHANGELOG.md`의 `## [10.6.4]` 섹션
- 이 파일(`RELEASE_NOTES_v10.6.4.md`)과 커뮤니티·VS Code 확장 현재 버전 표기
- 릴리스 증거 디렉터리 `output/release/v10.6.4/`

### 승인함 캡처 계약 (목 서버)

`tests/visual/mock_server.cjs`의 `/permissions/pending`은 두 행을 유지합니다.

1. mapped 경로: `action: "read"`, `action_label: "파일 읽기"`
2. unmapped 경로: `action: "delete"`, `action_label: "delete"` (F1 회귀 대상)

두 번째 행을 지워 캡처를 예쁘게 만드는 것은 금지입니다. 미매핑 액션이 UI에
어떻게 보이는지 가려지기 때문입니다.

### 스키마

없음. SQLite 기존 DB 읽기/쓰기 경로 변경 없음.

## 검증

- `product_readiness` packaging · trust-docs · ecosystem-path 가 10.6.4 증거를
  찾는지
- `npm run lint` exit 0
- `npm test` 실패 0 (F1 프론트 i18n 착지 전에는 캡처 09의 원문 키 노출이 남을
  수 있음 — 문서 게이트와 별개)

## 업그레이드 노트

데이터 마이그레이션 없음. 10.6.3 Brain DB를 그대로 엽니다.
