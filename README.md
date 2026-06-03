# Lattice AI

Lattice AI is an **AI Knowledge OS** for personal and organization workspaces.
It is not a simple chat app, local LLM launcher, or model manager.

Lattice AI turns files, documents, images, screenshots, conversations,
decisions, notes, work history, and generated artifacts into knowledge. That
knowledge is linked into a Knowledge Graph, and AI works on top of the graph to
advise, analyze, generate documents, and automate work.

The product rule for v2.2.0 is direct:

> Do not control the user in the name of protection. Explain clearly, disclose
> sources and risks, then let the user decide.

## Current release

**2.2.0 — Multimodal-First Knowledge OS Release.** Lattice AI now centers the
product on a multimodal Knowledge Graph, Gemma-4-first recommendations, source
disclosure, and equal feature access across basic and advanced modes. Admin mode
is the only mode with additional authority.

---

## What Lattice AI Does

- Accepts PDFs, Word files, spreadsheets, slide decks, images, screenshots,
  notes, code, web content, conversations, and work logs without asking the user
  to pre-convert them.
- Extracts entities, relationships, evidence, decisions, and artifacts into the
  Knowledge Graph.
- Uses AI models as replaceable workers on top of the graph.
- Recommends current-generation multimodal models based on CPU, GPU, RAM,
  storage, OS, and observed usage.
- Shows every model's source facts before use:
  1. 제작 국가
  2. 제작 회사
  3. 실행 방식
  4. 인터넷 사용 여부
  5. 모델명
- Keeps text-only local model recommendations out of the product path.

## Core Principles

- Users should not work for AI. AI should work for users.
- Do not hide features, sources, limitations, or risks.
- Basic and advanced modes expose the same capabilities.
- Basic mode explains in plain language.
- Advanced mode shows deeper technical reasoning.
- Admin mode is reserved for user management, permissions, audit logs,
  organization policy, security policy, model approval, and Private VPC.
- Knowledge Graph durability matters more than any single model.
- Old model generations are removed when a newer generation in the same family
  is available.

## Multimodal Model Policy

Lattice AI v2.2.0 removes the old text-only recommendation path.

Current local recommendations focus on:

| Family | Default role | Example current recommendation |
| --- | --- | --- |
| Gemma 4 | Default Google multimodal family | `mlx-community/gemma-4-12b-it-4bit` |
| Gemma 4 large | Higher-quality local multimodal work | `mlx-community/gemma-4-31b-it-4bit` |
| Qwen3-VL | Smaller and balanced multimodal options | `mlx-community/Qwen3-VL-4B-Instruct-4bit` |
| Llama 4 | Meta multimodal option | `mlx-community/Llama-4-Scout-17B-16E-Instruct-4bit` |

Removed from current recommendation catalogs:

- `mlx-lm` as a local text-only execution path
- Gemma 2 and Gemma 3 recommendations
- Qwen2.5-VL recommendations
- SmolLM, Phi, Mistral, DeepSeek, GPT-OSS, and Llama 3.x local recommendation
  entries
- text-only fallback logic for low-spec machines

Low-spec machines are handled with smaller or quantized multimodal models,
not older text-only models.

## Knowledge Graph Flow

```text
files / documents / images / conversations / work history
  -> multimodal understanding
  -> entity extraction
  -> relationship extraction
  -> evidence storage
  -> Knowledge Graph update
  -> advice / analysis / document generation / automation
```

The graph preserves the user's work memory even when the model changes.

## Quick Start

```bash
npm install
npm run dev
```

Then open:

```text
http://127.0.0.1:4825
```

Useful commands:

```bash
npm test
npm run check:python
npm run build
```

VS Code extension:

```bash
cd vscode-extension
npm install
npm run build
npm run package:vsix
```

## Python Package

```bash
python3 -m build
```

Expected v2.2.0 Python artifacts:

```text
dist/ltcai-2.2.0-py3-none-any.whl
dist/ltcai-2.2.0.tar.gz
```

## VS Code Package

```bash
cd vscode-extension
npm run build
npm run package:vsix
```

Expected v2.2.0 VSIX artifact:

```text
dist/ltcai-2.2.0.vsix
```

## npm Package

```bash
npm pack
```

Expected v2.2.0 npm artifact:

```text
ltcai-2.2.0.tgz
```

## Manual Publishing

Publishing is intentionally manual. Do not publish with globs.

PyPI:

```bash
python3 -m twine upload dist/ltcai-2.2.0-py3-none-any.whl dist/ltcai-2.2.0.tar.gz
```

VS Code Marketplace:

```bash
npx vsce publish --packagePath dist/ltcai-2.2.0.vsix
```

Open VSX:

```bash
npx ovsx publish dist/ltcai-2.2.0.vsix
```

npm:

```bash
npm publish --access public
```

## Documentation

- [ARCHITECTURE.md](ARCHITECTURE.md)
- [PROJECT_PRINCIPLES.md](PROJECT_PRINCIPLES.md)
- [AI_PHILOSOPHY.md](AI_PHILOSOPHY.md)
- [MODEL_POLICY.md](MODEL_POLICY.md)
- [KNOWLEDGE_GRAPH.md](KNOWLEDGE_GRAPH.md)
- [RELEASE_NOTES.md](RELEASE_NOTES.md)
- [docs/CHANGELOG.md](docs/CHANGELOG.md)

## Release History

| Version | Theme |
| --- | --- |
| **2.2.0** | Multimodal-first Knowledge OS, source disclosure, Gemma-4 recommendation policy |
| 2.1.0 | Agent Platform Maturity |
| 2.0.0 | Agentic Workspace Platform |
| 1.7.0 | Graph and collaboration |
| 1.6.0 | Product experience deepening |
| 1.5.0 | Unified product release |
