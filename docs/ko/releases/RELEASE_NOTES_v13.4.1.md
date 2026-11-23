[![English](https://img.shields.io/badge/English-Switch-475569?style=flat-square)](../../releases/RELEASE_NOTES_v13.4.1.md) [![한국어](https://img.shields.io/badge/한국어-현재_언어-2563eb?style=flat-square)](./RELEASE_NOTES_v13.4.1.md)

# Lattice AI v13.4.1 — Living Synthesis (2026-11-23)

전 스택 Dependabot PR `#58`, `#60`, `#61`의 메이저/브레이킹 의존성 업그레이드 호환성 및 컴파일 오류를 해결한 유지보수 릴리스입니다.

## 주요 변경 사항

- **프론트엔드 (`package.json`, `package-lock.json`, `tsconfig.json`, `frontend/src/vite-env.d.ts`):**
  - `package.json`에 `typescript` `overrides`를 추가해 `npm ci` 환경에서 `openapi-typescript@7.13.0`과 `typescript@7.0.2` 피어 의존성 충돌을 해결했습니다.
  - TypeScript 7 대응을 위해 `tsconfig.json`에서 제거된 `baseUrl`을 정리하고 `paths`를 `./frontend/src/*`로 전환했으며, CSS 사이드이펙트 임포트 타입을 위해 `frontend/src/vite-env.d.ts`(`/// <reference types="vite/client" />`)를 추가했습니다.
  - `vitest@5.1.1` / `@vitest/coverage-v8@5.1.1` / `jsdom@31` / `@testing-library/jest-dom@8` 업그레이드에 맞춰 111개 전체 프론트엔드 테스트 파일의 100% 커버리지를 유지했습니다.
- **Rust 워크스페이스 (`rust/` 및 `src-tauri/`):**
  - `sha2` 0.11 / `digest` 0.11 변경에 맞춰 `hex_lower` / `sha256_text` / `sha256_bytes`로 전환했습니다.
  - `rusqlite` 0.43의 `usize` `ToSql` 제거에 맞춰 `i64` 바인딩으로 전환했습니다.
  - `fancy-regex` 0.20의 `Captures<'_, str>` 제네릭 인자를 반영했습니다(`lattice-core/src/redact.rs`).
  - `lattice-auth`에서 `axum` 0.8 네이티브 비동기 `FromRequestParts`(`#[async_trait]` 제거) 및 `scrypt` 0.12 3인자 `Params::new`를 적용했습니다.
  - `lattice-platform`의 `zip` 9(`ZipFile<'_, R>`, `Result<Cow<'_, str>, ZipError>`, `SimpleFileOptions`), `lattice-ingest`의 `reqwest` 0.13, `src-tauri`의 `tauri` 2.11.6 / `plist` 1.8.0 / `time` 0.3.55 업그레이드를 반영했습니다.

## 배포 아티팩트

- `dist/ltcai-13.4.1-py3-none-any.whl`
- `dist/ltcai-13.4.1.tar.gz`
- `ltcai-13.4.1.tgz`
- `dist/ltcai-13.4.1.vsix`
- `src-tauri/target/release/bundle/dmg/Lattice AI_13.4.1_aarch64.dmg`
