# 개인정보 및 데이터 정책

## 요약

**Lattice AI는 데이터를 수집하거나 외부 서버로 전송하지 않습니다.**

모든 데이터는 사용자의 로컬 머신에만 저장됩니다.

## 저장되는 데이터

| 데이터 | 저장 위치 | 설명 |
|--------|-----------|------|
| 사용자 계정 | `~/.ltcai/users.json` | 이름, scrypt 해시 비밀번호, 역할 |
| 세션 토큰 | `~/.ltcai/sessions.json` | UUID 토큰, 만료시간 |
| 채팅 히스토리 | `~/.ltcai/chat_history.json` | 사용자-AI 대화 내용 |
| 지식 그래프 | `~/.ltcai/knowledge_graph.sqlite` | 채팅/문서 노드/엣지 |
| 업로드 파일 | `~/.ltcai/knowledge_graph_blobs/` | 원본 PDF/DOCX 등 |
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
