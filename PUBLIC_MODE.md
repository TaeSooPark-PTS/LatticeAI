# Lattice AI Local / Public Mode (v9.3.0)

Lattice AI now has two runtime modes.

## Local Mode

Use this on your Mac when you want MLX-VLM, local files, image analysis, and
desktop-adjacent tools. Telegram is opt-in and fails closed unless both of its
security settings are present.

```bash
LATTICEAI_MODE=local \
LATTICEAI_DATA_DIR="$PWD/.ltcai" \
LATTICEAI_BRAIN_DIR="$PWD/.ltcai-brain" \
python server.py
```

Defaults:

- Telegram bridge: off
- Local MLX-VLM models: on
- Default model: `mlx-community/gemma-4-12b-it-4bit`
- Port: `4825`

## Public Mode

Use this on a public server such as Render, Fly.io, Railway, a VPS, or any Docker host.

Public mode does not try to load Apple Silicon MLX models. It expects an OpenAI-compatible cloud model.

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

Supported public model prefixes:

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

Then open:

```text
http://localhost:4825/
```

Public/non-loopback mode forces authentication and closed registration. The
optional invitation gate is enabled with `LATTICEAI_INVITE_GATE_ENABLED=true`
and no longer has a shared default code. If it is enabled and
`LATTICEAI_INVITE_CODE` is omitted, Lattice AI creates a private per-install
code and signing secret in the configured data directory. For managed public
deployments, set your own random code and keep the persisted secret file on the
mounted volume. Invitation cookies are signed and expiring; a literal
`authorized=true` cookie is not accepted. A valid signed claim authorizes only
that registration request; direct unsigned registration stays closed.

## Optional Telegram Bridge

Keep Telegram disabled on public servers unless it is required. Enabling it
requires all of the following:

```bash
LATTICEAI_ENABLE_TELEGRAM=true
LATTICEAI_TELEGRAM_BOT_TOKEN=...
LATTICEAI_TELEGRAM_ALLOWED_CHAT_IDS=123456789,987654321
LATTICEAI_SERVER_SESSION_TOKEN=...
```

The chat-ID list applies to messages and callback queries before any chat is
registered. Missing or empty values deny all access. The server session token
is a dedicated authenticated Lattice AI session bearer; it must not be the
hashed value stored in `sessions.json`.

Permission notifications may link to an operator-owned review page:

```bash
LATTICEAI_PERMISSION_UI_URL=https://your-server.example/admin/permissions
```

This setting is optional. Notifications include only a short token hint and do
not put an approval token in the URL or message.

## Public Server Checklist

- Set `LATTICEAI_MODE=public`.
- Set one cloud API key, usually `OPENAI_API_KEY`.
- Keep registration closed; if invitation onboarding is required, set
  `LATTICEAI_INVITE_GATE_ENABLED=true` and set `LATTICEAI_INVITE_CODE` to a
  private random value, or retain the generated
  per-install secret on persistent storage.
- Mount a persistent volume to `/data`.
- Keep `LATTICEAI_ENABLE_TELEGRAM=false` unless you intentionally run the bot;
  if enabled, configure both `LATTICEAI_TELEGRAM_ALLOWED_CHAT_IDS` and
  `LATTICEAI_SERVER_SESSION_TOKEN`.
- Put the server behind HTTPS with your hosting provider or a reverse proxy.
