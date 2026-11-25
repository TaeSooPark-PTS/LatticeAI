[![English](https://img.shields.io/badge/English-Active-2563eb?style=flat-square)](./CONTRIBUTING.md) [![한국어](https://img.shields.io/badge/한국어-Korean-475569?style=flat-square)](./docs/ko/CONTRIBUTING.md)

# Contributing to Lattice AI

## Development Environment Setup

```bash
git clone https://github.com/TaeSooPark-PTS/LatticeAI.git
cd LatticeAI

# Python environment (Python 3.11+ required)
python3 -m venv venv
source venv/bin/activate
pip install -e ".[all]"
pip install pytest pytest-asyncio

# Start server (development mode — auto-reloads on code changes)
python ltcai_cli.py --reload
```

### VS Code Extension Development

```bash
cd vscode-extension
npm install
npm run build        # Compile TypeScript
npm run package:vsix # Build .vsix package
```

## Running Tests

```bash
# Run all tests
python -m pytest tests/ -v

# Unit tests only
python -m pytest tests/unit/ -v

# Specific file
python -m pytest tests/unit/test_tools.py -v
```

Current core unit test highlights:
- `tests/unit/test_security.py` — authentication, MIME validation, rate limiting (16 tests)
- `tests/unit/test_tools.py` — `edit_file`, `grep`, `todo`, `read_file`, sandbox (23 tests)

Please include tests covering your changes in every pull request.

## Pull Request Guidelines

1. Create a new branch from `main`: `git checkout -b feat/my-feature`
2. Prefix commit messages with `feat:`, `fix:`, `docs:`, `chore:`, etc.
3. Briefly explain what changed and why in the PR title and description
4. Add tests whenever possible
5. Verify that `LTCAI doctor` passes

## Code Style

- Use Python 3.11+ syntax
- Include `from __future__ import annotations` at the top of Python modules
- Type annotations are recommended
- Never write comments of any kind in source code (including docstrings, JSDoc, and linter/type-checker suppressions). Always fix the underlying code instead.
- Do not commit local runtime state (`.ltcai/`, `.ltcai-brain/`, logs, session files)

## Architecture Overview

See [docs/architecture.md](docs/architecture.md).

## Reporting Security Vulnerabilities

Please report vulnerabilities privately via [SECURITY.md](SECURITY.md) instead of opening a public issue.
