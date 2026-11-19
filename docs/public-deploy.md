[![English](https://img.shields.io/badge/English-Active-2563eb?style=flat-square)](./public-deploy.md) [![한국어](https://img.shields.io/badge/한국어-Korean-475569?style=flat-square)](./ko/public-deploy.md)

# Public Deployment Guide

Guide for deploying Lattice AI on external servers such as Render, Fly.io,
Railway, or a VPS.

## Environment Variables

```bash
# Required
LATTICEAI_MODE=public
LATTICEAI_INVITE_GATE_ENABLED=true            # Enable only when using invite onboarding
LATTICEAI_INVITE_CODE=my-random-invite-code   # Recommended: operator-generated random invite code

# Cloud models (at least one)
OPENAI_API_KEY=sk-...
# GROQ_API_KEY=gsk_...
# OPENROUTER_API_KEY=sk-or-...

LATTICEAI_PUBLIC_MODEL=openai:gpt-4o-mini     # Default public model

# Security
LATTICEAI_ALLOW_LOCAL_MODELS=false            # Disable MLX (unnecessary on servers)
LATTICEAI_ENABLE_TELEGRAM=false               # Disable Telegram bot

# Optional
LATTICEAI_ENABLE_GRAPH=false                  # Disable Data Graph
LATTICEAI_DATA_DIR=/data                      # Persistent data directory
LATTICEAI_ADMIN_EMAILS=you@example.com        # Pin admin email(s)
LATTICEAI_PERMISSION_UI_URL=https://example.com/admin/permissions  # Optional
```

Public / non-loopback mode enforces authentication and closes open signup.
Enable `LATTICEAI_INVITE_GATE_ENABLED=true` only when invite-based onboarding
is needed. There is no shared default invite code: if `LATTICEAI_INVITE_CODE`
is omitted while the gate is enabled, a per-installation code and cookie signing
key are generated via CSPRNG and stored in a restricted-permission file in the
data directory. In production, either set a random value explicitly or ensure
that file lives on a persistent volume. Static cookies such as `authorized=true`
are never trusted. A signed invite claim authorizes only that registration
request; calling `/register` directly without a claim cannot bypass closed
public signup.

Configure all four values below only when intentionally enabling Telegram:

```bash
LATTICEAI_ENABLE_TELEGRAM=true
LATTICEAI_TELEGRAM_BOT_TOKEN=...
LATTICEAI_TELEGRAM_ALLOWED_CHAT_IDS=123456789,987654321
LATTICEAI_SERVER_SESSION_TOKEN=...
```

The allowlist is enforced before registration for both messages and callback
queries. If it is empty, all chats are rejected. `LATTICEAI_SERVER_SESSION_TOKEN`
is the bearer token of a dedicated bot session—never copy a hashed value from
`sessions.json`. `LATTICEAI_PERMISSION_UI_URL` is only an optional link to the
human review page; notifications never contain full approval tokens.

## Docker

```dockerfile
# A Dockerfile is already included in the repository
docker build -t lattice-ai .
```

```bash
docker run --rm \
  -p 4825:4825 \
  -e LATTICEAI_MODE=public \
  -e OPENAI_API_KEY="$OPENAI_API_KEY" \
  -e LATTICEAI_INVITE_GATE_ENABLED=true \
  -e LATTICEAI_INVITE_CODE="my-secret-code" \
  -v "$PWD/.data:/data" \
  lattice-ai
```

## Deploying on Render

1. New Web Service → connect the GitHub repository
2. Environment: `Python 3`
3. Build Command: `pip install ltcai`
4. Start Command: `LTCAI`
5. Set the environment variables above in the Environment Variables tab
6. Add a persistent Disk mounted at `/data`

## Deploying on Fly.io

```bash
fly launch
fly secrets set LATTICEAI_MODE=public OPENAI_API_KEY=sk-... LATTICEAI_INVITE_GATE_ENABLED=true LATTICEAI_INVITE_CODE=secret
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

## nginx Reverse Proxy

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

        # SSE streaming support
        proxy_buffering off;
        proxy_cache off;
        proxy_read_timeout 300s;
        chunked_transfer_encoding on;
    }
}
```

## Caddy Reverse Proxy

```caddyfile
yourdomain.com {
    reverse_proxy localhost:4825
}
```

## Public Deployment Checklist

- [ ] Set `LATTICEAI_MODE=public`
- [ ] If using invite onboarding, set `LATTICEAI_INVITE_GATE_ENABLED=true` and
      a private random `LATTICEAI_INVITE_CODE` (or persist the generated
      per-install secret)
- [ ] Configure an HTTPS reverse proxy (nginx / Caddy)
- [ ] Mount a persistent volume (`/data` or `LATTICEAI_DATA_DIR`)
- [ ] Block direct public firewall access to port 4825
- [ ] Set `LATTICEAI_ALLOW_LOCAL_MODELS=false`
- [ ] Configure at least one cloud API key
- [ ] Verify the admin account after initial signup (`http://yourdomain.com/admin`)
- [ ] If enabling Telegram, set both the chat ID allowlist and a dedicated
      server session token

## Supported Cloud Model Prefixes

```
openai:gpt-4o-mini
openai:gpt-4o
openrouter:openai/gpt-4o-mini
openrouter:qwen/qwen3-vl-235b-a22b-instruct
together:Qwen/Qwen3-VL-32B-Instruct
```
