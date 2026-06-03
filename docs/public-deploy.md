# 퍼블릭 배포 가이드

Render, Fly.io, Railway, VPS 등 외부 서버에 Lattice AI를 배포할 때 사용하는 가이드입니다.

## 환경변수

```bash
# 필수
LATTICEAI_MODE=public
LATTICEAI_INVITE_CODE=my-secret-invite-code   # 회원가입 시 필요한 초대 코드

# 클라우드 모델 (최소 하나 이상)
OPENAI_API_KEY=sk-...
# GROQ_API_KEY=gsk_...
# OPENROUTER_API_KEY=sk-or-...

LATTICEAI_PUBLIC_MODEL=openai:gpt-4o-mini     # 기본 공개 모델

# 보안
LATTICEAI_ALLOW_LOCAL_MODELS=false            # MLX 비활성화 (서버에 불필요)
LATTICEAI_ENABLE_TELEGRAM=false               # Telegram 봇 비활성화

# 선택적
LATTICEAI_ENABLE_GRAPH=false                  # Data Graph 비활성화
LATTICEAI_DATA_DIR=/data                      # 데이터 디렉토리
LATTICEAI_ADMIN_EMAILS=you@example.com        # 어드민 이메일 고정
```

## Docker

```dockerfile
# Dockerfile이 이미 포함되어 있습니다
docker build -t lattice-ai .
```

```bash
docker run --rm \
  -p 4825:4825 \
  -e LATTICEAI_MODE=public \
  -e OPENAI_API_KEY="$OPENAI_API_KEY" \
  -e LATTICEAI_INVITE_CODE="my-secret-code" \
  -v "$PWD/.data:/data" \
  lattice-ai
```

## Render 배포

1. New Web Service → GitHub 레포 연결
2. Environment: `Python 3`
3. Build Command: `pip install ltcai`
4. Start Command: `LTCAI`
5. Environment Variables 탭에서 위 환경변수 입력
6. Disk 추가: `/data` (영구 저장용)

## Fly.io 배포

```bash
fly launch
fly secrets set LATTICEAI_MODE=public OPENAI_API_KEY=sk-... LATTICEAI_INVITE_CODE=secret
fly volumes create ltcai_data --size 1
fly deploy
```

`fly.toml`:
```toml
[build]
  dockerfile = "Dockerfile"

[[mounts]]
  source = "ltcai_data"
  destination = "/data"

[env]
  LATTICEAI_DATA_DIR = "/data"
```

## nginx 리버스 프록시

```nginx
server {
    listen 80;
    server_name yourdomain.com;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl http2;
    server_name yourdomain.com;

    ssl_certificate /etc/letsencrypt/live/yourdomain.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/yourdomain.com/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:4825;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # SSE 스트리밍 지원
        proxy_buffering off;
        proxy_cache off;
        proxy_read_timeout 300s;
        chunked_transfer_encoding on;
    }
}
```

## Caddy 리버스 프록시

```caddyfile
yourdomain.com {
    reverse_proxy localhost:4825
}
```

## 퍼블릭 배포 체크리스트

- [ ] `LATTICEAI_MODE=public` 설정
- [ ] `LATTICEAI_INVITE_CODE` 비공개 랜덤 값으로 설정
- [ ] HTTPS 리버스 프록시 구성 (nginx / Caddy)
- [ ] 영구 볼륨 마운트 (`/data` 또는 `LATTICEAI_DATA_DIR`)
- [ ] 방화벽에서 4825 포트 직접 노출 차단
- [ ] `LATTICEAI_ALLOW_LOCAL_MODELS=false`
- [ ] 최소 하나의 클라우드 API 키 설정
- [ ] 첫 가입 후 어드민 계정 확인 (`http://yourdomain.com/admin`)

## 지원 클라우드 모델 프리픽스

```
openai:gpt-4o-mini
openai:gpt-4o
openrouter:openai/gpt-4o-mini
openrouter:qwen/qwen3-vl-235b-a22b-instruct
together:Qwen/Qwen3-VL-32B-Instruct
```
