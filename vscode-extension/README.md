# Lattice AI Extension

Local/cloud LLM assistant for VS Code, Cursor, and Antigravity — powered by [Lattice AI](https://github.com/TaeSooPark-PTS/LatticeAI).

Connects to a running Lattice AI server (`http://localhost:4825`) and provides chat, code editing, explanation, terminal command generation, and knowledge garden features directly in your editor.

## Features

- **Chat panel** — talk to local MLX or cloud models (OpenAI, Groq, Together, etc.)
- **Edit selection** — rewrite selected code with AI
- **Explain selection** — get an explanation of selected code
- **Generate terminal command** — describe a task, get a shell command
- **Knowledge Garden** — save snippets/notes to `~/.ltcai-brain/`
- **Language** — Korean / English UI

## Commands

| Command | Shortcut |
|---------|----------|
| Lattice AI: Open Chat | `Cmd+Shift+A` |
| Lattice AI: Edit Selection | `Cmd+Shift+E` |
| Lattice AI: Load Model | `Cmd+Shift+M` |
| Lattice AI: Explain Selection | right-click menu |
| Lattice AI: Save to Knowledge Garden | right-click menu |

## Requirements

Lattice AI server must be running:

```bash
pip install ltcai && LTCAI
# → http://localhost:4825
```

## v0.1.4 Changes

### Added
- Session persistence — stay logged in after server restart
- SSO login — Entra ID / Okta OIDC support
- Chat history search — keyword search in sidebar
- Delete conversation button in sidebar
- MCP server management UI modal
- Inline diff view for Edit Selection — review before applying
- Attach Current File to Chat command

---

## v0.1.3 Changes

### Added
- 프로필 수정 UI — 이름·닉네임 변경
- 회원가입 폼 — 비밀번호 확인 필드, 인라인 에러 메시지
- 어드민 패널 초대 링크 섹션 — 원클릭 복사
- 어드민 대시보드 메시지 활동 차트 (Chart.js, 최근 14일)
- 웹 UI 한국어 / 영어 전환

### Fixed
- 로그아웃 시 서버 세션 쿠키 정상 만료
- 인증(`account.html`)과 채팅(`chat.html`) UI 분리
- 채팅 헤더 언어 선택 드롭다운 겹침 문제 수정

---

## Build

```bash
cd vscode-extension
npm install
npm run build
npm run package:vsix
```

## Install to all three editors

```bash
cd vscode-extension
npm run install:all
```

The script installs the latest `.vsix` into `code`, `cursor`, and `antigravity` if each CLI is available.

## Publish

```bash
npm run publish:vscode
npm run publish:openvsx
```

Before publishing, log in with `vsce login <publisher>` and configure an Open VSX token for `ovsx`.
