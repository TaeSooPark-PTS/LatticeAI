# 개인정보 및 데이터 정책

## 요약

**Lattice AI는 기본 상태에서 텔레메트리를 수집하거나 사용자 데이터를 외부로
전송하지 않습니다.** 사용자가 명시적으로 선택한 클라우드 모델, 모델 다운로드,
웹 페이지 읽기, Telegram/Brain Network 같은 외부 기능은 해당 대상과 통신합니다.

모든 데이터는 사용자의 로컬 머신에만 저장됩니다.

## 저장되는 데이터

| 데이터 | 저장 위치 | 설명 |
|--------|-----------|------|
| 사용자 계정 | `~/.ltcai/users.json` | 이름, scrypt 해시 비밀번호, 역할 |
| 세션 토큰 | `~/.ltcai/sessions.json` | SHA-256 토큰 해시, 만료/갱신 시각(평문 bearer 미저장) |
| 채팅 히스토리 | `~/.ltcai/chat_history.json` | 사용자-AI 대화 내용 |
| 지식 그래프 | `~/.ltcai/knowledge_graph.sqlite` | 채팅/문서/웹/탭 노드·엣지, 프로비넌스 |
| 업로드/수집 파일 | `~/.ltcai/knowledge_graph_blobs/` | 원본 PDF/DOCX/웹 텍스트 등 |
| 수집 출처 기록(프로비넌스) | `~/.ltcai/knowledge_graph.sqlite` (`ingestion_provenance`) | 각 노드의 출처/시각/처리 방식 — 외부 전송 없음 |
| 그래프 내보내기/백업 | `~/.ltcai/workspace_exports/` | 사용자가 직접 만든 로컬 export/backup 파일 (클라우드 미사용) |
| 지식 정원 | `~/.ltcai-brain/` | P-Reinforce 분류 저장 |
| 설정 | `~/.ltcai/config.json` | 모델 설정, API 키 (keyring) |

## 수집하지 않는 데이터

- 사용 통계, 세션 길이, 클릭 이벤트
- 오류 리포트 (로컬 `server.log`에만 기록)
- 기기 정보, IP 주소
- 프롬프트/응답 내용

## 클라우드 모델 사용 시

사용자가 직접 OpenAI, Groq, Together, OpenRouter 등의 API 키를 설정하고 클라우드 모델을 선택한 경우, 해당 프롬프트와 응답은 각 클라우드 제공업체의 서버를 경유합니다. 이는 각 제공업체의 개인정보처리방침을 따릅니다.

- OpenAI: https://openai.com/privacy
- Groq: https://groq.com/privacy-policy
- Together AI: https://www.together.ai/privacy
- OpenRouter: https://openrouter.ai/privacy

Apple Silicon MLX 로컬 모델 사용 시에는 프롬프트가 외부로 전송되지 않습니다.

## Telegram 및 권한 알림

Telegram bridge는 사용자가 명시적으로 활성화하고 chat ID를
`LATTICEAI_TELEGRAM_ALLOWED_CHAT_IDS`에 등록한 경우에만 해당 chat의 메시지와
callback query를 처리합니다. 로컬 Lattice API 호출에는 전용
`LATTICEAI_SERVER_SESSION_TOKEN`을 사용하며 세션 저장 파일에서 bearer를 찾지
않습니다.

Discord 등의 권한 요청 알림에는 전체 승인 토큰이 아닌 짧은 hint만 포함됩니다.
운영자가 `LATTICEAI_PERMISSION_UI_URL`을 설정하면 검토 페이지 링크를 제공할 수
있지만, 승인 토큰 자체는 메시지나 URL에 넣지 않습니다.

## 웹/브라우저 수집 (v3.6.0)

- **URL 읽기**(`/api/browser/read-url`): 사용자가 명시적으로 요청한 URL을 **로컬
  런타임이 직접** 가져와 텍스트만 추출해 그래프에 색인합니다. Lattice AI가 임의로
  크롤링하지 않으며, 가져온 페이지는 외부로 다시 전송되지 않습니다. private/local
  주소와 DNS rebinding을 차단하고 redirect마다 다시 검증하며 최대 4MB의 textual
  응답만 스트리밍합니다.
- **브라우저 탭 수집**(`/api/browser/ingest-current-tab`) 및 Manifest V3 확장:
  확장 프로그램은 **오직 `127.0.0.1`(로컬)** 로만 전송합니다. 클라우드 엔드포인트가
  존재하지 않습니다(`browser-extension/` 소스에서 단일 `fetch` 대상 확인 가능).

## 그래프 내보내기/백업 (v3.6.0)

지식 그래프 export/import 및 백업/복원은 **전적으로 로컬**에서 동작하며 클라우드
서비스를 요구하지 않습니다. 내보낸 파일의 이동·공유는 사용자 책임입니다.

## API 키 보안

- API 키는 OS keyring(macOS Keychain, Windows Credential Manager, Linux Secret Service)에 저장됩니다
- `LATTICEAI_ALLOW_PLAINTEXT_API_KEYS=true` 설정 없이는 디스크에 평문 저장되지 않습니다
- 채팅 히스토리 저장 전 API key/token/password 패턴 자동 마스킹

## 데이터 삭제

```bash
# 채팅 히스토리만 삭제
rm ~/.ltcai/chat_history.json

# 지식 그래프 삭제
rm ~/.ltcai/knowledge_graph.sqlite
rm -rf ~/.ltcai/knowledge_graph_blobs/

# 전체 데이터 삭제
rm -rf ~/.ltcai/
rm -rf ~/.ltcai-brain/
```

웹 UI에서: `/clear` 명령 (현재 대화 삭제), 어드민 패널에서 사용자별 데이터 관리

## 퍼블릭 배포 시

외부 사용자가 접근할 수 있는 환경에 배포하는 경우, 사용자에게 다음을 고지하세요:

1. 어떤 데이터가 서버에 저장되는지
2. 사용하는 클라우드 AI 제공업체
3. 채팅 내용의 보존 기간 및 삭제 방법

Lattice AI 자체는 퍼블릭 배포 운영자의 데이터 처리 방식에 대해 책임지지 않습니다.

## 문의

개인정보 관련 문의: rnlgnquvk@gmail.com
