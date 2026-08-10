# v11.2.0 — All Systems On (2026-08-11)

11.1.0이 지능 레이어를 지었다면, 11.2.0은 **그 전부가 실제로 작동하고,
오늘의 모델을 싣고, 스위치를 사용자 손에 쥐여 주는** 릴리스입니다.
모델 검증은 요청 원칙대로 **사용자 컴퓨터 무부하** — 가중치 다운로드 0,
실로드 0, 전부 Hugging Face API 메타데이터와 정적 판정으로 수행했습니다.

## 1. 모델 카탈로그 — 전수 점검·최신화 (M1)

**추천 10종 (전부 2025–2026 세대, 정적 검증 18/18 통과):**

| 모델 | 크기 | 티어 |
| --- | --- | --- |
| LFM2.5-2.6B-4bit (한국어 지원) | 1.5GB | 초경량 ≤8GB |
| gemma-4-e2b-it-4bit | 3.6GB | 초경량 ≤8GB |
| gemma-4-e4b-it-4bit | 5.2GB | 경량 16GB |
| Qwen3.5-9B-MLX-4bit (VLM) | 6.0GB | 중형 24GB |
| gemma-4-12B-it-4bit | 6.8GB | 중형 24GB |
| gpt-oss-20b-MXFP4-Q8 | 12.1GB | 범용 |
| gemma-4-26b-a4b-it-4bit (MoE) | 15.4GB | MoE 32GB+ |
| Qwen3.6-27B-4bit | 16.1GB | 대형 48GB+ |
| gemma-4-31b-it-4bit | 18.4GB | 대형 48GB+ |
| Qwen3.6-35B-A3B-4bit (MoE) | 20.4GB | MoE 32GB+ |

- **삭제**: Hub에서 사라진 2종(phi-3.5-vision-4bit, moondream2-4bit —
  익명 401/미존재 실측), gated 3종(gemma-3 계열, Llama-3.2-Vision 원본),
  vllm 전용 1종(Pixtral-12B-2409), 구세대 1종. 얻을 수 없는 것을
  인식 목록에 두는 것은 호환이 아니라 소음이므로 완전 제거.
- **인식 전용 8종 유지**: 이미 내려받은 사용자의 구모델(Qwen3-VL 계열
  등)은 로드 인식·런타임 프로파일을 유지 — 로컬 가중치를 고아로 만들지
  않습니다.
- **검증기 재작성**: `--deep`/`--test-load` 제거(도구가 무부하 원칙을
  위반할 수 없게 됨). 정적 판정 = mlx 라이브러리/태그 + 지원 아키텍처
  상수 대조 + config/tokenizer/safetensors 실존 + 정확한 대소문자 +
  바이트 합 대조. 한계("로드가 배제되지 않음"이지 "로드됨"이 아님)를
  스크립트 출력·리포트·문서에 명시. verification_report.json 동봉
  (gitignore돼 CI에서 항상 skip되던 것도 수정).
- **부수 결함 8건 수정**: Llama Scout 크기 11.8GB→실제 61.1GB(RAM 요구
  16→72), 조작된 ollama 경로 제거, gated 별칭 제거, 마이너 버전 필터가
  Qwen3.5를 지우던 버그, 프론트 폴백이 은퇴 모델을 가리키던 것 등.

## 2. 기능 스위치보드 — Brain 홈의 "기능" 서랍 (T7)

- 홈 dock 4번째 레일 항목 **기능** → 포커스 트랩 서랍에 **서버가 렌더하는
  카탈로그 10종**: 멀티모달 기억 · 비디오 · 브레인 네트워크 공유 · 볼트
  감시 · 사진 의미 검색 · RRF 융합 · 이웃 확장 · 자동 합성 · 배경 인덱싱
  · 벡터 백엔드 선택. 각 항목에 평문 설명, 현재 출처(default/env/사용자),
  라이브 스위치. 미설치 백엔드(hnsw)는 "설치 필요"로 정직하게 비활성.
- 우선순위 사용자 > env(시드) > 기본. **만진 스위치만 발화** — 손대지
  않은 설치는 진단 메시지까지 바이트 동일(정밀 우선순위 설계).
- 토글은 재시작 없이 즉시 반영(주입형 FeatureGate 시임) — 멀티모달 on이
  실제 인제스트 라우팅을 바꾸는 것까지 엔드투엔드 12종으로 단언.
- 접근성(role=switch, 키보드, aria-live), one-viewport·액센트 2곳 계약
  불변(캔버스에 카드 추가 없음), ko/en 패리티.

