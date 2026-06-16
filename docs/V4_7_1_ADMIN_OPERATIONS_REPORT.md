# v4.7.1 Admin Operations Report

## Scope

v4.7.1 turns Lattice AI into a cleaner product split: everyday users live in the
Living Brain, while operators use a dedicated Admin Console for observability and
maintenance. This release does not redesign Brain Core or the storage layer.

## Completed Areas

- Separated the admin route into `#/admin` while keeping `/app` focused on Brain
  plus conversation.
- Added admin overview cards for users, recent logs, security, and Brain index
  status.
- Added admin panels for user directory, role permissions, filtered audit logs,
  security events, policies, log retention, and index rebuild operations.
- Added server-side audit query filters for search text, actor, action,
  severity, and result limit.
- Added `/admin/log-retention` for local-first retention posture, retained event
  counts, prune candidates, and export-before-prune status.
- Added workspace-aware filtering for admin summary, stats, sensitivity, and
  audit reads when the active workspace header/query is present.
- Added frontend API helpers for `/admin/stats`, `/admin/security/events`,
  `/admin/roles`, and `/admin/log-retention`.
- Moved Admin Console data loading into a dedicated hook so Brain user state and
  admin observability state remain separate in the frontend runtime.
- Updated visual validation to confirm the admin console is separate from the
  user Brain surface.
- Synchronized then-current release version metadata to `4.7.1`.
- Updated README, RELEASE.md, release notes, changelog, architecture, security,
  feature status, VS Code extension docs, and release evidence references.

## Evidence

Fresh v4.7.1 screenshots and walkthrough media are indexed in
[output/release/v4.7.1/SCREENSHOT_INDEX.md](../output/release/v4.7.1/SCREENSHOT_INDEX.md).

## Expected Artifacts

- `dist/ltcai-4.7.1-py3-none-any.whl`
- `dist/ltcai-4.7.1.tar.gz`
- `dist/ltcai-4.7.1.vsix`
- `ltcai-4.7.1.tgz`
- `src-tauri/target/release/bundle/dmg/Lattice AI_4.7.1_aarch64.dmg`

## Validation Checklist

The final release report records command results, artifact SHA256 hashes, commit,
tag, push, GitHub Release URL, and package registry publish status.
