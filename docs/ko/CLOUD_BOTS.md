[![English](https://img.shields.io/badge/English-Switch-475569?style=flat-square)](../../CLOUD_BOTS.md) [![한국어](https://img.shields.io/badge/한국어-현재_언어-2563eb?style=flat-square)](./CLOUD_BOTS.md)

# 클라우드 텔레그램 봇 (Cloud Telegram Bots)

이 프로젝트는 두 개의 분리된 Telegram 봇을 사용합니다.

## 1. 로컬 Lattice AI 봇

목적: 로컬 Lattice AI 서버와 통신하고 웹 대화를 미러링합니다.

필수 환경 변수:

```bash
LATTICEAI_TELEGRAM_BOT_TOKEN=...
LATTICEAI_TELEGRAM_ALLOWED_CHAT_IDS=123456789
LATTICEAI_SERVER_SESSION_TOKEN=...
```

`LATTICEAI_TELEGRAM_ALLOWED_CHAT_IDS`는 채팅 등록 전에 메시지와 콜백 쿼리에 적용되는 쉼표 구분 허용 목록(allowlist)입니다. 비어 있지 않은 허용 목록과 전용 서버 세션 토큰이 없으면 봇은 시작되지 않습니다. `sessions.json`에서 해시값을 복사하지 말고, 브리지용으로 생성된 인증된 세션 베어러 토큰을 사용하십시오.

일반 로컬 서버와 함께 실행:

```bash
CTA
```

## 2. 클라우드 Codex 봇

목적: Telegram에서 Codex 스타일의 개발 어시스턴트와 대화하고 선택적으로 GitHub 이슈를 생성합니다.

필수 환경 변수:

```bash
CODEX_TELEGRAM_BOT_TOKEN=...
OPENAI_API_KEY=...
CODEX_OPENAI_MODEL=gpt-5.4
```

선택적 GitHub 이슈 브리지:

```bash
GITHUB_TOKEN=...
GITHUB_REPO=owner/repo
```

실행:

```bash
python codex_telegram_bot.py
```

Telegram 명령어:

```text
/start
/reset
/issue Title
Issue body here
```

## 토큰 교체 (Token Rotation)

이 저장소를 GitHub에 푸시하기 전에 다음 절차를 수행하십시오.

1. BotFather에서 기존 Telegram 토큰을 폐기(revoke)합니다.
2. 기존 로컬 Lattice AI 봇을 위한 새 토큰을 하나 생성합니다.
3. 새 Codex 봇을 위한 별도의 토큰을 생성합니다.
4. 실제 시크릿은 소스 코드가 아닌 `.env`에 보관합니다.
5. GitHub 저장소를 비공개(private)로 유지합니다.
