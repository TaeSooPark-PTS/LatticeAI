[![English](https://img.shields.io/badge/English-Switch-475569?style=flat-square)](../../PUBLIC_MODE.md) [![한국어](https://img.shields.io/badge/한국어-현재_언어-2563eb?style=flat-square)](./PUBLIC_MODE.md)

# Lattice AI 로컬 / 퍼블릭 모드 (v9.6.0)

Lattice AI는 두 가지 런타임 모드를 지원합니다.

## 로컬 모드 (Local Mode)

Mac에서 MLX-VLM, 로컬 파일, 이미지 분석, 데스크톱 인접 도구를 사용하고자 할 때 사용합니다. Telegram은 옵트인(opt-in) 방식이며 두 가지 보안 설정이 모두 존재하지 않으면 안전하게 비활성화(fail-closed)됩니다.

```bash
LATTICEAI_MODE=local \
LATTICEAI_DATA_DIR="$PWD/.ltcai" \
LATTICEAI_BRAIN_DIR="$PWD/.ltcai-brain" \
python server.py
```

기본값:

- Telegram 브리지: 꺼짐(off)
- 로컬 MLX-VLM 모델: 켜짐(on)
- 기본 모델: `mlx-community/gemma-4-12B-it-4bit`
- 포트: `4825`

## 퍼블릭 모드 (Public Mode)

Render, Fly.io, Railway, VPS 또는 Docker 호스트와 같은 공개 서버에서 사용할 때 선택합니다.

퍼블릭 모드는 Apple Silicon MLX 모델을 로드하지 않으며, OpenAI 호환 클라우드 모델을 사용합니다.

```bash
LATTICEAI_MODE=public \
LATTICEAI_ENABLE_TELEGRAM=false \
LATTICEAI_ALLOW_LOCAL_MODELS=false \
LATTICEAI_DATA_DIR=/data \
LATTICEAI_BRAIN_DIR=/data/brain \
LATTICEAI_PUBLIC_MODEL=openai:gpt-4o-mini \
LATTICEAI_INVITE_GATE_ENABLED=true \
LATTICEAI_INVITE_CODE="$(openssl rand -hex 24)" \
OPENAI_API_KEY=... \
python server.py
```

지원되는 퍼블릭 모델 접두사:

- `openai:gpt-4o-mini`
- `openrouter:openai/gpt-4o-mini`
- `openrouter:qwen/qwen3-vl-235b-a22b-instruct`
- `together:Qwen/Qwen3-VL-32B-Instruct`

## Docker

```bash
docker build -t lattice-ai .
docker run --rm -p 4825:4825 \
  -e OPENAI_API_KEY="$OPENAI_API_KEY" \
  -v "$PWD/.public-data:/data" \
  lattice-ai
```

이후 브라우저에서 다음 주소를 엽니다:

```text
http://localhost:4825/
```

퍼블릭/비루프백(non-loopback) 모드는 인증과 비공개 가입(closed registration)을 강제합니다. 선택적 초대 게이트는 `LATTICEAI_INVITE_GATE_ENABLED=true`로 활성화되며 더 이상 공유 기본 코드를 사용하지 않습니다. 초대 게이트를 활성화하고 `LATTICEAI_INVITE_CODE`를 생략하면 Lattice AI가 설정된 데이터 디렉터리에 설치별 비공개 초대 코드와 서명 시크릿을 생성합니다. 관리형 퍼블릭 배포 환경에서는 직접 무작위 초대 코드를 설정하고 마운트된 볼륨에 영속화된 시크릿 파일을 유지하십시오. 초대 쿠키는 서명되고 만료 시간이 적용되며, 단순 문자열 쿠키인 `authorized=true`는 허용되지 않습니다. 유효한 서명 클레임은 해당 가입 요청만 인가하며 서명 없는 직접 가입은 닫혀 있습니다.

## 선택적 Telegram 브리지

공개 서버에서는 꼭 필요한 경우가 아니면 Telegram을 비활성화 상태로 유지하십시오. 활성화하려면 다음 환경 변수가 모두 필요합니다.

```bash
LATTICEAI_ENABLE_TELEGRAM=true
LATTICEAI_TELEGRAM_BOT_TOKEN=...
LATTICEAI_TELEGRAM_ALLOWED_CHAT_IDS=123456789,987654321
LATTICEAI_SERVER_SESSION_TOKEN=...
```

채팅 ID 허용 목록은 채팅이 등록되기 전에 메시지와 콜백 쿼리에 적용됩니다. 값이 누락되거나 비어 있으면 모든 접근이 거부됩니다. 서버 세션 토큰은 전용 인증된 Lattice AI 세션 베어러 토큰이어야 하며, `sessions.json`에 저장된 해시값을 사용해서는 안 됩니다.

권한 알림은 운영자가 소유한 검토 페이지로 연결될 수 있습니다:

```bash
LATTICEAI_PERMISSION_UI_URL=https://your-server.example/admin/permissions
```

이 설정은 선택 사항입니다. 알림에는 짧은 토큰 힌트만 포함되며 URL이나 메시지에 승인 토큰 원문을 노출하지 않습니다.

## 퍼블릭 서버 체크리스트

- `LATTICEAI_MODE=public`으로 설정합니다.
- 클라우드 API 키(일반적으로 `OPENAI_API_KEY`)를 하나 설정합니다.
- 가입을 비공개로 유지합니다. 초대 기반 온보딩이 필요한 경우 `LATTICEAI_INVITE_GATE_ENABLED=true`로 설정하고 `LATTICEAI_INVITE_CODE`를 비공개 무작위 값으로 지정하거나 생성된 설치별 시크릿을 영구 스토리지에 보존합니다.
- `/data`에 영구 볼륨(persistent volume)을 마운트합니다.
- 봇을 의도적으로 운영하는 경우가 아니면 `LATTICEAI_ENABLE_TELEGRAM=false`로 유지하고, 활성화 시 `LATTICEAI_TELEGRAM_ALLOWED_CHAT_IDS`와 `LATTICEAI_SERVER_SESSION_TOKEN`을 모두 구성합니다.
- 호스팅 제공자 또는 리버스 프록시를 통해 서버 앞단에 HTTPS를 적용합니다.
