# Lattice AI Local / Public Mode

Lattice AI now has two runtime modes.

## Local Mode

Use this on your Mac when you want MLX, local files, Telegram mirroring, image analysis, and desktop-adjacent tools.

```bash
LATTICEAI_MODE=local \
LATTICEAI_DATA_DIR="$PWD/.ltcai" \
LATTICEAI_BRAIN_DIR="$PWD/.ltcai-brain" \
python server.py
```

Defaults:

- Telegram bridge: on
- Local MLX models: on
- Default model: `mlx-community/gemma-4-26b-a4b-it-4bit`
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
OPENAI_API_KEY=... \
python server.py
```

Supported public model prefixes:

- `openai:gpt-4o-mini`
- `openrouter:openai/gpt-4o-mini`
- `groq:llama-3.1-8b-instant`
- `together:meta-llama/Llama-3.3-70B-Instruct-Turbo`

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
http://localhost:4825/?code=gemma-lattice-ai
```

## Public Server Checklist

- Set `LATTICEAI_MODE=public`.
- Set one cloud API key, usually `OPENAI_API_KEY`.
- Set `LATTICEAI_INVITE_CODE` to a private value.
- Mount a persistent volume to `/data`.
- Keep `LATTICEAI_ENABLE_TELEGRAM=false` unless you intentionally want that public server to run the bot.
- Put the server behind HTTPS with your hosting provider or a reverse proxy.
