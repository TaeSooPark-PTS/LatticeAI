# Changelog

## [0.1.3] - 2026-05-18

### Added
- 프로필 수정 API (`PATCH /account/profile`) 및 UI — 이름·닉네임 변경
- 회원가입 폼 개선 — 비밀번호 확인 필드, 인라인 에러 메시지
- 어드민 패널 초대 링크 섹션 — 원클릭 복사
- 어드민 대시보드 메시지 활동 차트 (Chart.js, 최근 14일)
- 웹 UI 한국어 / 영어 전환 (`🌐 Languages` 버튼, localStorage 저장)

### Fixed
- 로그아웃 시 `/logout` API 호출하여 서버 세션 쿠키 정상 만료
- 인증(`account.html`)과 채팅(`chat.html`) UI 분리 — 레거시 `index.html` 제거
- `chat.html` 내 죽은 인증 코드 제거
- 채팅 헤더에서 언어 선택 드롭다운이 ops-strip을 가리는 문제 수정

---

## [0.1.1] - 2026-05-18

### Added
- 비밀번호 변경 API (`POST /account/change-password`)
- 웹 UI 비밀번호 변경 모달 (헤더 계정 아이콘)

### Docs
- 어드민 패널: 첫 가입자 자동 admin 안내 추가
- 플랫폼 지원 범위 (Windows/Linux) 안내 추가
- 언어 지원 (KO/EN) 안내 추가

### Deployed
- PyPI ✅
- npm ✅
- VS Code Marketplace ✅
- Open VSX ✅

---

## [0.1.0] - 2026-05-17

### Added
- FastAPI 브릿지 서버 (port 4825)
- Apple Silicon MLX 로컬 모델 지원 (Gemma 4, Qwen 2.5 등)
- 클라우드 모델 지원 (OpenAI, Groq, Together, OpenRouter 등)
- VS Code / Cursor / Antigravity 확장
- Telegram 봇 (로컬 AI 미러 + Codex 클라우드 봇)
- 어드민 패널 (`/admin`)
- P-Reinforce 지식 정원 엔진
- MCP 서버 연동
- Ollama / vLLM / LM Studio / llama.cpp 연동

### Security
- 모든 민감 엔드포인트 인증 적용
- SameSite=Lax 쿠키 (CSRF 방어)
- scrypt 비밀번호 해싱
- tempfile 레이스 컨디션 수정
- `run_command()` 위험 플래그 차단

### Deployed
- PyPI ✅
- npm ✅
- VS Code Marketplace ✅
- Open VSX ✅