## 3. 스코프 아웃 전면 해소 (F2)

- **브릿지**: Notion export(zip/디렉터리, id 접미사 정규화, 페이지 링크→
  엣지) · Git 히스토리(커밋=노드, 해시 멱등) · 메일(.eml)/캘린더(.ics,
  stdlib 파서, 새 의존성 0) — 전부 단일 인제스트 게이트+승인+dry_run.
- **수신자 공개키 암호화**: X25519+HKDF+AES-GCM sealed box(번들마다
  ephemeral 키 → 전방향 비밀성), 수신 키는 서명용 Ed25519와 분리,
  passphrase 방식과 병행(정확히 하나만 허용).
- **비디오 인제스트**: ffmpeg 가드 키프레임 → 기존 이미지 경로,
  .srt/.vtt 자동 동반, NodeType.VIDEO. ffmpeg 없으면 사유를 명시한 거부.
- **잔여 한계**: 볼트 감시 모드, 일괄 승인(`bulk/approve|dismiss`, 항목별
  판정), Self-Model 요약의 에이전트 루프 도달, 텍스트→이미지 자동 late
  fusion, HNSW 신선도 커버링 인덱스, kgv2_edges의 `COALESCE(NULLIF(...))`
  수정(''는 UNIQUE 중복제거 키라 NULL화 대신 읽기 정규화 — 정확한 원인
  분석), 죽은 자동화 저신뢰 게이트 복원, 테스트 HOME 오염 위생(수집 전
  샌드박스).

## 4. 전 기능 증거 감사 (A3 + T8)

58행 전수 검증([docs/FEATURE_AUDIT_v11.2.0.md](docs/FEATURE_AUDIT_v11.2.0.md)):
51 작동 · 수정 7 — 오늘의 브리핑 건강 섹션이 영구 공백이던 죽은 기능
(가짜 픽스처가 가림), 클라우드 유래 지식이 리뷰 센터에 도달하지 않던
write-back 미배선(+auto_commit 정책 연결), 빈 Brain이 "0의 100%"로
100점/excellent를 받던 채점, freshness 세분화 미노출, 멀티모달
context_quality 신호 미배선, 과장 문서 3건의 사실 하향. 횡단 스모크:
라우터 35 · 라우트 451 · OpenAPI 411 경로 · 5xx 0. grok 계열 사어 id는
현행 grok-4.5로 정리.

## 검증

| gate | result |
| --- | --- |
| pytest (라인+분기 100 플로어) | **6,490 passed · 100.00%** (39,054문 · 11,014분기) ×3 |
| linux python:3.14 컨테이너 (ffmpeg 포함) | 통과 (아래 교차 검증) |
| fresh python 3.11 venv | 통과 (unit) |
| vitest | 1,671 passed · 100% 4지표 |
| playwright 비주얼 | 35/35 (기능 서랍 스펙 포함) |
| mypy | 297 / 297 modules, 0 errors |
| ruff · OpenAPI drift · i18n · 번들(103KiB/150) | 전부 클린 |
| HF 모델 검증 | 18/18 · 가중치 다운로드 0 · 실로드 0 |

## 정직한 한계

- 모델 "로드 가능" 판정은 정적입니다 — mlx 커뮤니티 배포·아키텍처 지원
  표·파일 구성으로 "로드를 배제할 근거 없음"을 말하는 것이지, 이 기기에서
  실로드를 재현한 것이 아닙니다(사용자 컴퓨터 무부하 원칙).
- 시스템 연동형 브릿지(IMAP/Notion API/macOS 권한)는 여전히 파일 기반
  export 인제스트입니다 — 남은 범위는 FEATURE_STATUS에 명시.
- HTML 전용 메일은 본문 대신 `body_status: "html_only"`로 표시(내비게이션
  잔여물을 본문으로 저장하지 않기 위함).
- 감사 리포트는 스윕 시점 기록이며, 이후 수정은 처분(disposition) 줄로
  덧붙였습니다 — 스스로를 조용히 고쳐 쓰는 감사는 가치가 없기 때문.

## Artifacts (exact filenames)

- `dist/ltcai-11.2.0-py3-none-any.whl`
- `dist/ltcai-11.2.0.tar.gz`
- `ltcai-11.2.0.tgz`
- `dist/ltcai-11.2.0.vsix`
- `src-tauri/target/release/bundle/dmg/Lattice AI_11.2.0_aarch64.dmg`

와일드카드 업로드는 사용하지 않습니다. 패키지 스토어 배포는 owner-run입니다.
