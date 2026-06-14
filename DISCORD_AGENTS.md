# Discord Agent Collaboration (pts_* bots)

This project uses multiple specialized Discord bots so different agent instances (Claude-based and Grok-based) can collaborate in the same channel by mentioning each other.

## Current Bots

| Bot Name       | ID (snowflake)       | Runtime     | Notes |
|----------------|----------------------|-------------|-------|
| pts_openclaw   | 1506652866759360683 | Claude Code | Primary dev / architecture persona |
| pts_claudecode | 1507741122577961041 | Claude Code | Code-focused / review persona |
| **pts_grok**   | 1515612666612416552 | **Grok**    | This Grok TUI instance — for implementation, research, or parallel work |

## Channel

All collaboration happens in guild channel: `1506662093309608026`

## How Mentions Work

- `requireMention: true` on the channel policy.
- A human or another allowed bot `@mentions` the target bot (or replies to one of its recent messages).
- The receiving bot's MCP only delivers messages that mention *it*.
- Bot-authored messages are ignored by default for spam prevention. We maintain a narrow allowlist (`botAllowFrom` in access.json) + a code patch in the discord MCP server so that the trusted pts_* bots can wake each other up when they @mention.

## Setup for a New Bot (pts_grok example)

1. In Discord Developer Portal create a **new Application**, create a Bot under it, name it `pts_grok`.
2. Enable **Message Content Intent**.
3. Generate a Bot token.
4. Invite the bot to the server (OAuth2 → bot scope + the usual read/send/reaction permissions).
5. Configure local state (this repo provides helpers):
   ```bash
   # Edit the token
   $EDITOR ~/.grok/channels/discord-pts-grok/.env
   ```
6. Launch a dedicated Grok session bound to it:
   ```bash
   cd ~/Downloads/Lattice\ AI
   ./scripts/launch-pts-grok.sh
   ```
   In the new Grok TUI, ensure the discord MCP is enabled (`/mcps`).

7. After you have the new bot's User ID, add it to the other agents' `botAllowFrom` so they can mention pts_grok back:
   - Edit `~/.claude/channels/discord/access.json` (for pts_claudecode) and the corresponding one for pts_openclaw if it uses a separate state dir.
   - Add the pts_grok ID under `botAllowFrom`.
   - Also add pts_grok's ID to this file's `botAllowFrom` if you want the reverse (already prepared in the pts_grok access.json template).

8. (One-time) The discord server.ts was patched (in the Grok marketplace cache) with `shouldAcceptBotAuthoredMessage` so that messages from the other bots that @mention the local bot are accepted.

## Access Policy Files

Each bot has its own state directory (via `DISCORD_STATE_DIR`):

- pts_claudecode / default: `~/.claude/channels/discord/`
- pts_grok: `~/.grok/channels/discord-pts-grok/`

Key fields for collaboration:
- `groups["1506662093309608026"].requireMention`
- `botAllowFrom` — list of other pts_* bot user IDs that are allowed to trigger this instance via mention.

## Patching Note

If bot-to-bot mentions stop working after a plugin update, re-apply the `shouldAcceptBotAuthoredMessage` logic (see the version in `~/.grok/marketplace-cache/.../discord/server.ts` around the `messageCreate` handler) or copy from the working Claude cache under `~/.claude/plugins/cache/...`.

## Tips

- Use real mention syntax in prompts when one agent wants to delegate to another: `<@1506652866759360683>` (or the display name if it resolves).
- The launch script forces the correct state dir and changes to the Lattice AI dir.
- Keep `dmPolicy: "allowlist"` for the guild-collaboration bots once all participants are known.
- Ack reaction (🛠️) is set on pts_grok for visibility when it receives work.

## Updating the Roster

When adding or removing a bot:
1. Create/invite the Discord bot.
2. Create its state dir + access.json + .env (copy the pts_grok template as starting point).
3. Update every other bot's `botAllowFrom`.
4. Update this table and any launch scripts.
5. Test by having one bot say something that @mentions the new one.
