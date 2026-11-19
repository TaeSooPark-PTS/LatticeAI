[![English](https://img.shields.io/badge/English-Active-2563eb?style=flat-square)](./RELEASE_NOTES_v12.2.0.md) [![한국어](https://img.shields.io/badge/한국어-Korean-475569?style=flat-square)](../ko/releases/RELEASE_NOTES_v12.2.0.md)

# Lattice AI v12.2.0 — Small Voice (2026-08-29)

> **Status: historical** — point-in-time release note.

A release that closes the gap where 2B-class local models failed to select MCP tools, skills, and native actions. The write engine remains a single `GraphWriter`, and the Home screen now suggests those workflows in plain language.

| | |
| --- | --- |
| Theme | **Small Voice** — small models can speak tools too |
| Door | Native 422 operations / 41 families (unchanged) |
| Worker | 20 routes (unchanged) |

## Added

- The action parser now understands function-call envelopes: `{"name", "arguments"}`, nested `function`, Qwen `<function=read_file><parameter=path>...`, and `Action: mcp.grep`. Repairs are recorded in the trace as `alias_keys` / `args_string` / `xml_call` / `labeled`.
- The compact action list is capped at the same 9 rows as the guided menu. Actions explicitly named in the request stay at the front, and `final` stays last.
- When a skill's `when` phrase matches the request, the agent reads `SKILL.md` first even if the user did not cite the catalog name.
- Quoted search terms backfill missing `pattern`/`query` arguments.
- Home composer chips: find in folder, read and summarize file, search memory, review with skill — plus a matching explanation card in Settings. The MCP/skill timeline prefixes dotted tool names with plain-language labels.

## Changed

- The compact prompt states in one line that `name`/`arguments` envelopes are accepted.
- 12.1.0 Fast Path overlapping ingest, single-shot embed, and auto-HNSW remain intact.

## Fixed

- Labeled salvage previously rejected dotted `mcp.` / `skill.` action names as chatter.

## Known Issues

- Small-model *content* quality remains fail-closed: structure is salvaged, while thin summaries are still stopped by the critic.
- `api_key` cloud paths are mock-verified only.
- The macOS DMG is ad-hoc signed.
- The default vector search env remains `brute`.
- `watch` does not auto-delete files.
- Real-embedding default rollout is not closed yet.

## Follow-up

- Flipping `hnsw` to the env default requires re-measuring `recall@10` on a continuously ingested Brain.
- Sidecar queries reading the full vector table and `SkipByHash` not updating stamps are queued for the next cut.
