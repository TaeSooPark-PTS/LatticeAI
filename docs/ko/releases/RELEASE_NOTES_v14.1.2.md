[![English](https://img.shields.io/badge/English-Switch-475569?style=flat-square)](../../releases/RELEASE_NOTES_v14.1.2.md) [![한국어](https://img.shields.io/badge/한국어-현재_언어-2563eb?style=flat-square)](./RELEASE_NOTES_v14.1.2.md)

# Lattice AI v14.1.2 — Living Synthesis

릴리스 날짜: **2026-11-25**

`v14.1.2`는 모든 추적 소스 언어 전반에 **무주석(zero-comments)** 표준을 적용하고, 모든 린터/타입 체커 억제 지시문을 근본 코드 수정으로 대체하며, 이를 로컬 린트와 GitHub Actions CI에 영구 게이트로 잠급니다.

## 주요 변경 사항

- **전 소스 언어 주석 및 억제 지시문 전량 제거:**
  - Python, TypeScript/JavaScript, Rust, Shell, CSS, HTML 소스 파일 전반에서 인라인 주석, 블록 주석, 독스트링, JSDoc 및 억제 지시문(`# noqa`, `# type: ignore`, `// @ts-expect-error`, `// eslint-disable`, `/* @vite-ignore */` 등)을 전량 제거하고 OS/컴파일러 필수 지시문(`#!/usr/bin/env ...` 셔뱅 및 `/// <reference ... />`)만 유지했습니다.
  - 억제 지시문을 제거하고 근본 원인을 직접 수정했습니다: `images.py`·`loading.py`·`foundation.py` 명시적 타입 어노테이션, `_kg_fsutil.py` 명시적 임포트, `latticeai/tools/_shared.py` 분리(`E402` 해소), `worker_compute_render.py`·`worker_seams.py`·`audio.py`·`ports.py`·`hnsw.py`·`sidecar.py` 구체적 예외 튜플 및 `TTFError` 처리, `sidecar.py` 파라미터화 `json_each(?)` 쿼리, `worker_seams.py` `os.sysconf` 메모리 조회, `lattice-retrieval`·`lattice-platform` Clippy 정리.
- **영구 무주석 게이트 도입 (`scripts/check_no_comments.mjs`, `package.json`, `.github/workflows/ci.yml`):**
  - `@babel/parser`(TS/JS), Python `ast` + `tokenize`, Rust/Shell/CSS/HTML 렉서 기반의 `npm run check:no-comments` 게이트를 추가했습니다.
  - `npm run lint`와 CI `frontend` 잡에 `npm run check:no-comments`를 연동하고, `AGENTS.md`, `CONTRIBUTING.md` 및 한국어 미러 문서에 영구 무주석 정책을 명시했습니다.

## 정확한 배포 아티팩트

- Python wheel: `dist/ltcai-14.1.2-py3-none-any.whl`
- Python sdist: `dist/ltcai-14.1.2.tar.gz`
- npm tarball: `ltcai-14.1.2.tgz`
- VS Code / Cursor / Antigravity 확장: `dist/ltcai-14.1.2.vsix`
- macOS Apple Silicon DMG: `src-tauri/target/release/bundle/dmg/Lattice AI_14.1.2_aarch64.dmg`
