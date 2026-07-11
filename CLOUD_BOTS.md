# Cloud Telegram Bots

This project uses two separate Telegram bots.

## 1. Local Lattice AI Bot

Purpose: talk to the local Lattice AI server and mirror web conversations.

Required env:

```bash
LATTICEAI_TELEGRAM_BOT_TOKEN=...
LATTICEAI_TELEGRAM_ALLOWED_CHAT_IDS=123456789
LATTICEAI_SERVER_SESSION_TOKEN=...
```

`LATTICEAI_TELEGRAM_ALLOWED_CHAT_IDS` is a comma-separated allowlist applied to
messages and callback queries before chat registration. The bot does not start
without a non-empty allowlist and dedicated server session token. Do not copy a
hash from `sessions.json`; use an authenticated session bearer created for the
bridge.

Run with the normal local server:

```bash
CTA
```

## 2. Cloud Codex Bot

Purpose: talk with a Codex-style development assistant from Telegram and optionally create GitHub issues.

Required env:

```bash
CODEX_TELEGRAM_BOT_TOKEN=...
OPENAI_API_KEY=...
CODEX_OPENAI_MODEL=gpt-5.4
```

Optional GitHub issue bridge:

```bash
GITHUB_TOKEN=...
GITHUB_REPO=owner/repo
```

Run:

```bash
python codex_telegram_bot.py
```

Telegram commands:

```text
/start
/reset
/issue Title
Issue body here
```

## Token Rotation

Before pushing this repository to GitHub:

1. Revoke the old Telegram token in BotFather.
2. Create one token for the existing local Lattice AI bot.
3. Create a separate token for the new Codex bot.
4. Put real secrets in `.env`, never in source code.
5. Keep the GitHub repository private.
